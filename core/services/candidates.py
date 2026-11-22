from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from core.models import Asset, AssetAssignment, AssetCandidate, AuditEvent


@transaction.atomic
def approve_asset_candidate(*, candidate, reviewer):
    candidate = AssetCandidate.objects.select_for_update().select_related(
        "organization", "site", "submitted_by"
    ).get(pk=candidate.pk)
    if candidate.review_status != AssetCandidate.ReviewStatus.PENDING:
        return None
    base_client_id = slugify(candidate.name)[:80] or "asset"
    client_id = base_client_id
    suffix = 1
    while Asset.objects.filter(
        organization=candidate.organization, client_id=client_id
    ).exists():
        suffix += 1
        client_id = f"{base_client_id[:75]}-{suffix}"
    requested_code = candidate.identifier.strip()[:60]
    code = requested_code or f"NEW-{str(candidate.id)[:8].upper()}"
    if Asset.objects.filter(organization=candidate.organization, code=code).exists():
        code = f"NEW-{str(candidate.id)[:8].upper()}"
    asset = Asset.objects.create(
        organization=candidate.organization,
        site=candidate.site,
        department=candidate.department
        or candidate.site.departments.order_by("id").first(),
        client_id=client_id,
        code=code,
        name=candidate.name,
        asset_type=candidate.asset_type,
        location=candidate.location,
        serial_number=candidate.identifier,
    )
    AssetAssignment.objects.update_or_create(
        user=candidate.submitted_by,
        asset=asset,
        defaults={"interactions": [], "is_active": True},
    )
    candidate.review_status = AssetCandidate.ReviewStatus.APPROVED
    candidate.reviewed_by = reviewer
    candidate.reviewed_at = timezone.now()
    candidate.review_reason = "Approved through Energia administration"
    candidate.save(
        update_fields=[
            "review_status",
            "reviewed_by",
            "reviewed_at",
            "review_reason",
        ]
    )
    AuditEvent.objects.create(
        organization=candidate.organization,
        actor=reviewer,
        action="asset_candidate.approved",
        object_type="AssetCandidate",
        object_id=str(candidate.id),
        metadata={"asset_id": asset.client_id},
    )
    return asset


@transaction.atomic
def reject_asset_candidate(*, candidate, reviewer):
    candidate = AssetCandidate.objects.select_for_update().get(pk=candidate.pk)
    if candidate.review_status != AssetCandidate.ReviewStatus.PENDING:
        return False
    candidate.review_status = AssetCandidate.ReviewStatus.REJECTED
    candidate.reviewed_by = reviewer
    candidate.reviewed_at = timezone.now()
    candidate.review_reason = "Rejected through Energia administration"
    candidate.save(
        update_fields=[
            "review_status",
            "reviewed_by",
            "reviewed_at",
            "review_reason",
        ]
    )
    AuditEvent.objects.create(
        organization=candidate.organization,
        actor=reviewer,
        action="asset_candidate.rejected",
        object_type="AssetCandidate",
        object_id=str(candidate.id),
    )
    return True
