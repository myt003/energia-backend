from datetime import datetime, time

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import (
    AssetAssignment,
    AuditEvent,
    RecommendationTemplate,
    RuleVersion,
    SafetyConstraint,
    Task,
)
from core.services.scope import work_profile_for


def _due_at(template, day):
    naive = datetime.combine(day, time(template.due_hour, template.due_minute))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _conflicting_constraints(*, template, profile, asset, constraints):
    tags = set(template.recommendation_tags)
    conflicts = []
    for constraint in constraints:
        if constraint.target_department_id and (
            constraint.target_department_id != profile.department_id
        ):
            continue
        if constraint.target_asset_types and (
            asset is None or asset.asset_type not in constraint.target_asset_types
        ):
            continue
        if tags.intersection(constraint.blocked_recommendation_tags):
            conflicts.append(constraint)
    return conflicts


@transaction.atomic
def generate_energy_habits(*, user, now=None):
    now = now or timezone.now()
    day = timezone.localdate(now)
    profile = work_profile_for(user)
    membership = profile.membership
    activity_codes = set(profile.activities.values_list("code", flat=True))
    assignments = list(
        AssetAssignment.objects.filter(
            user=user,
            is_active=True,
            asset__organization=membership.organization,
            asset__status="active",
        ).select_related("asset__department")
    )
    templates = RecommendationTemplate.objects.filter(
        organization=membership.organization,
        is_approved=True,
    ).select_related("target_department")
    constraints = list(
        SafetyConstraint.objects.filter(
            organization=membership.organization,
            is_active=True,
            is_locked=True,
        ).select_related("target_department")
    )
    created_tasks = []
    expected_keys = set()
    excluded = []
    for template in templates:
        if template.code.startswith("DEMO-") and not settings.ENABLE_DEMO_TASKS:
            continue
        if template.target_department_id and (
            template.target_department_id != profile.department_id
        ):
            continue
        if template.required_activity_codes and not activity_codes.intersection(
            template.required_activity_codes
        ):
            continue
        if template.target_asset_types:
            matching_assignments = [
                assignment
                for assignment in assignments
                if assignment.asset.asset_type in template.target_asset_types
                and (
                    not template.required_interactions
                    or set(assignment.interactions).intersection(
                        template.required_interactions
                    )
                )
            ]
            if not matching_assignments:
                continue
        else:
            matching_assignments = [None]

        for assignment in matching_assignments:
            asset = assignment.asset if assignment else None
            conflicts = _conflicting_constraints(
                template=template,
                profile=profile,
                asset=asset,
                constraints=constraints,
            )
            if conflicts:
                excluded.append(
                    {
                        "template": template.code,
                        "asset": asset.client_id if asset else None,
                        "constraints": [constraint.code for constraint in conflicts],
                    }
                )
                continue
            unique_key = ":".join(
                [
                    str(membership.organization_id),
                    str(user.id),
                    day.isoformat(),
                    "habit",
                    template.code,
                    str(template.version),
                    asset.client_id if asset else "department",
                ]
            )
            expected_keys.add(unique_key)
            reasons = [
                "Approved energy habit template",
                f"{profile.department.name} department",
            ]
            if asset:
                reasons.insert(0, f"{asset.name} assignment")
            if template.required_activity_codes:
                matched = activity_codes.intersection(template.required_activity_codes)
                reasons.append(f"{sorted(matched)[0].replace('_', ' ').title()} activity")
            if template.required_interactions and assignment:
                matched_interactions = set(assignment.interactions).intersection(
                    template.required_interactions
                )
                reasons.append(
                    f"{sorted(matched_interactions)[0].replace('_', ' ').title()} interaction"
                )
            urgency = {
                RecommendationTemplate.RiskLevel.LOW: RuleVersion.Urgency.ROUTINE,
                RecommendationTemplate.RiskLevel.MEDIUM: RuleVersion.Urgency.IMPORTANT,
                RecommendationTemplate.RiskLevel.HIGH: RuleVersion.Urgency.URGENT,
            }[template.risk_level]
            task, created = Task.objects.get_or_create(
                unique_key=unique_key,
                defaults={
                    "organization": membership.organization,
                    "site": membership.site,
                    "department": asset.department if asset else profile.department,
                    "assignee": user,
                    "asset": asset,
                    "recommendation_template": template,
                    "task_kind": Task.Kind.ENERGY_HABIT,
                    "rule_code_snapshot": template.code,
                    "rule_version_snapshot": template.version,
                    "title": template.title_template.format(
                        department=profile.department.name,
                        asset=asset.name if asset else "",
                    ).strip(),
                    "instruction": template.instruction,
                    "evidence_requirement": template.evidence_requirement,
                    "pass_condition": template.pass_condition,
                    "urgency": urgency,
                    "source_title": template.source_title,
                    "source_version": template.source_version,
                    "source_section": template.source_section,
                    "recommendation_owner": template.owner_role,
                    "why_assigned": reasons,
                    "due_at": _due_at(template, day),
                },
            )
            if created:
                created_tasks.append(task)
            elif task.status == Task.Status.CANCELLED:
                task.status = Task.Status.DUE
                task.save(update_fields=["status"])

    cancelled = (
        Task.objects.filter(
            assignee=user,
            task_kind=Task.Kind.ENERGY_HABIT,
            status=Task.Status.DUE,
            generated_at__date=day,
            recommendation_template__isnull=False,
        )
        .exclude(unique_key__in=expected_keys)
        .update(status=Task.Status.CANCELLED)
    )
    audit_object_id = f"{day.isoformat()}:{user.id}"
    metadata = {
        "task_ids": [str(task.id) for task in created_tasks],
        "excluded_by_safety": excluded,
        "cancelled_count": cancelled,
    }
    previous = AuditEvent.objects.filter(
        organization=membership.organization,
        action="energy_habits.generated",
        object_type="TaskBatch",
        object_id=audit_object_id,
    ).first()
    exclusion_changed = (
        previous is None
        or previous.metadata.get("excluded_by_safety") != excluded
    )
    if created_tasks or cancelled or exclusion_changed:
        AuditEvent.objects.create(
            organization=membership.organization,
            actor=user,
            action="energy_habits.generated",
            object_type="TaskBatch",
            object_id=audit_object_id,
            metadata=metadata,
        )
    return {"created": created_tasks, "excluded": excluded, "cancelled": cancelled}
