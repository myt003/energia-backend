from rest_framework import serializers

from core.models import (
    Asset,
    AssetAssignment,
    AssetCandidate,
    Evidence,
    EvidenceFrame,
    Invoice,
    InvoiceScan,
    InvoiceScanPage,
    InvoiceSchema,
    KnowledgeDocument,
    ScoreSnapshot,
    Task,
    TaskExplanation,
    VerificationResult,
)


class AssetSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="client_id")
    asset_code = serializers.CharField(source="code")
    type = serializers.CharField(source="asset_type")
    department = serializers.CharField(source="department.name")
    qr_value = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id",
            "asset_code",
            "name",
            "type",
            "department",
            "location",
            "serial_number",
            "qr_value",
        ]

    def get_qr_value(self, obj):
        return f"energia://asset/{obj.qr_identifier}"


class AssetAssignmentSerializer(serializers.ModelSerializer):
    asset = AssetSerializer(read_only=True)

    class Meta:
        model = AssetAssignment
        fields = ["asset", "interactions", "updated_at"]


class AssetCandidateSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source="asset_type")
    status = serializers.CharField(source="review_status", read_only=True)
    photo_url = serializers.SerializerMethodField()
    submitted_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AssetCandidate
        fields = [
            "id",
            "name",
            "type",
            "location",
            "identifier",
            "photo_url",
            "status",
            "duplicate_suggestions",
            "submitted_at",
            "submitted_by_name",
        ]

    def get_photo_url(self, obj):
        if not obj.photo:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.photo.url) if request else obj.photo.url

    def get_submitted_by_name(self, obj):
        return obj.submitted_by.get_full_name() or obj.submitted_by.username


class TaskSerializer(serializers.ModelSerializer):
    rule_id = serializers.CharField(source="rule_code_snapshot")
    rule_version = serializers.IntegerField(source="rule_version_snapshot")
    department = serializers.CharField(source="department.name")
    asset_id = serializers.CharField(source="asset.client_id", allow_null=True)
    asset_name = serializers.CharField(source="asset.name", allow_null=True)
    asset_code = serializers.CharField(source="asset.code", allow_null=True)
    citation = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id",
            "rule_id",
            "rule_version",
            "task_kind",
            "title",
            "instruction",
            "department",
            "asset_id",
            "asset_name",
            "asset_code",
            "due_at",
            "status",
            "urgency",
            "evidence_requirement",
            "pass_condition",
            "recommendation_owner",
            "citation",
            "why_assigned",
            "generated_at",
            "event_triggered",
            "completed_at",
            "submitted_at",
        ]

    def get_citation(self, obj):
        return {
            "source_title": obj.source_title,
            "source_version": obj.source_version,
            "section": obj.source_section,
        }


class TaskExplanationSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskExplanation
        fields = [
            "plain_instruction",
            "why_summary",
            "evidence_guidance",
            "safety_summary",
            "ai_simplified",
            "model_name",
            "model_version",
            "generated_at",
        ]


class EvidenceFrameSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvidenceFrame
        fields = ["frame_index", "timestamp_ms", "checksum_sha256"]


class VerificationResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationResult
        fields = [
            "status",
            "factual_summary",
            "evidence_quality",
            "confidence",
            "extracted_fields",
            "findings",
            "cited_frames",
            "deterministic_checks",
            "model_name",
            "model_version",
            "provider_request_id",
            "needs_human_review",
            "created_at",
        ]


class EvidenceSerializer(serializers.ModelSerializer):
    verification_result = VerificationResultSerializer(read_only=True)
    frames = EvidenceFrameSerializer(many=True, read_only=True)

    class Meta:
        model = Evidence
        fields = [
            "id",
            "task_id",
            "media_type",
            "original_filename",
            "mime_type",
            "size_bytes",
            "checksum_sha256",
            "captured_at",
            "status",
            "created_at",
            "updated_at",
            "verification_result",
            "frames",
        ]


class InvoiceSchemaSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceSchema
        fields = ["id", "name", "supplier", "energy_type", "field_schema"]


class InvoiceScanPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceScanPage
        fields = [
            "page_index",
            "original_filename",
            "mime_type",
            "checksum_sha256",
            "width",
            "height",
            "rotation_degrees",
            "quality_flags",
        ]


class InvoiceScanSerializer(serializers.ModelSerializer):
    pages = InvoiceScanPageSerializer(many=True, read_only=True)
    schema_name = serializers.CharField(source="schema.name", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)
    invoice_id = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceScan
        fields = [
            "id",
            "site_id",
            "site_name",
            "schema_id",
            "schema_name",
            "supplier",
            "status",
            "raw_fields",
            "corrected_fields",
            "field_confidences",
            "field_provenance",
            "field_boxes",
            "duplicate_warnings",
            "model_name",
            "model_request_id",
            "pages",
            "invoice_id",
            "created_at",
            "updated_at",
            "confirmed_at",
        ]

    def get_invoice_id(self, obj):
        try:
            return obj.invoice.id
        except Invoice.DoesNotExist:
            return None


class InvoiceSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)
    schema_name = serializers.CharField(source="schema.name", read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "site_id",
            "site_name",
            "schema_id",
            "schema_name",
            "source_scan_id",
            "supplier",
            "billing_period_start",
            "billing_period_end",
            "energy_value",
            "energy_unit",
            "total_cost",
            "currency",
            "tariff",
            "created_at",
        ]


class KnowledgeDocumentAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeDocument
        fields = [
            "id",
            "title",
            "source",
            "version",
            "effective_date",
            "jurisdiction",
            "status",
            "mime_type",
            "checksum_sha256",
            "chunk_count",
            "ingestion_error",
            "created_at",
        ]


class ScoreSnapshotAdminSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", allow_null=True)
    site_name = serializers.CharField(source="site.name")

    class Meta:
        model = ScoreSnapshot
        fields = [
            "id",
            "site_name",
            "department_name",
            "user_id",
            "display_name",
            "period_start",
            "period_end",
            "overall_score",
            "energy_component",
            "check_component",
            "data_coverage",
            "formula_version",
            "inputs",
            "provisional",
            "top_employee",
            "created_at",
        ]
