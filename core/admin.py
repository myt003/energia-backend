from django.contrib import admin

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
    EvidenceFrame,
    Invoice,
    InvoiceScan,
    InvoiceScanPage,
    InvoiceSchema,
    Membership,
    ModelRequestLog,
    Organization,
    RecommendationTemplate,
    RuleVersion,
    SafetyConstraint,
    Site,
    StaffWorkProfile,
    Task,
    TaskExplanation,
    VerificationResult,
)
from core.services.candidates import approve_asset_candidate, reject_asset_candidate


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "timezone", "created_at")
    search_fields = ("name", "slug")


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "organization", "timezone")
    list_filter = ("organization",)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "site")
    list_filter = ("site__organization", "site")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "site", "department", "role", "is_active")
    list_filter = ("organization", "site", "role", "is_active")
    search_fields = ("user__username", "user__first_name", "user__last_name")


@admin.register(ActivityType)
class ActivityTypeAdmin(admin.ModelAdmin):
    list_display = ("title", "code", "organization", "is_active")
    list_filter = ("organization", "is_active")


class CustomActivityInline(admin.TabularInline):
    model = CustomActivity
    extra = 0


@admin.register(StaffWorkProfile)
class StaffWorkProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "department",
        "pending_department",
        "job_role",
        "shift",
        "is_complete",
        "updated_at",
    )
    list_filter = ("department__site", "department", "shift", "is_complete")
    filter_horizontal = ("activities",)
    inlines = (CustomActivityInline,)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "asset_type", "department", "status", "updated_at")
    list_filter = ("organization", "site", "department", "asset_type", "status")
    search_fields = ("code", "name", "serial_number", "location")
    readonly_fields = ("qr_identifier", "created_at", "updated_at")


@admin.register(AssetAssignment)
class AssetAssignmentAdmin(admin.ModelAdmin):
    list_display = ("user", "asset", "is_active", "updated_at")
    list_filter = ("is_active", "asset__organization", "asset__department")
    search_fields = ("user__username", "asset__code", "asset__name")


@admin.register(AssetCandidate)
class AssetCandidateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "asset_type",
        "site",
        "submitted_by",
        "review_status",
        "submitted_at",
    )
    list_filter = ("organization", "site", "review_status", "submitted_at")
    search_fields = ("name", "identifier", "location", "submitted_by__username")
    readonly_fields = ("duplicate_suggestions", "submitted_at")
    actions = ("approve_candidates", "reject_candidates")

    @admin.action(description="Approve and add selected candidates to inventory")
    def approve_candidates(self, request, queryset):
        approved = 0
        for candidate in queryset:
            if approve_asset_candidate(candidate=candidate, reviewer=request.user):
                approved += 1
        self.message_user(request, f"Approved {approved} candidate(s)")

    @admin.action(description="Reject selected candidates")
    def reject_candidates(self, request, queryset):
        rejected = 0
        for candidate in queryset:
            if reject_asset_candidate(candidate=candidate, reviewer=request.user):
                rejected += 1
        self.message_user(request, f"Rejected {rejected} candidate(s)")


@admin.register(RuleVersion)
class RuleVersionAdmin(admin.ModelAdmin):
    list_display = ("code", "version", "organization", "urgency", "is_published")
    list_filter = ("organization", "is_published", "urgency", "event_trigger")
    search_fields = ("code", "title_template", "source_title")


@admin.register(RecommendationTemplate)
class RecommendationTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "version",
        "organization",
        "risk_level",
        "owner_role",
        "is_approved",
    )
    list_filter = ("organization", "is_approved", "risk_level")
    search_fields = ("code", "title_template", "source_title", "owner_role")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.is_approved:
            return tuple(field.name for field in self.model._meta.fields)
        return ("created_at",)


@admin.register(SafetyConstraint)
class SafetyConstraintAdmin(admin.ModelAdmin):
    list_display = ("code", "version", "organization", "is_locked", "is_active")
    list_filter = ("organization", "is_locked", "is_active")
    search_fields = ("code", "title", "source_title")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.is_locked:
            return tuple(field.name for field in self.model._meta.fields)
        return ("created_at",)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "assignee",
        "department",
        "asset",
        "task_kind",
        "status",
        "urgency",
        "due_at",
    )
    list_filter = (
        "organization",
        "site",
        "department",
        "task_kind",
        "status",
        "urgency",
    )
    search_fields = ("title", "assignee__username", "asset__code", "rule_code_snapshot")
    readonly_fields = (
        "unique_key",
        "rule_version",
        "recommendation_template",
        "task_kind",
        "rule_code_snapshot",
        "rule_version_snapshot",
        "title",
        "instruction",
        "evidence_requirement",
        "pass_condition",
        "source_title",
        "source_version",
        "source_section",
        "recommendation_owner",
        "why_assigned",
        "generated_at",
    )


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "organization", "actor", "action", "object_type", "object_id")
    list_filter = ("organization", "action", "object_type")
    search_fields = ("actor__username", "object_id", "action")
    readonly_fields = (
        "organization",
        "actor",
        "action",
        "object_type",
        "object_id",
        "metadata",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EvidenceFrameInline(admin.TabularInline):
    model = EvidenceFrame
    extra = 0
    readonly_fields = ("frame_index", "timestamp_ms", "image", "checksum_sha256")
    can_delete = False


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "task",
        "submitted_by",
        "media_type",
        "status",
        "created_at",
    )
    list_filter = ("media_type", "status", "task__organization")
    search_fields = ("task__title", "submitted_by__username", "checksum_sha256")
    readonly_fields = (
        "task",
        "submitted_by",
        "media_type",
        "file",
        "original_filename",
        "mime_type",
        "size_bytes",
        "checksum_sha256",
        "idempotency_key",
        "captured_at",
        "created_at",
        "updated_at",
    )
    inlines = (EvidenceFrameInline,)


@admin.register(VerificationResult)
class VerificationResultAdmin(admin.ModelAdmin):
    list_display = (
        "evidence",
        "status",
        "evidence_quality",
        "confidence",
        "needs_human_review",
        "model_name",
        "created_at",
    )
    list_filter = ("status", "evidence_quality", "needs_human_review", "model_name")
    readonly_fields = tuple(field.name for field in VerificationResult._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TaskExplanation)
class TaskExplanationAdmin(admin.ModelAdmin):
    list_display = ("task", "ai_simplified", "model_name", "generated_at")
    readonly_fields = tuple(field.name for field in TaskExplanation._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(AIJob)
class AIJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "evidence",
        "invoice_scan",
        "purpose",
        "status",
        "attempts",
        "updated_at",
    )
    list_filter = ("status", "purpose")
    readonly_fields = (
        "evidence",
        "invoice_scan",
        "purpose",
        "attempts",
        "locked_at",
        "last_error",
        "created_at",
        "updated_at",
    )


@admin.register(ModelRequestLog)
class ModelRequestLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "organization",
        "actor",
        "purpose",
        "model_name",
        "status",
        "input_tokens",
        "output_tokens",
        "latency_ms",
    )
    list_filter = ("organization", "purpose", "status", "model_name")
    readonly_fields = tuple(field.name for field in ModelRequestLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InvoiceSchema)
class InvoiceSchemaAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "supplier", "energy_type", "is_active")
    list_filter = ("organization", "energy_type", "is_active")
    search_fields = ("name", "supplier")


class InvoiceScanPageInline(admin.TabularInline):
    model = InvoiceScanPage
    extra = 0
    readonly_fields = (
        "page_index",
        "image",
        "original_filename",
        "mime_type",
        "checksum_sha256",
        "width",
        "height",
        "rotation_degrees",
        "quality_flags",
    )
    can_delete = False


@admin.register(InvoiceScan)
class InvoiceScanAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "supplier",
        "site",
        "schema",
        "submitted_by",
        "status",
        "created_at",
    )
    list_filter = ("organization", "site", "status", "schema")
    search_fields = ("supplier", "submitted_by__username", "ocr_text")
    readonly_fields = (
        "organization",
        "site",
        "schema",
        "supplier",
        "submitted_by",
        "status",
        "ocr_text",
        "raw_fields",
        "corrected_fields",
        "field_confidences",
        "field_provenance",
        "field_boxes",
        "duplicate_warnings",
        "model_name",
        "model_request_id",
        "created_at",
        "updated_at",
        "confirmed_at",
        "confirmed_by",
    )
    inlines = (InvoiceScanPageInline,)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "supplier",
        "site",
        "billing_period_start",
        "billing_period_end",
        "energy_value",
        "energy_unit",
        "total_cost",
        "currency",
    )
    list_filter = ("organization", "site", "supplier", "currency")
    search_fields = ("supplier", "tariff")
    readonly_fields = tuple(field.name for field in Invoice._meta.fields)

    def has_add_permission(self, request):
        return False
