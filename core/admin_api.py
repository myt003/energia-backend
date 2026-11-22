"""Synchronized administrator APIs for the complete Energia workspace."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import date, timedelta

from django.db import models as django_models
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import (
    Alert,
    Asset,
    AssetCandidate,
    AuditEvent,
    AuditFinding,
    AuditPlan,
    Baseline,
    CorrectiveAction,
    CustomRule,
    DashboardView,
    Department,
    DisputeRequest,
    EmissionFactor,
    EmissionRecord,
    EnergyReview,
    EnergyUse,
    EnPI,
    ESPDevice,
    Evidence,
    FieldScan,
    FormCorrection,
    ImportBatch,
    Invoice,
    ImportRow,
    KnowledgeChunk,
    KnowledgeDocument,
    MaintenancePlan,
    MaturityAssessment,
    Meter,
    MeterSchema,
    Notification,
    Reading,
    Recommendation,
    ScoreSnapshot,
    Site,
    StaffFormSubmission,
    Task,
    TaskAction,
    WeatherForecast,
    WorkOrder,
)
from core.services.intelligence import (
    audit,
    build_report,
    calculate_scores,
    chart_series,
    confirm_field_scan,
    confirm_import,
    create_reading,
    device_summary,
    ingest_knowledge_document,
    maintenance_due,
    preview_rule,
    publish_rule,
    rag_answer,
    refresh_weather,
    stage_import,
)
from core.services.report_export import build_csv_report, build_pdf_report
from core.services.scope import active_membership_for

ADMIN_ROLES = {
    "admin",
    "energy_manager",
    "team_lead",
    "maintenance",
    "auditor",
}


def admin_membership(request):
    membership = active_membership_for(request.user)
    if membership.role not in ADMIN_ROLES:
        raise PermissionDenied("Administrator access is required")
    return membership


def _dynamic_serializer(model, *, exclude=()):
    _model = model

    class _Serializer(serializers.ModelSerializer):
        class Meta:
            model = _model
            fields = "__all__"
            read_only_fields = tuple(
                field.name
                for field in model._meta.fields
                if field.name in {"created_at", "updated_at", "confirmed_at", "published_at"}
            )

    return _Serializer


class AdminAssetSerializer(serializers.ModelSerializer):
    """Asset view used by administrator inventory management.

    ``qr_value`` is the exact payload the staff QR scanner resolves through
    ``/api/v1/assets/resolve-qr/`` so a label generated here always matches the
    mobile workflow.
    """

    id = serializers.CharField(source="client_id", read_only=True)
    pk = serializers.UUIDField(source="id", read_only=True)
    asset_code = serializers.CharField(source="code")
    type = serializers.CharField(source="asset_type")
    department_name = serializers.CharField(source="department.name", read_only=True)
    qr_value = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id",
            "pk",
            "organization",
            "client_id",
            "asset_code",
            "name",
            "type",
            "department",
            "department_name",
            "site",
            "location",
            "serial_number",
            "status",
            "qr_identifier",
            "qr_value",
        ]
        read_only_fields = ["id", "pk", "department_name", "qr_identifier", "qr_value"]

    def get_qr_value(self, obj):
        return f"energia://asset/{obj.qr_identifier}"

    def validate(self, attrs):
        site = attrs.get("site") or getattr(self.instance, "site", None)
        department = attrs.get("department") or getattr(self.instance, "department", None)
        if site and department and department.site_id != site.id:
            raise ValidationError({"department": ["Department must belong to the asset site"]})
        return attrs


class ESPDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ESPDevice
        fields = [
            "id",
            "organization",
            "site",
            "department",
            "name",
            "refresh_seconds",
            "allow_top_employee",
            "is_active",
            "last_seen_at",
            "created_at",
        ]
        read_only_fields = ["id", "last_seen_at", "created_at"]


RESOURCES = {
    "assets": (Asset, None, None),
    "sites": (Site, None, None),
    "departments": (Department, None, "site__organization"),
    "asset-candidates": (AssetCandidate, None, None),
    "invoices": (Invoice, None, None),
    "knowledge-documents": (KnowledgeDocument, None, None),
    "knowledge-chunks": (KnowledgeChunk, "document", "document__organization"),
    "custom-rules": (CustomRule, None, None),
    "meter-schemas": (MeterSchema, None, None),
    "meters": (Meter, None, None),
    "readings": (Reading, None, None),
    "import-batches": (ImportBatch, None, None),
    "import-rows": (ImportRow, "batch", "batch__organization"),
    "dashboard-views": (DashboardView, None, None),
    "baselines": (Baseline, None, None),
    "enpis": (EnPI, None, None),
    "score-snapshots": (ScoreSnapshot, None, None),
    "alerts": (Alert, None, None),
    "recommendations": (Recommendation, None, None),
    "maintenance-plans": (MaintenancePlan, None, None),
    "work-orders": (WorkOrder, None, None),
    "weather-forecasts": (WeatherForecast, None, None),
    "energy-reviews": (EnergyReview, None, None),
    "energy-uses": (EnergyUse, "review", "review__organization"),
    "emission-factors": (EmissionFactor, None, None),
    "emission-records": (EmissionRecord, None, None),
    "maturity-assessments": (MaturityAssessment, None, None),
    "audit-plans": (AuditPlan, None, None),
    "audit-findings": (AuditFinding, "audit", "audit__organization"),
    "corrective-actions": (CorrectiveAction, "finding", "finding__audit__organization"),
    "field-scans": (FieldScan, None, None),
    "form-submissions": (StaffFormSubmission, "task", "task__organization"),
    "task-actions": (TaskAction, "task", "task__organization"),
    "dispute-requests": (DisputeRequest, None, None),
    "esp-devices": (ESPDevice, None, None),
    "notifications": (Notification, None, None),
    "tasks": (Task, None, None),
    "audit-events": (AuditEvent, None, None),
}

_SERIALIZER_CACHE = {}


def _serializer_for(model):
    if model not in _SERIALIZER_CACHE:
        if model is Asset:
            _SERIALIZER_CACHE[model] = AdminAssetSerializer
        elif model is ESPDevice:
            _SERIALIZER_CACHE[model] = ESPDeviceSerializer
        else:
            _SERIALIZER_CACHE[model] = _dynamic_serializer(model)
    return _SERIALIZER_CACHE[model]


def _filtered_queryset(resource, request):
    model, parent_field, scope_path = RESOURCES[resource]
    membership = admin_membership(request)
    queryset = model.objects.all()
    if scope_path:
        queryset = queryset.filter(**{scope_path: membership.organization_id})
    elif hasattr(model, "organization"):
        queryset = queryset.filter(organization=membership.organization_id)

    if parent_field:
        parent_value = request.query_params.get(parent_field) or request.query_params.get(
            f"{parent_field}_id"
        )
        if parent_value:
            queryset = queryset.filter(**{f"{parent_field}_id": parent_value})

    simple_filters = {
        "site": "site_id",
        "site_id": "site_id",
        "department": "department_id",
        "department_id": "department_id",
        "status": "status",
        "kind": "kind",
        "category": "category",
        "user": "user_id",
        "user_id": "user_id",
        "assignee": "assignee_id",
        "meter": "meter_id",
        "meter_id": "meter_id",
        "task": "task_id",
        "task_id": "task_id",
        "asset": "asset_id",
        "asset_id": "asset_id",
    }
    for key, field in simple_filters.items():
        value = request.query_params.get(key)
        if value and field in {item.name for item in model._meta.fields}:
            queryset = queryset.filter(**{field: value})

    search = request.query_params.get("search")
    if search:
        searchable = [
            field.name
            for field in model._meta.fields
            if isinstance(field, (django_models.CharField, django_models.TextField))
        ]
        if searchable:
            query = Q()
            for field in searchable[:6]:
                query |= Q(**{f"{field}__icontains": search})
            queryset = queryset.filter(query)
    return queryset.select_related() if hasattr(queryset, "select_related") else queryset


def _paginate(request, queryset, serializer_class):
    page = max(1, int(request.query_params.get("page", 1) or 1))
    page_size = min(200, max(1, int(request.query_params.get("page_size", 50) or 50)))
    total = queryset.count()
    start = (page - 1) * page_size
    items = queryset[start : start + page_size]
    return {
        "count": total,
        "page": page,
        "page_size": page_size,
        "next": page + 1 if start + page_size < total else None,
        "previous": page - 1 if page > 1 else None,
        "results": serializer_class(items, many=True, context={"request": request}).data,
    }


def _validate_parent(resource, data, organization):
    model, parent_field, _ = RESOURCES[resource]
    if not parent_field:
        return
    parent_model = model._meta.get_field(parent_field).related_model
    parent_id = data.get(f"{parent_field}_id") or data.get(parent_field)
    if not parent_id:
        raise ValidationError({parent_field: ["This related record is required"]})
    parent = parent_model.objects.filter(id=parent_id).first()
    if not parent:
        raise ValidationError({parent_field: ["Related record was not found"]})
    parent_org = getattr(parent, "organization_id", None)
    if parent_org is None and hasattr(parent, "task_id"):
        parent_org = parent.task.organization_id
    if parent_org is None and hasattr(parent, "audit_id"):
        parent_org = parent.audit.organization_id
    if parent_org != organization.id:
        raise ValidationError({parent_field: ["Related record is outside your organization"]})


class AdminResourceListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request, resource):
        if resource not in RESOURCES:
            raise NotFound("Unknown admin resource")
        serializer = _serializer_for(RESOURCES[resource][0])
        return Response(_paginate(request, _filtered_queryset(resource, request), serializer))

    def post(self, request, resource):
        if resource not in RESOURCES:
            raise NotFound("Unknown admin resource")
        membership = admin_membership(request)
        model = RESOURCES[resource][0]
        data = dict(request.data.items())
        for key, value in list(data.items()):
            if isinstance(value, list) and key not in {"interactions", "assignments"}:
                data[key] = value[0] if value else ""
        _validate_parent(resource, data, membership.organization)
        for field in ("organization", "organization_id", "id"):
            data.pop(field, None)
        if hasattr(model, "organization"):
            data["organization"] = membership.organization_id
        for field in ("created_by", "submitted_by"):
            if field in {item.name for item in model._meta.fields} and field not in data:
                data[field] = request.user.id
        serializer = _serializer_for(model)(data=data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        audit(
            membership.organization,
            request.user,
            f"admin.{resource}.created",
            model.__name__.lower(),
            instance.pk,
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AdminResourceDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def _object(self, request, resource, pk):
        if resource not in RESOURCES:
            raise NotFound("Unknown admin resource")
        return _filtered_queryset(resource, request).filter(pk=pk).first()

    def get(self, request, resource, pk):
        instance = self._object(request, resource, pk)
        if not instance:
            return Response({"detail": "Not found"}, status=404)
        serializer = _serializer_for(type(instance))(instance, context={"request": request})
        return Response(serializer.data)

    def patch(self, request, resource, pk):
        instance = self._object(request, resource, pk)
        if not instance:
            return Response({"detail": "Not found"}, status=404)
        data = dict(request.data.items())
        for key in ("organization", "organization_id", "id", "created_by", "submitted_by"):
            data.pop(key, None)
        serializer = _serializer_for(type(instance))(
            instance, data=data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        membership = admin_membership(request)
        audit(
            membership.organization,
            request.user,
            f"admin.{resource}.updated",
            type(instance).__name__.lower(),
            instance.pk,
        )
        return Response(serializer.data)

    def delete(self, request, resource, pk):
        instance = self._object(request, resource, pk)
        if not instance:
            return Response({"detail": "Not found"}, status=404)
        membership = admin_membership(request)
        audit(
            membership.organization,
            request.user,
            f"admin.{resource}.deleted",
            type(instance).__name__.lower(),
            instance.pk,
        )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class KnowledgeIngestView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        membership = admin_membership(request)
        upload = request.FILES.get("file")
        if not upload:
            raise ValidationError({"file": ["A document file is required"]})
        checksum = hashlib.sha256(upload.read()).hexdigest()
        upload.seek(0)
        document = KnowledgeDocument.objects.create(
            organization=membership.organization,
            site_id=request.data.get("site_id") or None,
            title=request.data.get("title") or upload.name,
            source=request.data.get("source", ""),
            version=request.data.get("version", "1.0"),
            effective_date=request.data.get("effective_date") or None,
            jurisdiction=request.data.get("jurisdiction", ""),
            applicable_sites=request.data.getlist("applicable_sites") or [],
            file=upload,
            mime_type=upload.content_type,
            checksum_sha256=checksum,
            created_by=request.user,
        )
        ingest_knowledge_document(document)
        audit(
            membership.organization,
            request.user,
            "knowledge.ingested",
            "knowledgedocument",
            document.id,
            {"chunks": document.chunk_count, "status": document.status},
        )
        from core.serializers import KnowledgeDocumentAdminSerializer

        return Response(
            KnowledgeDocumentAdminSerializer(document).data,
            status=status.HTTP_201_CREATED,
        )


class RagQueryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        membership = admin_membership(request)
        query = str(request.data.get("query", "")).strip()
        if not query:
            raise ValidationError({"query": ["A question is required"]})
        result = rag_answer(
            membership.organization,
            request.user,
            query,
            site_id=request.data.get("site_id"),
            use_model=request.data.get("use_model", True) is not False,
        )
        return Response(result)


class RulePreviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        membership = admin_membership(request)
        rule_id = request.data.get("rule_id")
        if rule_id:
            rule = CustomRule.objects.filter(
                id=rule_id, organization=membership.organization
            ).first()
            if not rule:
                return Response({"detail": "Not found"}, status=404)
            definition = rule.definition or {}
        else:
            definition = dict(request.data.get("definition") or request.data)
        return Response(preview_rule(membership.organization, definition))


class RulePublishView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        membership = admin_membership(request)
        rule = CustomRule.objects.filter(id=pk, organization=membership.organization).first()
        if not rule:
            return Response({"detail": "Not found"}, status=404)
        if rule.status == CustomRule.Status.PUBLISHED:
            raise ValidationError("This rule version is already published")
        version = publish_rule(rule, request.user)
        return Response({"rule_id": str(rule.id), "rule_version_id": version.id})


class AdminTaskBulkView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        membership = admin_membership(request)
        task_ids = request.data.get("task_ids") or []
        action = request.data.get("action")
        reason = request.data.get("reason", "")
        if not isinstance(task_ids, list) or not task_ids or not reason:
            raise ValidationError("task_ids and reason are required")
        if action not in {choice[0] for choice in TaskAction.Action.choices}:
            raise ValidationError("Unsupported bulk action")
        tasks = Task.objects.filter(id__in=task_ids, organization=membership.organization)
        for task in tasks:
            TaskAction.objects.create(
                task=task,
                actor=request.user,
                action=action,
                reason=reason,
                metadata=request.data.get("metadata") or {},
            )
            if action == TaskAction.Action.REASSIGN:
                task.assignee_id = request.data.get("assignee_id") or task.assignee_id
            elif action == TaskAction.Action.REQUEST_ANOTHER:
                task.status = Task.Status.WAITING_REVIEW
            elif action == TaskAction.Action.EXCEPTION:
                task.status = Task.Status.COMPLETED
                task.completed_at = timezone.now()
            elif action == TaskAction.Action.ESCALATE:
                task.status = Task.Status.WAITING_REVIEW
            task.save()
            audit(membership.organization, request.user, "task.bulk_action", "task", task.id, {"action": action})
        return Response({"updated": tasks.count(), "action": action})


class TaskCompleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        membership = admin_membership(request)
        task = Task.objects.filter(id=pk, organization=membership.organization).first()
        if not task:
            return Response({"detail": "Not found"}, status=404)
        submission = StaffFormSubmission.objects.create(
            task=task,
            user=request.user,
            answers=request.data.get("answers") or {},
            readings=request.data.get("readings") or [],
            comment=request.data.get("comment", ""),
            issue_reason=request.data.get("issue_reason", ""),
            out_of_range_flags=request.data.get("out_of_range_flags") or [],
            idempotency_key=request.data.get("idempotency_key") or str(uuid.uuid4()),
        )
        target_status = request.data.get("status", Task.Status.COMPLETED)
        if target_status not in {Task.Status.COMPLETED, Task.Status.WAITING_REVIEW}:
            raise ValidationError("Unsupported task status")
        task.status = target_status
        if target_status == Task.Status.COMPLETED:
            task.completed_at = timezone.now()
        task.submitted_at = timezone.now()
        task.save(update_fields=["status", "completed_at", "submitted_at"])
        submission.status = (
            StaffFormSubmission.Status.VERIFIED
            if target_status == Task.Status.COMPLETED
            else StaffFormSubmission.Status.REVIEW
        )
        submission.save(update_fields=["status"])
        audit(membership.organization, request.user, "task.completed", "task", task.id, {"status": target_status})
        return Response({"task_id": str(task.id), "submission_id": submission.id, "status": task.status})


class DepartmentHealthView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        membership = admin_membership(request)
        snapshots = calculate_scores(
            membership.organization, request.query_params.get("period", "month"), request.user
        )
        from core.serializers import ScoreSnapshotAdminSerializer

        return Response(
            ScoreSnapshotAdminSerializer(snapshots, many=True, context={"request": request}).data
        )


class ChartSeriesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        membership = admin_membership(request)
        try:
            days = int(request.query_params.get("days", 90))
        except ValueError:
            days = 90
        return Response(
            chart_series(
                membership.organization,
                site_id=request.query_params.get("site_id"),
                department_id=request.query_params.get("department_id"),
                meter_id=request.query_params.get("meter_id"),
                days=days,
                group=request.query_params.get("group", "day"),
                metric=request.query_params.get("metric", "energy"),
            )
        )


class EnergyFlowView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        membership = admin_membership(request)
        try:
            days = int(request.query_params.get("days", 90))
        except ValueError:
            days = 90
        end = timezone.localdate()
        start = end - timedelta(days=days)
        readings = Reading.objects.filter(
            organization=membership.organization,
            period_start__gte=start,
            period_end__lte=end,
        ).select_related("site", "department", "meter")
        totals = defaultdict(float)
        sites = {}
        departments = {}
        meters = {}
        for reading in readings:
            value = float(reading.canonical_value)
            sites[reading.site_id] = reading.site.name
            key = ("site", reading.site_id)
            totals[key] += value
            if reading.department_id:
                departments[reading.department_id] = (
                    reading.department.name,
                    reading.site_id,
                )
                key = ("department", reading.department_id)
                totals[key] += value
            if reading.meter_id:
                meters[reading.meter_id] = (
                    reading.meter.name,
                    reading.department_id or reading.site_id,
                )
                key = ("meter", reading.meter_id)
                totals[key] += value
        nodes = []
        for node_id, name in sites.items():
            nodes.append({"id": f"site-{node_id}", "name": name, "type": "site"})
        for node_id, (name, parent) in departments.items():
            nodes.append({"id": f"department-{node_id}", "name": name, "type": "department"})
        for node_id, (name, parent) in meters.items():
            nodes.append({"id": f"meter-{node_id}", "name": name, "type": "meter"})
        links = []
        for department_id, (_, site_id) in departments.items():
            value = totals.get(("department", department_id), 0)
            if value:
                links.append(
                    {
                        "source": f"site-{site_id}",
                        "target": f"department-{department_id}",
                        "value": round(value, 3),
                    }
                )
        for meter_id, (_, parent) in meters.items():
            value = totals.get(("meter", meter_id), 0)
            if value:
                parent_type = "department" if any(
                    dept_id == parent for dept_id in departments
                ) else "site"
                links.append(
                    {
                        "source": f"{parent_type}-{parent}",
                        "target": f"meter-{meter_id}",
                        "value": round(value, 3),
                    }
                )
        return Response(
            {
                "nodes": nodes,
                "links": links,
                "unit": readings.first().canonical_unit if readings.exists() else "kWh",
                "sample_count": readings.count(),
            }
        )


class PeakCheckView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        membership = admin_membership(request)
        payload = dict(request.data)
        created = create_reading(
            membership.organization, request.user, payload, source=request.data.get("source", "scan")
        )
        previous = (
            Reading.objects.filter(
                organization=membership.organization,
                meter=created.meter,
                period_end__lt=created.period_end,
            )
            .order_by("-period_end")
            .first()
            if created.meter_id
            else None
        )
        current = float(created.canonical_value)
        previous_value = float(previous.canonical_value) if previous else None
        change = ((current - previous_value) / previous_value * 100) if previous_value else None
        peak = change is not None and change >= 25
        return Response(
            {
                "reading_id": created.id,
                "current": current,
                "previous": previous_value,
                "change_percent": round(change, 1) if change is not None else None,
                "peak": peak,
                "quality_flags": created.quality_flags,
                "recommendation": (
                    "Compare operating hours and production output, then inspect the linked "
                    "meter and equipment against the approved baseline before taking action."
                    if peak
                    else "No threshold exceeded. Continue normal monitoring."
                ),
            },
            status=status.HTTP_201_CREATED,
        )


class ReportGenerateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return self._generate(request)

    def post(self, request):
        return self._generate(request)

    def _generate(self, request):
        membership = admin_membership(request)
        end = request.query_params.get("to") or request.data.get("to") or timezone.localdate().isoformat()
        start = (
            request.query_params.get("from")
            or request.data.get("from")
            or (timezone.localdate() - timedelta(days=30)).isoformat()
        )
        report = build_report(
            membership.organization,
            date.fromisoformat(start),
            date.fromisoformat(end),
        )
        export = request.query_params.get("export") or request.data.get("export", "json")
        audit(
            membership.organization,
            request.user,
            "report.generated",
            "report",
            f"{start}:{end}",
            {"format": export},
        )
        if export == "csv":
            response = HttpResponse(build_csv_report(report), content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = (
                f'attachment; filename="energia-report-{start}-{end}.csv"'
            )
            return response
        if export == "pdf":
            response = HttpResponse(
                build_pdf_report(
                    report,
                    membership.organization.name,
                    start,
                    end,
                ),
                content_type="application/pdf",
            )
            response["Content-Disposition"] = (
                f'attachment; filename="energia-report-{start}-{end}.pdf"'
            )
            return response
        return Response(report)


class ImportUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        membership = admin_membership(request)
        upload = request.FILES.get("file")
        if not upload:
            raise ValidationError({"file": ["An XLSX file is required"]})
        batch = ImportBatch.objects.create(
            organization=membership.organization,
            site_id=request.data.get("site_id"),
            kind=request.data.get("kind", "readings"),
            file=upload,
            column_mapping=json.loads(request.data.get("column_mapping") or "{}"),
            created_by=request.user,
        )
        stage_import(membership.organization, request.user, batch)
        return Response(
            {
                "id": str(batch.id),
                "status": batch.status,
                "summary": batch.summary,
            },
            status=status.HTTP_201_CREATED,
        )


class ImportConfirmView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        membership = admin_membership(request)
        batch = ImportBatch.objects.filter(id=pk, organization=membership.organization).first()
        if not batch:
            return Response({"detail": "Not found"}, status=404)
        if batch.status == ImportBatch.Status.CONFIRMED:
            raise ValidationError("This import has already been confirmed")
        confirm_import(batch, request.user)
        return Response({"id": str(batch.id), "status": batch.status, "summary": batch.summary})


class WeatherRefreshView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        membership = admin_membership(request)
        created = refresh_weather(membership.organization)
        for forecast in WeatherForecast.objects.filter(
            organization=membership.organization, peak_alert=True
        ):
            Alert.objects.get_or_create(
                organization=membership.organization,
                site=forecast.site,
                category="weather_peak",
                title=f"Weather peak forecast · {forecast.site.name}",
                defaults={
                    "description": forecast.recommended_preparation,
                    "severity": "warning",
                    "confidence": forecast.confidence or 0.6,
                    "source_refs": [f"weatherforecast:{forecast.id}"],
                },
            )
        return Response({"forecasts_created": created})


class FieldScanConfirmView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        membership = admin_membership(request)
        scan = FieldScan.objects.filter(id=pk, organization=membership.organization).first()
        if not scan:
            return Response({"detail": "Not found"}, status=404)
        created = confirm_field_scan(scan, request.user, request.data.get("values"))
        return Response({"scan_id": str(scan.id), "status": scan.status, "reading_id": created.id if created else None})


class MaintenanceDueView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        membership = admin_membership(request)
        return Response(maintenance_due(membership.organization))


class StaffHistoryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, user_id):
        membership = admin_membership(request)
        tasks = Task.objects.filter(organization=membership.organization, assignee_id=user_id)
        scores = ScoreSnapshot.objects.filter(
            organization=membership.organization, user_id=user_id
        )
        evidence = Evidence.objects.filter(
            task__organization=membership.organization, submitted_by_id=user_id
        )
        return Response(
            {
                "user_id": user_id,
                "tasks": [
                    {
                        "id": str(item.id),
                        "title": item.title,
                        "status": item.status,
                        "due_at": item.due_at,
                    }
                    for item in tasks[:100]
                ],
                "scores": [
                    {
                        "score": item.overall_score,
                        "coverage": item.data_coverage,
                        "provisional": item.provisional,
                        "period_start": item.period_start,
                        "period_end": item.period_end,
                    }
                    for item in scores[:24]
                ],
                "evidence": [
                    {
                        "id": str(item.id),
                        "status": item.status,
                        "task_id": str(item.task_id),
                        "created_at": item.created_at,
                    }
                    for item in evidence[:50]
                ],
            }
        )


class AssetCandidateReviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        membership = admin_membership(request)
        candidate = AssetCandidate.objects.filter(
            id=pk, organization=membership.organization
        ).first()
        if not candidate:
            return Response({"detail": "Not found"}, status=404)
        action = request.data.get("action")
        from core.services.candidates import approve_asset_candidate, reject_asset_candidate

        if action == "approve":
            asset = approve_asset_candidate(candidate=candidate, reviewer=request.user)
            if asset is None:
                raise ValidationError("This candidate has already been reviewed")
            return Response({"status": "approved", "asset_id": asset.client_id})
        if action == "reject":
            changed = reject_asset_candidate(candidate=candidate, reviewer=request.user)
            if not changed:
                raise ValidationError("This candidate has already been reviewed")
            return Response({"status": "rejected"})
        raise ValidationError("action must be approve or reject")


class FieldScanUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        membership = admin_membership(request)
        upload = request.FILES.get("image") or request.FILES.get("file")
        if not upload:
            raise ValidationError({"image": ["A field image is required"]})
        try:
            import json as _json

            raw_fields = _json.loads(request.data.get("raw_fields") or "{}")
        except (TypeError, ValueError):
            raw_fields = {}
        scan = FieldScan.objects.create(
            organization=membership.organization,
            site_id=request.data.get("site_id"),
            meter_id=request.data.get("meter_id") or None,
            kind=request.data.get("kind", FieldScan.Kind.METER),
            image=upload,
            raw_fields=raw_fields if isinstance(raw_fields, dict) else {},
            created_by=request.user,
            status=FieldScan.Status.READY,
        )
        audit(membership.organization, request.user, "field_scan.uploaded", "fieldscan", scan.id)
        return Response(
            {"id": str(scan.id), "kind": scan.kind, "status": scan.status},
            status=status.HTTP_201_CREATED,
        )


class DeviceSummariesView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, device_id):
        token = request.headers.get("Authorization", "")
        if token.startswith("Device "):
            token = token[7:]
        if not token:
            raise PermissionDenied("Device token is required")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        device = ESPDevice.objects.filter(
            id=device_id, token_hash=token_hash, is_active=True
        ).first()
        if not device:
            raise PermissionDenied("Invalid device credentials")
        device.last_seen_at = timezone.now()
        device.save(update_fields=["last_seen_at"])
        return Response(device_summary(device))
