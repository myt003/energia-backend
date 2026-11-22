import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Organization(models.Model):
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=80, unique=True)
    timezone = models.CharField(max_length=64, default="UTC")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Site(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="sites"
    )
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=40)
    timezone = models.CharField(max_length=64, default="UTC")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="unique_site_code_per_org"
            )
        ]

    def __str__(self):
        return f"{self.organization.name} / {self.name}"


class Department(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=40)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "code"], name="unique_department_code_per_site"
            )
        ]

    @property
    def organization(self):
        return self.site.organization

    def __str__(self):
        return f"{self.site.name} / {self.name}"


class Membership(models.Model):
    class Role(models.TextChoices):
        STAFF = "staff", "Staff"
        TEAM_LEAD = "team_lead", "Team lead"
        MAINTENANCE = "maintenance", "Maintenance"
        ENERGY_MANAGER = "energy_manager", "Energy manager"
        ADMIN = "admin", "Administrator"
        AUDITOR = "auditor", "Auditor"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="memberships")
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="memberships",
        null=True,
        blank=True,
    )
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.STAFF)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization", "site", "department", "role"],
                name="unique_scoped_membership",
                nulls_distinct=False,
            )
        ]

    def clean(self):
        if self.site_id and self.organization_id:
            if self.site.organization_id != self.organization_id:
                raise ValidationError("Site must belong to the membership organization")
        if self.department_id and self.site_id:
            if self.department.site_id != self.site_id:
                raise ValidationError("Department must belong to the membership site")

    def __str__(self):
        return f"{self.user} / {self.organization} / {self.role}"


class ActivityType(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="activity_types"
    )
    code = models.SlugField(max_length=80)
    title = models.CharField(max_length=120)
    description = models.CharField(max_length=240, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="unique_activity_code_per_org"
            )
        ]

    def __str__(self):
        return self.title


class StaffWorkProfile(models.Model):
    class Shift(models.TextChoices):
        DAY = "day", "Day · 06:00–14:00"
        EVENING = "evening", "Evening · 14:00–22:00"
        NIGHT = "night", "Night · 22:00–06:00"
        ROTATING = "rotating", "Rotating shift"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="work_profile"
    )
    membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="work_profiles"
    )
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="staff_profiles"
    )
    pending_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="pending_staff_profiles",
        null=True,
        blank=True,
    )
    job_role = models.CharField(max_length=100)
    shift = models.CharField(max_length=20, choices=Shift.choices, default=Shift.DAY)
    activities = models.ManyToManyField(ActivityType, blank=True, related_name="profiles")
    notes = models.CharField(max_length=240, blank=True)
    is_complete = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.department_id and self.membership_id:
            if self.department.site_id != self.membership.site_id:
                raise ValidationError("Profile department must belong to the membership site")
        if self.pending_department_id and self.membership_id:
            if self.pending_department.site_id != self.membership.site_id:
                raise ValidationError("Pending department must belong to the membership site")

    def __str__(self):
        return f"{self.user} work profile"


class CustomActivity(models.Model):
    profile = models.ForeignKey(
        StaffWorkProfile, on_delete=models.CASCADE, related_name="custom_activities"
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    title = models.CharField(max_length=80)
    description = models.CharField(max_length=180, blank=True)
    selected = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "title"], name="unique_custom_activity_title"
            )
        ]

    def __str__(self):
        return self.title


class Asset(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="assets"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="assets")
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="assets"
    )
    client_id = models.SlugField(max_length=100)
    code = models.CharField(max_length=60)
    name = models.CharField(max_length=160)
    asset_type = models.CharField(max_length=100)
    location = models.CharField(max_length=160)
    serial_number = models.CharField(max_length=100, blank=True)
    qr_identifier = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "client_id"],
                name="unique_asset_client_id_per_org",
            ),
            models.UniqueConstraint(
                fields=["organization", "code"], name="unique_asset_code_per_org"
            )
        ]
        indexes = [
            models.Index(fields=["organization", "department", "status"]),
            models.Index(fields=["organization", "name"]),
        ]

    def clean(self):
        if self.site_id and self.organization_id:
            if self.site.organization_id != self.organization_id:
                raise ValidationError("Asset site must belong to the organization")
        if self.department_id and self.site_id:
            if self.department.site_id != self.site_id:
                raise ValidationError("Asset department must belong to the site")

    def __str__(self):
        return f"{self.code} · {self.name}"


class AssetAssignment(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="asset_assignments"
    )
    asset = models.ForeignKey(
        Asset, on_delete=models.PROTECT, related_name="assignments"
    )
    interactions = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "asset"], name="unique_user_asset_assignment"
            )
        ]

    def __str__(self):
        return f"{self.user} / {self.asset}"


def candidate_photo_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"organizations/{instance.organization_id}/asset-candidates/{instance.id}.{extension}"


class AssetCandidate(models.Model):
    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="asset_candidates"
    )
    site = models.ForeignKey(
        Site, on_delete=models.PROTECT, related_name="asset_candidates"
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="asset_candidates",
        null=True,
        blank=True,
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_asset_candidates",
    )
    name = models.CharField(max_length=160)
    asset_type = models.CharField(max_length=100)
    location = models.CharField(max_length=160)
    identifier = models.CharField(max_length=100, blank=True)
    photo = models.ImageField(upload_to=candidate_photo_path)
    review_status = models.CharField(
        max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING
    )
    duplicate_suggestions = models.JSONField(default=list, blank=True)
    review_reason = models.CharField(max_length=500, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_asset_candidates",
        null=True,
        blank=True,
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [models.Index(fields=["organization", "review_status"])]

    def __str__(self):
        return f"{self.name} ({self.review_status})"


class RuleVersion(models.Model):
    class Urgency(models.TextChoices):
        ROUTINE = "routine", "Routine"
        IMPORTANT = "important", "Important"
        URGENT = "urgent", "Urgent"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="rule_versions"
    )
    code = models.CharField(max_length=60)
    version = models.PositiveIntegerField()
    title_template = models.CharField(max_length=240)
    instruction = models.TextField()
    target_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="rule_versions",
        null=True,
        blank=True,
    )
    target_roles = models.JSONField(default=list, blank=True)
    required_activity_codes = models.JSONField(default=list, blank=True)
    required_asset_types = models.JSONField(default=list, blank=True)
    target_asset_client_ids = models.JSONField(default=list, blank=True)
    event_trigger = models.CharField(max_length=80, blank=True)
    evidence_requirement = models.CharField(max_length=240)
    pass_condition = models.CharField(max_length=500)
    urgency = models.CharField(
        max_length=20, choices=Urgency.choices, default=Urgency.ROUTINE
    )
    source_title = models.CharField(max_length=240)
    source_version = models.CharField(max_length=60)
    source_section = models.CharField(max_length=160)
    due_hour = models.PositiveSmallIntegerField(default=12)
    due_minute = models.PositiveSmallIntegerField(default=0)
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code", "version"],
                name="unique_rule_version_per_org",
            ),
            models.CheckConstraint(
                condition=Q(due_hour__gte=0, due_hour__lte=23),
                name="valid_rule_due_hour",
            ),
            models.CheckConstraint(
                condition=Q(due_minute__gte=0, due_minute__lte=59),
                name="valid_rule_due_minute",
            ),
        ]

    def __str__(self):
        return f"{self.code} v{self.version}"


class RecommendationTemplate(models.Model):
    class RiskLevel(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="recommendation_templates"
    )
    code = models.CharField(max_length=60)
    version = models.PositiveIntegerField()
    title_template = models.CharField(max_length=240)
    instruction = models.TextField()
    target_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="recommendation_templates",
        null=True,
        blank=True,
    )
    target_asset_types = models.JSONField(default=list, blank=True)
    required_activity_codes = models.JSONField(default=list, blank=True)
    required_interactions = models.JSONField(default=list, blank=True)
    recommendation_tags = models.JSONField(default=list)
    risk_level = models.CharField(
        max_length=20, choices=RiskLevel.choices, default=RiskLevel.LOW
    )
    evidence_requirement = models.CharField(max_length=240)
    pass_condition = models.CharField(max_length=500)
    owner_role = models.CharField(max_length=120)
    source_title = models.CharField(max_length=240)
    source_version = models.CharField(max_length=60)
    source_section = models.CharField(max_length=160)
    safety_note = models.CharField(max_length=500)
    due_hour = models.PositiveSmallIntegerField(default=14)
    due_minute = models.PositiveSmallIntegerField(default=0)
    is_approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code", "version"],
                name="unique_recommendation_template_version",
            ),
            models.CheckConstraint(
                condition=Q(due_hour__gte=0, due_hour__lte=23),
                name="valid_recommendation_due_hour",
            ),
            models.CheckConstraint(
                condition=Q(due_minute__gte=0, due_minute__lte=59),
                name="valid_recommendation_due_minute",
            ),
        ]

    def __str__(self):
        return f"{self.code} v{self.version}"


class SafetyConstraint(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="safety_constraints"
    )
    code = models.CharField(max_length=60)
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=240)
    instruction = models.TextField()
    target_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="safety_constraints",
        null=True,
        blank=True,
    )
    target_asset_types = models.JSONField(default=list, blank=True)
    blocked_recommendation_tags = models.JSONField(default=list)
    source_title = models.CharField(max_length=240)
    source_version = models.CharField(max_length=60)
    source_section = models.CharField(max_length=160)
    is_locked = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code", "version"],
                name="unique_safety_constraint_version",
            )
        ]

    def __str__(self):
        return f"{self.code} v{self.version}"


class Task(models.Model):
    class Kind(models.TextChoices):
        COMPLIANCE = "compliance", "Compliance check"
        ENERGY_HABIT = "energy_habit", "Energy habit"

    class Status(models.TextChoices):
        DUE = "due", "Due"
        COMPLETED = "completed", "Completed"
        OVERDUE = "overdue", "Overdue"
        WAITING_REVIEW = "waiting_review", "Waiting for review"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    unique_key = models.CharField(max_length=240, unique=True)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="tasks"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="tasks")
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="tasks"
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasks"
    )
    asset = models.ForeignKey(
        Asset, on_delete=models.SET_NULL, related_name="tasks", null=True, blank=True
    )
    rule_version = models.ForeignKey(
        RuleVersion,
        on_delete=models.PROTECT,
        related_name="tasks",
        null=True,
        blank=True,
    )
    recommendation_template = models.ForeignKey(
        RecommendationTemplate,
        on_delete=models.PROTECT,
        related_name="tasks",
        null=True,
        blank=True,
    )
    task_kind = models.CharField(
        max_length=30, choices=Kind.choices, default=Kind.COMPLIANCE
    )
    rule_code_snapshot = models.CharField(max_length=60)
    rule_version_snapshot = models.PositiveIntegerField()
    title = models.CharField(max_length=240)
    instruction = models.TextField()
    evidence_requirement = models.CharField(max_length=240)
    pass_condition = models.CharField(max_length=500)
    urgency = models.CharField(max_length=20, choices=RuleVersion.Urgency.choices)
    source_title = models.CharField(max_length=240)
    source_version = models.CharField(max_length=60)
    source_section = models.CharField(max_length=160)
    recommendation_owner = models.CharField(max_length=120, blank=True)
    why_assigned = models.JSONField(default=list)
    due_at = models.DateTimeField()
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DUE)
    event_triggered = models.BooleanField(default=False)
    generated_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_at", "id"]
        indexes = [
            models.Index(fields=["organization", "department", "status", "due_at"]),
            models.Index(fields=["assignee", "status", "due_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(rule_version__isnull=False, recommendation_template__isnull=True)
                    | Q(rule_version__isnull=True, recommendation_template__isnull=False)
                ),
                name="task_has_exactly_one_published_source",
            )
        ]

    def __str__(self):
        return self.title


class AuditEvent(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="audit_events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_events",
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "created_at"])]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit events are append-only")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events are append-only")

    def __str__(self):
        return f"{self.action} {self.object_type}:{self.object_id}"


class TaskExplanation(models.Model):
    task = models.OneToOneField(
        Task, on_delete=models.CASCADE, related_name="explanation"
    )
    plain_instruction = models.TextField()
    why_summary = models.TextField()
    evidence_guidance = models.TextField()
    safety_summary = models.TextField(blank=True)
    ai_simplified = models.BooleanField(default=False)
    model_name = models.CharField(max_length=120, blank=True)
    model_version = models.CharField(max_length=120, blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Explanation for {self.task_id}"


def evidence_upload_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return (
        f"organizations/{instance.task.organization_id}/evidence/"
        f"{instance.id}/original.{extension}"
    )


class Evidence(models.Model):
    class MediaType(models.TextChoices):
        PHOTO = "photo", "Photo"
        VIDEO = "video", "Video"

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        PROCESSING = "processing", "Processing"
        VERIFIED = "verified", "Verified"
        POTENTIAL_PROBLEM = "potential_problem", "Potential problem"
        INSUFFICIENT = "insufficient_evidence", "Insufficient evidence"
        HUMAN_REVIEW = "human_review_required", "Human review required"
        FAILED = "failed", "Processing failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="evidence")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_evidence",
    )
    media_type = models.CharField(max_length=20, choices=MediaType.choices)
    file = models.FileField(upload_to=evidence_upload_path)
    original_filename = models.CharField(max_length=240)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=100)
    captured_at = models.DateTimeField()
    status = models.CharField(
        max_length=40, choices=Status.choices, default=Status.UPLOADED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["submitted_by", "idempotency_key"],
                name="unique_evidence_idempotency_key_per_user",
            )
        ]
        indexes = [
            models.Index(fields=["task", "status"]),
            models.Index(fields=["submitted_by", "created_at"]),
        ]

    def __str__(self):
        return f"{self.media_type} evidence {self.id}"


def evidence_frame_path(instance, filename):
    return (
        f"organizations/{instance.evidence.task.organization_id}/evidence/"
        f"{instance.evidence_id}/frames/{instance.frame_index:02d}.jpg"
    )


class EvidenceFrame(models.Model):
    evidence = models.ForeignKey(
        Evidence, on_delete=models.CASCADE, related_name="frames"
    )
    frame_index = models.PositiveSmallIntegerField()
    timestamp_ms = models.PositiveIntegerField()
    image = models.ImageField(upload_to=evidence_frame_path)
    checksum_sha256 = models.CharField(max_length=64)

    class Meta:
        ordering = ["frame_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["evidence", "frame_index"],
                name="unique_frame_index_per_evidence",
            )
        ]


class VerificationResult(models.Model):
    evidence = models.OneToOneField(
        Evidence, on_delete=models.CASCADE, related_name="verification_result"
    )
    status = models.CharField(max_length=40, choices=Evidence.Status.choices)
    factual_summary = models.TextField()
    evidence_quality = models.CharField(max_length=40)
    confidence = models.FloatField(default=0)
    extracted_fields = models.JSONField(default=dict, blank=True)
    findings = models.JSONField(default=list, blank=True)
    cited_frames = models.JSONField(default=list, blank=True)
    deterministic_checks = models.JSONField(default=dict, blank=True)
    model_name = models.CharField(max_length=120, blank=True)
    model_version = models.CharField(max_length=120, blank=True)
    provider_request_id = models.CharField(max_length=160, blank=True)
    needs_human_review = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.status} for {self.evidence_id}"


class AIJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evidence = models.OneToOneField(
        Evidence,
        on_delete=models.CASCADE,
        related_name="ai_job",
        null=True,
        blank=True,
    )
    invoice_scan = models.OneToOneField(
        "InvoiceScan",
        on_delete=models.CASCADE,
        related_name="ai_job",
        null=True,
        blank=True,
    )
    purpose = models.CharField(max_length=80, default="evidence_verification")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["status", "available_at"])]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(evidence__isnull=False, invoice_scan__isnull=True)
                    | Q(evidence__isnull=True, invoice_scan__isnull=False)
                ),
                name="ai_job_has_exactly_one_subject",
            )
        ]


class ModelRequestLog(models.Model):
    class Status(models.TextChoices):
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        UNAVAILABLE = "unavailable", "Unavailable"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="model_request_logs"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="model_request_logs",
        null=True,
        blank=True,
    )
    purpose = models.CharField(max_length=80)
    model_name = models.CharField(max_length=120)
    provider_request_id = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    request_hash = models.CharField(max_length=64)
    error_code = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "purpose", "created_at"])]


class InvoiceSchema(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="invoice_schemas"
    )
    name = models.CharField(max_length=160)
    supplier = models.CharField(max_length=160, blank=True)
    energy_type = models.CharField(max_length=60, default="electricity")
    field_schema = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"], name="unique_invoice_schema_name"
            )
        ]

    def __str__(self):
        return self.name


class InvoiceScan(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        PROCESSING = "processing", "Processing"
        READY = "ready_for_review", "Ready for review"
        CONFIRMED = "confirmed", "Confirmed"
        HUMAN_REVIEW = "human_review_required", "Human review required"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="invoice_scans"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="invoice_scans")
    schema = models.ForeignKey(
        InvoiceSchema, on_delete=models.PROTECT, related_name="scans"
    )
    supplier = models.CharField(max_length=160)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="invoice_scans",
    )
    status = models.CharField(
        max_length=40, choices=Status.choices, default=Status.UPLOADED
    )
    ocr_text = models.TextField(blank=True)
    raw_fields = models.JSONField(default=dict, blank=True)
    corrected_fields = models.JSONField(default=dict, blank=True)
    field_confidences = models.JSONField(default=dict, blank=True)
    field_provenance = models.JSONField(default=dict, blank=True)
    field_boxes = models.JSONField(default=dict, blank=True)
    duplicate_warnings = models.JSONField(default=list, blank=True)
    model_name = models.CharField(max_length=120, blank=True)
    model_request_id = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_invoice_scans",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "site", "status", "created_at"])
        ]

    def __str__(self):
        return f"{self.supplier} scan {self.id}"


def invoice_scan_page_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return (
        f"organizations/{instance.scan.organization_id}/invoice-scans/"
        f"{instance.scan_id}/page-{instance.page_index:02d}.{extension}"
    )


class InvoiceScanPage(models.Model):
    scan = models.ForeignKey(InvoiceScan, on_delete=models.CASCADE, related_name="pages")
    page_index = models.PositiveSmallIntegerField()
    image = models.ImageField(upload_to=invoice_scan_page_path)
    original_filename = models.CharField(max_length=240)
    mime_type = models.CharField(max_length=100)
    checksum_sha256 = models.CharField(max_length=64)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    rotation_degrees = models.SmallIntegerField(default=0)
    quality_flags = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["page_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["scan", "page_index"], name="unique_invoice_scan_page"
            )
        ]


class Invoice(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="invoices"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="invoices")
    schema = models.ForeignKey(
        InvoiceSchema, on_delete=models.PROTECT, related_name="invoices"
    )
    source_scan = models.OneToOneField(
        InvoiceScan, on_delete=models.PROTECT, related_name="invoice"
    )
    supplier = models.CharField(max_length=160)
    billing_period_start = models.DateField()
    billing_period_end = models.DateField()
    energy_value = models.DecimalField(max_digits=18, decimal_places=4)
    energy_unit = models.CharField(max_length=40)
    total_cost = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=12, default="USD")
    tariff = models.CharField(max_length=120, blank=True)
    raw_extraction = models.JSONField(default=dict)
    confirmed_values = models.JSONField(default=dict)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_invoices",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-billing_period_end", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "site",
                    "supplier",
                    "billing_period_start",
                    "billing_period_end",
                ],
                name="unique_invoice_period_per_supplier_site",
            )
        ]

    def __str__(self):
        return f"{self.supplier} {self.billing_period_start}–{self.billing_period_end}"


def knowledge_document_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"organizations/{instance.organization_id}/knowledge/{instance.id}.{extension}"


def field_scan_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"organizations/{instance.organization_id}/field-scans/{instance.id}.{extension}"


def import_batch_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"organizations/{instance.organization_id}/imports/{instance.id}.{extension}"


class Notification(models.Model):
    class Category(models.TextChoices):
        TASK = "task", "Task"
        ALERT = "alert", "Alert"
        MAINTENANCE = "maintenance", "Maintenance"
        WEATHER = "weather", "Weather"
        SYSTEM = "system", "System"

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="notifications"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="energia_notifications",
        null=True,
        blank=True,
    )
    site = models.ForeignKey(
        Site, on_delete=models.CASCADE, related_name="notifications", null=True, blank=True
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    category = models.CharField(
        max_length=30, choices=Category.choices, default=Category.SYSTEM
    )
    severity = models.CharField(
        max_length=20, choices=Severity.choices, default=Severity.INFO
    )
    action_url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "user", "is_read"])]


class KnowledgeDocument(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"
        ERROR = "error", "Ingestion error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="knowledge_documents"
    )
    site = models.ForeignKey(
        Site, on_delete=models.PROTECT, related_name="knowledge_documents", null=True, blank=True
    )
    title = models.CharField(max_length=240)
    source = models.CharField(max_length=240, blank=True)
    version = models.CharField(max_length=60, default="1.0")
    effective_date = models.DateField(null=True, blank=True)
    jurisdiction = models.CharField(max_length=120, blank=True)
    applicable_sites = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    file = models.FileField(upload_to=knowledge_document_path, blank=True)
    mime_type = models.CharField(max_length=120, blank=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    extracted_text = models.TextField(blank=True)
    ingestion_error = models.TextField(blank=True)
    chunk_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="knowledge_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status"])]


class KnowledgeChunk(models.Model):
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="chunks"
    )
    chunk_index = models.PositiveIntegerField()
    section = models.CharField(max_length=240, blank=True)
    page = models.PositiveIntegerField(null=True, blank=True)
    text = models.TextField()
    keywords = models.JSONField(default=list, blank=True)
    checksum_sha256 = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["document", "chunk_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "chunk_index"], name="unique_chunk_index_per_document"
            )
        ]


class CustomRule(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEW = "review", "Review"
        PUBLISHED = "published", "Published"
        PAUSED = "paused", "Paused"
        ARCHIVED = "archived", "Archived"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="custom_rules"
    )
    code = models.CharField(max_length=60)
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=240)
    definition = models.JSONField(default=dict)
    preview_summary = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_custom_rules",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_custom_rules",
        null=True,
        blank=True,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code", "version"], name="unique_custom_rule_version"
            )
        ]


class MeterSchema(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="meter_schemas"
    )
    name = models.CharField(max_length=160)
    field_schema = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"], name="unique_meter_schema_name"
            )
        ]


class Meter(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        CALIBRATION_DUE = "calibration_due", "Calibration due"
        RETIRED = "retired", "Retired"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="meters"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="meters")
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="meters", null=True, blank=True
    )
    asset = models.ForeignKey(
        Asset, on_delete=models.SET_NULL, related_name="meters", null=True, blank=True
    )
    schema = models.ForeignKey(
        MeterSchema, on_delete=models.SET_NULL, related_name="meters", null=True, blank=True
    )
    code = models.CharField(max_length=80)
    name = models.CharField(max_length=160)
    utility_type = models.CharField(max_length=60, default="electricity")
    unit = models.CharField(max_length=40, default="kWh")
    register_config = models.JSONField(default=dict, blank=True)
    calibration_due = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.ACTIVE)
    qr_identifier = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="unique_meter_code")
        ]
        indexes = [models.Index(fields=["organization", "site", "status"])]


class Reading(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"
        SCAN = "scan", "Scan"
        SYSTEM = "system", "System"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="readings"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="readings")
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="readings", null=True, blank=True
    )
    meter = models.ForeignKey(
        Meter, on_delete=models.SET_NULL, related_name="readings", null=True, blank=True
    )
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    utility_type = models.CharField(max_length=60, default="electricity")
    value = models.DecimalField(max_digits=20, decimal_places=6)
    unit = models.CharField(max_length=40)
    canonical_value = models.DecimalField(max_digits=20, decimal_places=6)
    canonical_unit = models.CharField(max_length=40)
    cost = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    currency = models.CharField(max_length=12, default="USD")
    period_start = models.DateField()
    period_end = models.DateField()
    recorded_at = models.DateTimeField(null=True, blank=True)
    quality_flags = models.JSONField(default=list, blank=True)
    revision = models.PositiveIntegerField(default=1)
    correction_reason = models.CharField(max_length=300, blank=True)
    previous = models.ForeignKey(
        "self", on_delete=models.SET_NULL, related_name="corrections", null=True, blank=True
    )
    field_scan = models.ForeignKey(
        "FieldScan", on_delete=models.SET_NULL, related_name="readings", null=True, blank=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="readings"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end", "-created_at"]
        indexes = [
            models.Index(fields=["organization", "site", "period_end"]),
            models.Index(fields=["meter", "period_end"]),
        ]


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        STAGED = "staged", "Staged"
        VALIDATED = "validated", "Validated"
        CONFIRMED = "confirmed", "Confirmed"
        FAILED = "failed", "Failed"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="import_batches"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="import_batches")
    kind = models.CharField(max_length=40, default="readings")
    file = models.FileField(upload_to=import_batch_path)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.STAGED)
    column_mapping = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="import_batches"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_import_batches",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]


class ImportRow(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        DUPLICATE = "duplicate", "Duplicate"
        IMPORTED = "imported", "Imported"

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="rows")
    row_index = models.PositiveIntegerField()
    raw_data = models.JSONField(default=dict)
    parsed_data = models.JSONField(default=dict)
    errors = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["batch", "row_index"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "row_index"], name="unique_import_row")
        ]


class DashboardView(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="dashboard_views"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dashboard_views"
    )
    name = models.CharField(max_length=160)
    scope = models.JSONField(default=dict)
    chart_config = models.JSONField(default=dict)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]


class Baseline(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="baselines"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="baselines")
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="baselines", null=True, blank=True
    )
    name = models.CharField(max_length=160)
    period_start = models.DateField()
    period_end = models.DateField()
    value = models.DecimalField(max_digits=20, decimal_places=6)
    unit = models.CharField(max_length=40)
    adjustments = models.JSONField(default=list, blank=True)
    normalization = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_baselines",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]


class EnPI(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="enpis"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="enpis")
    name = models.CharField(max_length=160)
    formula = models.JSONField(default=dict)
    unit = models.CharField(max_length=40)
    variables = models.JSONField(default=list, blank=True)
    baseline = models.ForeignKey(
        Baseline, on_delete=models.SET_NULL, related_name="enpis", null=True, blank=True
    )
    version = models.PositiveIntegerField(default=1)
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "-version"]


class ScoreSnapshot(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="score_snapshots"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="score_snapshots")
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="score_snapshots", null=True, blank=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="score_snapshots",
        null=True,
        blank=True,
    )
    display_name = models.CharField(max_length=160, blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    overall_score = models.FloatField()
    energy_component = models.FloatField(null=True, blank=True)
    check_component = models.FloatField(null=True, blank=True)
    data_coverage = models.FloatField(default=0)
    formula_version = models.CharField(max_length=40, default="2026-11-v1")
    inputs = models.JSONField(default=dict, blank=True)
    provisional = models.BooleanField(default=False)
    top_employee = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end", "-created_at"]
        indexes = [
            models.Index(fields=["organization", "department", "created_at"]),
            models.Index(fields=["organization", "user", "created_at"]),
        ]


class Alert(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        REVIEW = "review", "Review"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        FALSE_POSITIVE = "false_positive", "False positive"
        RESOLVED = "resolved", "Resolved"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="alerts"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="alerts")
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="alerts", null=True, blank=True
    )
    asset = models.ForeignKey(
        Asset, on_delete=models.SET_NULL, related_name="alerts", null=True, blank=True
    )
    evidence = models.ForeignKey(
        Evidence, on_delete=models.SET_NULL, related_name="alerts", null=True, blank=True
    )
    category = models.CharField(max_length=80)
    severity = models.CharField(max_length=20, default="warning")
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    confidence = models.FloatField(default=0)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.NEW)
    source_refs = models.JSONField(default=list, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="acknowledged_alerts",
        null=True,
        blank=True,
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "created_at"])]


class Recommendation(models.Model):
    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"
        ASSIGNED = "assigned", "Assigned"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="recommendations"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="recommendations")
    alert = models.ForeignKey(
        Alert, on_delete=models.SET_NULL, related_name="recommendations", null=True, blank=True
    )
    asset = models.ForeignKey(
        Asset, on_delete=models.SET_NULL, related_name="recommendations", null=True, blank=True
    )
    category = models.CharField(max_length=80)
    title = models.CharField(max_length=240)
    rationale = models.TextField(blank=True)
    urgency = models.CharField(max_length=20, default="routine")
    expected_impact = models.JSONField(default=dict, blank=True)
    assumptions = models.JSONField(default=list, blank=True)
    safe_next_check = models.TextField(blank=True)
    evidence_needed = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROPOSED)
    owner = models.CharField(max_length=160, blank=True)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class MaintenancePlan(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="maintenance_plans"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="maintenance_plans")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="maintenance_plans")
    interval_days = models.PositiveIntegerField(default=90)
    running_hour_interval = models.PositiveIntegerField(null=True, blank=True)
    last_service_date = models.DateField(null=True, blank=True)
    running_hours = models.PositiveIntegerField(default=0)
    next_due_date = models.DateField(null=True, blank=True)
    checklist = models.JSONField(default=list, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["next_due_date", "id"]


class WorkOrder(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="work_orders"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="work_orders")
    asset = models.ForeignKey(
        Asset, on_delete=models.PROTECT, related_name="work_orders"
    )
    maintenance_plan = models.ForeignKey(
        MaintenancePlan, on_delete=models.SET_NULL, related_name="work_orders", null=True, blank=True
    )
    recommendation = models.ForeignKey(
        Recommendation, on_delete=models.SET_NULL, related_name="work_orders", null=True, blank=True
    )
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    owner = models.CharField(max_length=160, blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    evidence_needed = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="work_orders"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class WeatherForecast(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="weather_forecasts"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="weather_forecasts")
    forecast_for = models.DateTimeField()
    temperature_c = models.FloatField()
    humidity = models.FloatField(null=True, blank=True)
    source = models.CharField(max_length=120, default="open-meteo")
    confidence = models.FloatField(null=True, blank=True)
    peak_alert = models.BooleanField(default=False)
    recommended_preparation = models.TextField(blank=True)
    inputs = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["forecast_for"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "forecast_for", "source"], name="unique_weather_forecast"
            )
        ]


class EnergyReview(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        SUPERSEDED = "superseded", "Superseded"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="energy_reviews"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="energy_reviews")
    title = models.CharField(max_length=240)
    period_start = models.DateField()
    period_end = models.DateField()
    criteria = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_energy_reviews",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="energy_reviews"
    )
    created_at = models.DateTimeField(auto_now_add=True)


class EnergyUse(models.Model):
    review = models.ForeignKey(
        EnergyReview, on_delete=models.CASCADE, related_name="energy_uses"
    )
    asset = models.ForeignKey(
        Asset, on_delete=models.SET_NULL, related_name="energy_uses", null=True, blank=True
    )
    name = models.CharField(max_length=200)
    utility_type = models.CharField(max_length=60)
    annual_energy = models.DecimalField(max_digits=20, decimal_places=4, default=0)
    unit = models.CharField(max_length=40)
    contribution = models.FloatField(default=0)
    is_significant = models.BooleanField(default=False)
    relevant_variables = models.JSONField(default=list, blank=True)
    owner_role = models.CharField(max_length=120, blank=True)
    rationale = models.TextField(blank=True)


class EmissionFactor(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="emission_factors"
    )
    source = models.CharField(max_length=200)
    region = models.CharField(max_length=120, blank=True)
    utility_type = models.CharField(max_length=60)
    value = models.DecimalField(max_digits=20, decimal_places=8)
    unit = models.CharField(max_length=60)
    market_method = models.BooleanField(default=False)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    source_reference = models.CharField(max_length=240, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-effective_from", "-version"]


class EmissionRecord(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="emission_records"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="emission_records")
    reading = models.ForeignKey(
        Reading, on_delete=models.SET_NULL, related_name="emission_records", null=True, blank=True
    )
    factor = models.ForeignKey(
        EmissionFactor, on_delete=models.PROTECT, related_name="emission_records"
    )
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    canonical_unit = models.CharField(max_length=40)
    co2e_tonnes = models.DecimalField(max_digits=20, decimal_places=6)
    method = models.CharField(max_length=40, default="market")
    period_start = models.DateField()
    period_end = models.DateField()
    inputs = models.JSONField(default=dict, blank=True)
    calculated_at = models.DateTimeField(auto_now_add=True)


class MaturityAssessment(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="maturity_assessments"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="maturity_assessments")
    level = models.PositiveSmallIntegerField()
    answers = models.JSONField(default=dict)
    assessed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="maturity_assessments"
    )
    assessed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-assessed_at"]


class AuditPlan(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In progress"
        CLOSED = "closed", "Closed"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="audit_plans"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="audit_plans")
    title = models.CharField(max_length=240)
    period_start = models.DateField()
    period_end = models.DateField()
    scope = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="audit_plans"
    )
    created_at = models.DateTimeField(auto_now_add=True)


class AuditFinding(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CORRECTION = "correction", "Immediate correction"
        ROOT_CAUSE = "root_cause", "Root cause analysis"
        ACTION = "action", "Corrective action"
        EFFECTIVENESS = "effectiveness", "Effectiveness review"
        CLOSED = "closed", "Closed"
        REOPENED = "reopened", "Reopened"

    audit = models.ForeignKey(AuditPlan, on_delete=models.CASCADE, related_name="findings")
    task = models.ForeignKey(
        Task, on_delete=models.SET_NULL, related_name="audit_findings", null=True, blank=True
    )
    asset = models.ForeignKey(
        Asset, on_delete=models.SET_NULL, related_name="audit_findings", null=True, blank=True
    )
    requirement = models.CharField(max_length=300)
    description = models.TextField()
    finding_type = models.CharField(max_length=60, default="nonconformity")
    severity = models.CharField(max_length=20, default="major")
    iso_reference = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)


class CorrectiveAction(models.Model):
    finding = models.ForeignKey(
        AuditFinding, on_delete=models.CASCADE, related_name="corrective_actions"
    )
    action = models.TextField()
    owner = models.CharField(max_length=160, blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=30, default="open")
    evidence_required = models.TextField(blank=True)
    completion_evidence = models.TextField(blank=True)
    effectiveness_notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class FieldScan(models.Model):
    class Kind(models.TextChoices):
        INVOICE = "invoice", "Invoice"
        METER = "meter", "Meter"
        NAMEPLATE = "nameplate", "Nameplate"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready for review"
        CONFIRMED = "confirmed", "Confirmed"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="field_scans"
    )
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="field_scans")
    meter = models.ForeignKey(
        Meter, on_delete=models.SET_NULL, related_name="field_scans", null=True, blank=True
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    image = models.ImageField(upload_to=field_scan_path)
    raw_fields = models.JSONField(default=dict, blank=True)
    corrected_fields = models.JSONField(default=dict, blank=True)
    field_confidences = models.JSONField(default=dict, blank=True)
    duplicate_warnings = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="field_scans"
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_field_scans",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class StaffFormSubmission(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REVIEW = "review", "Review required"

    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="form_submissions")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="form_submissions"
    )
    answers = models.JSONField(default=dict)
    readings = models.JSONField(default=list, blank=True)
    comment = models.TextField(blank=True)
    issue_reason = models.CharField(max_length=240, blank=True)
    out_of_range_flags = models.JSONField(default=list, blank=True)
    idempotency_key = models.CharField(max_length=120)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"], name="unique_form_submission_idempotency"
            )
        ]


class FormCorrection(models.Model):
    submission = models.ForeignKey(
        StaffFormSubmission, on_delete=models.CASCADE, related_name="corrections"
    )
    original_values = models.JSONField(default=dict)
    corrected_values = models.JSONField(default=dict)
    reason = models.TextField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="form_corrections"
    )
    created_at = models.DateTimeField(auto_now_add=True)


class TaskAction(models.Model):
    class Action(models.TextChoices):
        REASSIGN = "reassign", "Reassign"
        REQUEST_ANOTHER = "request_another", "Request another submission"
        EXCEPTION = "exception", "Grant exception"
        ESCALATE = "escalate", "Escalate"
        OVERRIDE = "override", "Override AI result"

    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="actions")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="task_actions"
    )
    action = models.CharField(max_length=30, choices=Action.choices)
    reason = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DisputeRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="dispute_requests"
    )
    task = models.ForeignKey(
        Task, on_delete=models.SET_NULL, related_name="dispute_requests", null=True, blank=True
    )
    score_snapshot = models.ForeignKey(
        ScoreSnapshot, on_delete=models.SET_NULL, related_name="disputes", null=True, blank=True
    )
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_disputes",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)


class ESPDevice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="esp_devices"
    )
    site = models.ForeignKey(
        Site, on_delete=models.PROTECT, related_name="esp_devices", null=True, blank=True
    )
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="esp_devices", null=True, blank=True
    )
    name = models.CharField(max_length=120)
    token_hash = models.CharField(max_length=64)
    refresh_seconds = models.PositiveIntegerField(default=180)
    allow_top_employee = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
