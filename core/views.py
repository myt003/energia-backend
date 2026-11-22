import hashlib
import json
import uuid

from django.conf import settings
from django.db import connection, transaction
from django.http import FileResponse
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, parsers, permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import (
    ActivityType,
    AIJob,
    Asset,
    AssetAssignment,
    AssetCandidate,
    AuditEvent,
    CustomActivity,
    Department,
    Evidence,
    Invoice,
    InvoiceScan,
    InvoiceScanPage,
    InvoiceSchema,
    Membership,
    Site,
    Task,
)
from core.serializers import (
    AssetAssignmentSerializer,
    AssetCandidateSerializer,
    AssetSerializer,
    EvidenceSerializer,
    InvoiceScanSerializer,
    InvoiceSchemaSerializer,
    InvoiceSerializer,
    TaskSerializer,
    TaskExplanationSerializer,
)
from core.services.duplicates import find_asset_duplicates
from core.services.evidence import EvidenceValidationError, validate_uploaded_media
from core.services.explanations import get_or_create_task_explanation
from core.services.habits import generate_energy_habits
from core.services.invoices import confirm_invoice_scan
from core.services.scope import active_membership_for, work_profile_for
from core.services.tasks import generate_daily_tasks, refresh_overdue_tasks


class HealthView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return Response({"status": "ok", "database": "postgresql"})


def _profile_payload(profile):
    membership = profile.membership
    selected_activity_codes = set(profile.activities.values_list("code", flat=True))
    activities = [
        {
            "id": activity.code,
            "title": activity.title,
            "description": activity.description,
            "selected": activity.code in selected_activity_codes,
        }
        for activity in ActivityType.objects.filter(
            organization=membership.organization, is_active=True
        )
    ]
    custom_activities = [
        {
            "id": str(activity.public_id),
            "title": activity.title,
            "description": activity.description,
            "selected": activity.selected,
        }
        for activity in profile.custom_activities.all()
    ]
    assignments = AssetAssignmentSerializer(
        AssetAssignment.objects.filter(user=profile.user, is_active=True).select_related(
            "asset__department"
        ),
        many=True,
    ).data
    pending_labels = []
    if profile.pending_department_id:
        pending_labels.append("Department change")
    if any(
        "isolate" in assignment["interactions"] for assignment in assignments
    ):
        pending_labels.append("Safety-critical interaction")
    selected_department = profile.pending_department or profile.department
    return {
        "employee": {
            "id": profile.user_id,
            "name": profile.user.get_full_name() or profile.user.username,
            "username": profile.user.username,
        },
        "organization": {
            "id": membership.organization_id,
            "name": membership.organization.name,
        },
        "site": {"id": membership.site_id, "name": membership.site.name},
        "department": {
            "id": selected_department.id,
            "name": selected_department.name,
            "approved_id": profile.department_id,
            "is_pending": bool(profile.pending_department_id),
        },
        "department_options": [
            {"id": department.id, "name": department.name}
            for department in Department.objects.filter(site=membership.site)
        ],
        "job_role": profile.job_role,
        "shift": profile.shift,
        "shift_label": profile.get_shift_display(),
        "activities": activities,
        "custom_activities": custom_activities,
        "notes": profile.notes,
        "is_complete": profile.is_complete,
        "pending_approval_labels": pending_labels,
        "assignments": assignments,
        "updated_at": profile.updated_at,
    }


class MeView(APIView):
    def get(self, request):
        membership = active_membership_for(request.user)
        return Response(
            {
                "user": {
                    "id": request.user.id,
                    "username": request.user.username,
                    "name": request.user.get_full_name() or request.user.username,
                },
                "organization": {
                    "id": membership.organization_id,
                    "name": membership.organization.name,
                },
                "site": {"id": membership.site_id, "name": membership.site.name},
                "role": membership.role,
                "is_administrator": membership.role
                in {
                    Membership.Role.ADMIN,
                    Membership.Role.ENERGY_MANAGER,
                    Membership.Role.TEAM_LEAD,
                },
            }
        )


class WorkProfileView(APIView):
    def get(self, request):
        return Response(_profile_payload(work_profile_for(request.user)))

    @transaction.atomic
    def put(self, request):
        profile = work_profile_for(request.user)
        membership = profile.membership
        data = request.data
        department_id = data.get("department_id")
        if department_id is not None and int(department_id) != profile.department_id:
            try:
                profile.pending_department = Department.objects.get(
                    id=department_id, site=membership.site
                )
            except Department.DoesNotExist:
                return Response(
                    {"department_id": ["Department is not available at this site"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        profile.job_role = str(data.get("job_role", profile.job_role)).strip()[:100]
        requested_shift = data.get("shift", profile.shift)
        if requested_shift not in profile.Shift.values:
            return Response(
                {"shift": ["Unsupported shift"]}, status=status.HTTP_400_BAD_REQUEST
            )
        profile.shift = requested_shift
        profile.notes = str(data.get("notes", profile.notes)).strip()[:240]
        profile.is_complete = bool(data.get("is_complete", profile.is_complete))
        profile.full_clean()
        profile.save()

        activity_codes = set(data.get("activity_codes", []))
        activities = ActivityType.objects.filter(
            organization=membership.organization,
            is_active=True,
            code__in=activity_codes,
        )
        if activities.count() != len(activity_codes):
            return Response(
                {"activity_codes": ["One or more activities are not available"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile.activities.set(activities)

        custom_payload = data.get("custom_activities", [])
        supplied_ids = set()
        catalog_titles = {
            title.lower()
            for title in ActivityType.objects.filter(
                organization=membership.organization, is_active=True
            ).values_list("title", flat=True)
        }
        seen_titles = set()
        for item in custom_payload:
            title = " ".join(str(item.get("title", "")).split())[:80]
            if not title:
                return Response(
                    {"custom_activities": ["Every custom activity needs a title"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            normalized = title.lower()
            if normalized in catalog_titles or normalized in seen_titles:
                return Response(
                    {"custom_activities": [f"Duplicate activity: {title}"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            seen_titles.add(normalized)
            public_id = item.get("id")
            activity = None
            if public_id:
                try:
                    parsed_public_id = uuid.UUID(str(public_id))
                except (ValueError, TypeError, AttributeError):
                    parsed_public_id = None
                if parsed_public_id:
                    activity = profile.custom_activities.filter(
                        public_id=parsed_public_id
                    ).first()
            if activity is None:
                activity = profile.custom_activities.filter(title__iexact=title).first()
            if activity is None:
                activity = CustomActivity(profile=profile)
            activity.title = title
            activity.description = " ".join(
                str(item.get("description", "")).split()
            )[:180]
            activity.selected = bool(item.get("selected", True))
            activity.full_clean()
            activity.save()
            supplied_ids.add(activity.id)
        profile.custom_activities.exclude(id__in=supplied_ids).delete()

        AuditEvent.objects.create(
            organization=membership.organization,
            actor=request.user,
            action="work_profile.updated",
            object_type="StaffWorkProfile",
            object_id=str(profile.id),
            metadata={"pending_department_id": profile.pending_department_id},
        )
        return Response(_profile_payload(work_profile_for(request.user)))


class AssetListView(generics.ListAPIView):
    serializer_class = AssetSerializer

    def get_queryset(self):
        membership = active_membership_for(self.request.user)
        queryset = Asset.objects.filter(
            organization=membership.organization,
            site=membership.site,
            status=Asset.Status.ACTIVE,
        ).select_related("department")
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(code__icontains=search)
                | Q(serial_number__icontains=search)
                | Q(asset_type__icontains=search)
                | Q(location__icontains=search)
            )
        department = self.request.query_params.get("department", "").strip()
        if department:
            queryset = queryset.filter(department__name__iexact=department)
        return queryset


class AssetQrResolveView(APIView):
    def get(self, request):
        membership = active_membership_for(request.user)
        value = request.query_params.get("value", "").strip()
        if value.startswith("energia://asset/"):
            value = value.rsplit("/", 1)[-1]
        try:
            qr_identifier = uuid.UUID(value)
        except (ValueError, TypeError, AttributeError):
            qr_identifier = None
        lookup = Q(code__iexact=value) | Q(serial_number__iexact=value)
        if qr_identifier:
            lookup |= Q(qr_identifier=qr_identifier)
        asset = (
            Asset.objects.filter(
                lookup,
                organization=membership.organization,
                site=membership.site,
                status=Asset.Status.ACTIVE,
            )
            .select_related("department")
            .first()
        )
        if asset is None:
            return Response({"detail": "Asset not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(AssetSerializer(asset).data)


class AssetDuplicateSuggestionView(APIView):
    def post(self, request):
        membership = active_membership_for(request.user)
        return Response(
            {
                "results": find_asset_duplicates(
                    organization=membership.organization,
                    name=request.data.get("name", ""),
                    asset_type=request.data.get("type", ""),
                    location=request.data.get("location", ""),
                    identifier=request.data.get("identifier", ""),
                )
            }
        )


class EquipmentAssignmentsView(APIView):
    def get(self, request):
        assignments = AssetAssignment.objects.filter(
            user=request.user, is_active=True
        ).select_related("asset__department")
        return Response(AssetAssignmentSerializer(assignments, many=True).data)

    @transaction.atomic
    def put(self, request):
        membership = active_membership_for(request.user)
        payload = request.data.get("assignments", [])
        if not isinstance(payload, list) or len(payload) > 100:
            return Response(
                {"assignments": ["Provide a list of no more than 100 assignments"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ids = [str(item.get("asset_id", "")) for item in payload]
        if len(ids) != len(set(ids)):
            return Response(
                {"assignments": ["Duplicate asset assignment"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        assets = {
            asset.client_id: asset
            for asset in Asset.objects.filter(
                organization=membership.organization,
                site=membership.site,
                status=Asset.Status.ACTIVE,
                client_id__in=ids,
            )
        }
        if set(ids) != set(assets):
            return Response(
                {"assignments": ["One or more assets are not available"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        AssetAssignment.objects.filter(user=request.user).exclude(
            asset__client_id__in=ids
        ).update(is_active=False)
        for item in payload:
            interactions = list(dict.fromkeys(item.get("interactions", [])))
            assignment, _ = AssetAssignment.objects.update_or_create(
                user=request.user,
                asset=assets[str(item["asset_id"])],
                defaults={"interactions": interactions, "is_active": True},
            )
            assignment.full_clean()
        AuditEvent.objects.create(
            organization=membership.organization,
            actor=request.user,
            action="asset_assignments.updated",
            object_type="User",
            object_id=str(request.user.id),
            metadata={"asset_ids": ids},
        )
        assignments = AssetAssignment.objects.filter(
            user=request.user, is_active=True
        ).select_related("asset__department")
        return Response(AssetAssignmentSerializer(assignments, many=True).data)


class AssetCandidateListCreateView(generics.ListCreateAPIView):
    serializer_class = AssetCandidateSerializer
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]

    def get_queryset(self):
        membership = active_membership_for(self.request.user)
        queryset = AssetCandidate.objects.filter(
            organization=membership.organization,
            site=membership.site,
        ).select_related("submitted_by")
        if membership.role not in {
            Membership.Role.ADMIN,
            Membership.Role.ENERGY_MANAGER,
            Membership.Role.TEAM_LEAD,
        }:
            queryset = queryset.filter(submitted_by=self.request.user)
        return queryset

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        profile = work_profile_for(request.user)
        data = request.data
        required = ["name", "type", "location"]
        errors = {field: ["This field is required"] for field in required if not data.get(field)}
        if not data.get("photo"):
            errors["photo"] = ["A clear equipment photo is required"]
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        duplicate_suggestions = find_asset_duplicates(
            organization=profile.membership.organization,
            name=data["name"],
            asset_type=data["type"],
            location=data["location"],
            identifier=data.get("identifier", ""),
        )
        candidate = AssetCandidate(
            organization=profile.membership.organization,
            site=profile.membership.site,
            department=profile.department,
            submitted_by=request.user,
            name=" ".join(str(data["name"]).split())[:160],
            asset_type=" ".join(str(data["type"]).split())[:100],
            location=" ".join(str(data["location"]).split())[:160],
            identifier=" ".join(str(data.get("identifier", "")).split())[:100],
            photo=data["photo"],
            duplicate_suggestions=duplicate_suggestions,
        )
        candidate.full_clean()
        candidate.save()
        AuditEvent.objects.create(
            organization=profile.membership.organization,
            actor=request.user,
            action="asset_candidate.submitted",
            object_type="AssetCandidate",
            object_id=str(candidate.id),
            metadata={"duplicate_suggestions": duplicate_suggestions},
        )
        serializer = self.get_serializer(candidate)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class TaskListView(generics.ListAPIView):
    serializer_class = TaskSerializer
    pagination_class = None

    def get_queryset(self):
        generate_daily_tasks(
            user=self.request.user,
            event_codes=settings.DEMO_EVENT_TRIGGERS,
        )
        generate_energy_habits(user=self.request.user)
        refresh_overdue_tasks(user=self.request.user)
        queryset = Task.objects.filter(assignee=self.request.user).exclude(
            status=Task.Status.CANCELLED
        ).select_related(
            "department", "asset", "rule_version"
        )
        requested_status = self.request.query_params.get("status", "").strip()
        if requested_status:
            queryset = queryset.filter(status=requested_status)
        requested_kind = self.request.query_params.get("kind", "").strip()
        if requested_kind:
            queryset = queryset.filter(task_kind=requested_kind)
        return queryset


class TaskDetailView(generics.RetrieveAPIView):
    serializer_class = TaskSerializer
    lookup_url_kwarg = "task_id"

    def get_queryset(self):
        return Task.objects.filter(assignee=self.request.user).exclude(
            status=Task.Status.CANCELLED
        ).select_related("department", "asset", "rule_version")


class TaskExplanationView(APIView):
    def get(self, request, task_id):
        task = (
            Task.objects.filter(assignee=request.user, id=task_id)
            .exclude(status=Task.Status.CANCELLED)
            .select_related("organization", "asset")
            .first()
        )
        if task is None:
            return Response({"detail": "Task not found"}, status=status.HTTP_404_NOT_FOUND)
        explanation = get_or_create_task_explanation(task=task, actor=request.user)
        return Response(TaskExplanationSerializer(explanation).data)


class TaskEvidenceListCreateView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def get_task(self, request, task_id):
        return (
            Task.objects.filter(assignee=request.user, id=task_id)
            .exclude(status=Task.Status.CANCELLED)
            .select_related("organization")
            .first()
        )

    def get(self, request, task_id):
        task = self.get_task(request, task_id)
        if task is None:
            return Response({"detail": "Task not found"}, status=status.HTTP_404_NOT_FOUND)
        evidence = Evidence.objects.filter(
            task=task, submitted_by=request.user
        ).prefetch_related("frames").select_related("verification_result")
        return Response(EvidenceSerializer(evidence, many=True).data)

    @transaction.atomic
    def post(self, request, task_id):
        task = self.get_task(request, task_id)
        if task is None:
            return Response({"detail": "Task not found"}, status=status.HTTP_404_NOT_FOUND)
        uploaded = request.FILES.get("file")
        media_type = request.data.get("media_type", "")
        idempotency_key = str(request.data.get("idempotency_key", "")).strip()
        captured_raw = request.data.get("captured_at")
        errors = {}
        if uploaded is None:
            errors["file"] = ["Evidence file is required"]
        if media_type not in Evidence.MediaType.values:
            errors["media_type"] = ["Use photo or video"]
        if not idempotency_key or len(idempotency_key) > 100:
            errors["idempotency_key"] = ["A bounded idempotency key is required"]
        try:
            captured_at = serializers.DateTimeField().run_validation(captured_raw)
        except serializers.ValidationError:
            captured_at = None
            errors["captured_at"] = ["A valid ISO-8601 capture time is required"]
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        existing = Evidence.objects.filter(
            submitted_by=request.user, idempotency_key=idempotency_key
        ).first()
        if existing:
            return Response(EvidenceSerializer(existing).data)
        try:
            mime_type = validate_uploaded_media(uploaded, media_type)
        except EvidenceValidationError as exc:
            return Response(
                {"file": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST
            )
        digest = hashlib.sha256()
        for chunk in uploaded.chunks():
            digest.update(chunk)
        uploaded.seek(0)
        evidence = Evidence.objects.create(
            task=task,
            submitted_by=request.user,
            media_type=media_type,
            file=uploaded,
            original_filename=uploaded.name[:240],
            mime_type=mime_type,
            size_bytes=uploaded.size,
            checksum_sha256=digest.hexdigest(),
            idempotency_key=idempotency_key,
            captured_at=captured_at,
        )
        AIJob.objects.create(evidence=evidence)
        AuditEvent.objects.create(
            organization=task.organization,
            actor=request.user,
            action="evidence.submitted",
            object_type="Evidence",
            object_id=str(evidence.id),
            metadata={
                "task_id": str(task.id),
                "media_type": media_type,
                "checksum_sha256": evidence.checksum_sha256,
            },
        )
        return Response(
            EvidenceSerializer(evidence).data,
            status=status.HTTP_201_CREATED,
        )


class EvidenceDetailView(generics.RetrieveAPIView):
    serializer_class = EvidenceSerializer
    lookup_url_kwarg = "evidence_id"

    def get_queryset(self):
        return Evidence.objects.filter(
            submitted_by=self.request.user
        ).prefetch_related("frames").select_related("verification_result")


class EvidenceFileView(APIView):
    def get(self, request, evidence_id):
        evidence = Evidence.objects.filter(
            id=evidence_id, submitted_by=request.user
        ).first()
        if evidence is None:
            return Response(
                {"detail": "Evidence not found"}, status=status.HTTP_404_NOT_FOUND
            )
        return FileResponse(
            evidence.file.open("rb"),
            content_type=evidence.mime_type,
            as_attachment=False,
            filename=evidence.original_filename,
        )


def _admin_membership(user):
    membership = active_membership_for(user)
    if membership.role not in {
        Membership.Role.ADMIN,
        Membership.Role.ENERGY_MANAGER,
        Membership.Role.TEAM_LEAD,
    }:
        return None
    return membership


class InvoiceSchemaListView(generics.ListAPIView):
    serializer_class = InvoiceSchemaSerializer
    pagination_class = None

    def get_queryset(self):
        membership = _admin_membership(self.request.user)
        if membership is None:
            return InvoiceSchema.objects.none()
        return InvoiceSchema.objects.filter(
            organization=membership.organization, is_active=True
        )

    def list(self, request, *args, **kwargs):
        if _admin_membership(request.user) is None:
            return Response(
                {"detail": "Administrator role required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().list(request, *args, **kwargs)


class InvoiceScanListCreateView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def get(self, request):
        membership = _admin_membership(request.user)
        if membership is None:
            return Response(
                {"detail": "Administrator role required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        scans = InvoiceScan.objects.filter(
            organization=membership.organization
        ).select_related("site", "schema", "invoice").prefetch_related("pages")[:50]
        return Response(InvoiceScanSerializer(scans, many=True).data)

    @transaction.atomic
    def post(self, request):
        membership = _admin_membership(request.user)
        if membership is None:
            return Response(
                {"detail": "Administrator role required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        files = request.FILES.getlist("pages") or request.FILES.getlist("page")
        if not 1 <= len(files) <= 5:
            return Response(
                {"pages": ["Upload between 1 and 5 invoice pages"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            site = Site.objects.get(
                id=request.data.get("site_id"),
                organization=membership.organization,
            )
            schema = InvoiceSchema.objects.get(
                id=request.data.get("schema_id"),
                organization=membership.organization,
                is_active=True,
            )
        except (Site.DoesNotExist, InvoiceSchema.DoesNotExist, ValueError, TypeError):
            return Response(
                {"detail": "Valid site and invoice schema are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        supplier = " ".join(str(request.data.get("supplier", "")).split())[:160]
        if not supplier:
            return Response(
                {"supplier": ["Supplier is required"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rotations_raw = request.data.get("rotations", "[]")
        try:
            rotations = json.loads(rotations_raw) if isinstance(rotations_raw, str) else []
        except json.JSONDecodeError:
            rotations = []
        scan = InvoiceScan.objects.create(
            organization=membership.organization,
            site=site,
            schema=schema,
            supplier=supplier,
            submitted_by=request.user,
        )
        for index, uploaded in enumerate(files):
            if uploaded.size > 12 * 1024 * 1024:
                transaction.set_rollback(True)
                return Response(
                    {"pages": [f"Page {index + 1} exceeds 12 MB"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            mime_type = (uploaded.content_type or "").lower()
            if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
                transaction.set_rollback(True)
                return Response(
                    {"pages": [f"Page {index + 1} has an unsupported format"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            digest = hashlib.sha256()
            for chunk in uploaded.chunks():
                digest.update(chunk)
            uploaded.seek(0)
            try:
                rotation = int(rotations[index]) if index < len(rotations) else 0
            except (TypeError, ValueError):
                rotation = 0
            if rotation not in {0, 90, 180, 270}:
                rotation = 0
            InvoiceScanPage.objects.create(
                scan=scan,
                page_index=index,
                image=uploaded,
                original_filename=uploaded.name[:240],
                mime_type=mime_type,
                checksum_sha256=digest.hexdigest(),
                rotation_degrees=rotation,
            )
        AIJob.objects.create(
            invoice_scan=scan,
            purpose="invoice_extraction",
        )
        AuditEvent.objects.create(
            organization=membership.organization,
            actor=request.user,
            action="invoice_scan.submitted",
            object_type="InvoiceScan",
            object_id=str(scan.id),
            metadata={"site_id": site.id, "page_count": len(files)},
        )
        return Response(
            InvoiceScanSerializer(scan).data,
            status=status.HTTP_201_CREATED,
        )


class InvoiceScanDetailView(generics.RetrieveAPIView):
    serializer_class = InvoiceScanSerializer
    lookup_url_kwarg = "scan_id"

    def get_queryset(self):
        membership = _admin_membership(self.request.user)
        if membership is None:
            return InvoiceScan.objects.none()
        return InvoiceScan.objects.filter(
            organization=membership.organization
        ).select_related("site", "schema", "invoice").prefetch_related("pages")


class InvoiceScanConfirmView(APIView):
    @transaction.atomic
    def post(self, request, scan_id):
        membership = _admin_membership(request.user)
        if membership is None:
            return Response(
                {"detail": "Administrator role required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        scan = InvoiceScan.objects.filter(
            id=scan_id, organization=membership.organization
        ).select_related("site", "schema").first()
        if scan is None:
            return Response(
                {"detail": "Invoice scan not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        corrected_fields = request.data.get("corrected_fields", {})
        if not isinstance(corrected_fields, dict):
            return Response(
                {"corrected_fields": ["Expected an object"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            invoice = confirm_invoice_scan(
                scan=scan,
                actor=request.user,
                corrected_fields=corrected_fields,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        AuditEvent.objects.create(
            organization=membership.organization,
            actor=request.user,
            action="invoice_scan.confirmed",
            object_type="Invoice",
            object_id=str(invoice.id),
            metadata={"scan_id": str(scan.id)},
        )
        return Response(InvoiceSerializer(invoice).data)


class InvoiceListView(generics.ListAPIView):
    serializer_class = InvoiceSerializer

    def get_queryset(self):
        membership = _admin_membership(self.request.user)
        if membership is None:
            return Invoice.objects.none()
        return Invoice.objects.filter(
            organization=membership.organization
        ).select_related("site", "schema")
