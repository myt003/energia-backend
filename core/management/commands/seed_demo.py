import base64
import uuid
from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    ActivityType,
    Asset,
    AssetAssignment,
    AssetCandidate,
    Department,
    InvoiceSchema,
    Membership,
    Organization,
    RecommendationTemplate,
    RuleVersion,
    SafetyConstraint,
    Site,
    StaffWorkProfile,
    Task,
)
from core.services.habits import generate_energy_habits
from core.services.tasks import generate_daily_tasks


ASSETS = [
    ("cartoner-c12", "PKG-C12", "Cartoner C-12", "Packaging machine", "Packaging", "Packaging line 2", "CTN-12-8841", "63c6df4c-3fc5-4daa-a8d2-1cfbcb99d001"),
    ("conveyor-cv08", "PKG-CV08", "Conveyor CV-08", "Conveyor", "Packaging", "Packaging line 2", "CV08-2219", "63c6df4c-3fc5-4daa-a8d2-1cfbcb99d002"),
    ("compressor-ac04", "UTL-AC04", "Air compressor AC-04", "Air compressor", "Utilities", "Utilities room", "AC04-7725", "63c6df4c-3fc5-4daa-a8d2-1cfbcb99d003"),
    ("filler-f07", "PKG-F07", "Rotary filler F-07", "Filling machine", "Packaging", "Packaging line 1", "FLR-07-4612", "63c6df4c-3fc5-4daa-a8d2-1cfbcb99d004"),
    ("heat-sealer-hs03", "PKG-HS03", "Heat sealer HS-03", "Packaging machine", "Packaging", "Packaging line 3", "HS03-9084", "63c6df4c-3fc5-4daa-a8d2-1cfbcb99d005"),
    ("chiller-ch02", "UTL-CH02", "Process chiller CH-02", "Chiller", "Utilities", "North utility bay", "CH02-3350", "63c6df4c-3fc5-4daa-a8d2-1cfbcb99d006"),
    ("mixer-mx11", "PRD-MX11", "Mixing tank MX-11", "Process mixer", "Production", "Production hall A", "MX11-6407", "63c6df4c-3fc5-4daa-a8d2-1cfbcb99d007"),
    ("wrapper-pw05", "WHS-PW05", "Pallet wrapper PW-05", "Wrapping machine", "Warehouse", "Dispatch bay 2", "PW05-1186", "63c6df4c-3fc5-4daa-a8d2-1cfbcb99d008"),
    ("boiler-b01", "UTL-B01", "Steam boiler B-01", "Boiler", "Utilities", "Boiler room", "BLR-01-5302", "63c6df4c-3fc5-4daa-a8d2-1cfbcb99d009"),
]

ACTIVITIES = [
    ("line_startup", "Line start-up", "Pre-start checks and approved start sequence"),
    ("operate_line", "Operate production line", "Run and monitor equipment during production"),
    ("quality_checks", "Quality checks", "Inspect output and record process conditions"),
    ("changeover", "Product changeover", "Prepare equipment between production batches"),
    ("cleaning", "Cleaning", "Clean the line using approved procedures"),
    ("shift_shutdown", "End-of-shift shutdown", "Leave assigned equipment in its approved state"),
]


class Command(BaseCommand):
    help = "Create or update Energia's synchronized demo organization and data"

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        organization, _ = Organization.objects.update_or_create(
            slug="aster-foods",
            defaults={"name": "Aster Foods", "timezone": "UTC"},
        )
        site, _ = Site.objects.update_or_create(
            organization=organization,
            code="NORTH",
            defaults={"name": "North Plant", "timezone": "UTC"},
        )
        departments = {}
        for name, code in [
            ("Packaging", "PKG"),
            ("Production", "PRD"),
            ("Utilities", "UTL"),
            ("Warehouse", "WHS"),
        ]:
            department, _ = Department.objects.update_or_create(
                site=site, code=code, defaults={"name": name}
            )
            departments[name] = department

        maya, _ = User.objects.update_or_create(
            username="maya",
            defaults={
                "first_name": "Maya",
                "last_name": "Chen",
                "email": "maya@energia.demo",
                "is_active": True,
            },
        )
        maya.set_password("energia-demo")
        maya.save(update_fields=["password"])
        admin_user, _ = User.objects.update_or_create(
            username="energia-admin",
            defaults={
                "first_name": "Alex",
                "last_name": "Morgan",
                "email": "admin@energia.demo",
                "is_active": True,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        admin_user.set_password("Energia-Admin-Demo-2026!")
        admin_user.save(update_fields=["password"])

        maya_membership, _ = Membership.objects.update_or_create(
            user=maya,
            organization=organization,
            site=site,
            department=departments["Packaging"],
            role=Membership.Role.STAFF,
            defaults={"is_active": True},
        )
        Membership.objects.update_or_create(
            user=admin_user,
            organization=organization,
            site=site,
            department=None,
            role=Membership.Role.ADMIN,
            defaults={"is_active": True},
        )

        activity_records = {}
        for code, title, description in ACTIVITIES:
            activity, _ = ActivityType.objects.update_or_create(
                organization=organization,
                code=code,
                defaults={
                    "title": title,
                    "description": description,
                    "is_active": True,
                },
            )
            activity_records[code] = activity

        profile, _ = StaffWorkProfile.objects.update_or_create(
            user=maya,
            defaults={
                "membership": maya_membership,
                "department": departments["Packaging"],
                "job_role": "Production operator",
                "shift": StaffWorkProfile.Shift.DAY,
                "is_complete": True,
            },
        )
        profile.activities.set(
            [activity_records["line_startup"], activity_records["quality_checks"]]
        )

        InvoiceSchema.objects.update_or_create(
            organization=organization,
            name="Electricity bill · Standard",
            defaults={
                "supplier": "NorthGrid Energy",
                "energy_type": "electricity",
                "is_active": True,
                "field_schema": {
                    "fields": [
                        {"key": "account_number", "label": "Account number", "type": "text", "required": False},
                        {"key": "billing_period_start", "label": "Period start", "type": "date", "required": True},
                        {"key": "billing_period_end", "label": "Period end", "type": "date", "required": True},
                        {"key": "energy_value", "label": "Energy quantity", "type": "decimal", "required": True},
                        {"key": "energy_unit", "label": "Energy unit", "type": "text", "required": True},
                        {"key": "maximum_demand_kw", "label": "Maximum demand (kW)", "type": "decimal", "required": False},
                        {"key": "total_cost", "label": "Total cost", "type": "decimal", "required": True},
                        {"key": "currency", "label": "Currency", "type": "text", "required": True},
                        {"key": "tariff", "label": "Tariff", "type": "text", "required": False},
                    ]
                },
            },
        )

        assets = {}
        for client_id, code, name, asset_type, department_name, location, serial, qr in ASSETS:
            asset, _ = Asset.objects.update_or_create(
                organization=organization,
                client_id=client_id,
                defaults={
                    "site": site,
                    "department": departments[department_name],
                    "code": code,
                    "name": name,
                    "asset_type": asset_type,
                    "location": location,
                    "serial_number": serial,
                    "qr_identifier": uuid.UUID(qr),
                    "status": Asset.Status.ACTIVE,
                },
            )
            assets[client_id] = asset

        demo_assignment_ids = ["cartoner-c12", "heat-sealer-hs03"]
        AssetAssignment.objects.filter(user=maya).exclude(
            asset__client_id__in=demo_assignment_ids
        ).update(is_active=False)
        for asset_id in demo_assignment_ids:
            AssetAssignment.objects.update_or_create(
                user=maya,
                asset=assets[asset_id],
                defaults={"interactions": ["operate", "inspect"], "is_active": True},
            )

        rules = [
            {
                "code": "RULE-EN-0042",
                "version": 3,
                "title_template": "{department} shift energy walk",
                "instruction": "Check the approved shift route for idle equipment, leaks, and controls left outside their standard state.",
                "evidence_requirement": "Checklist · 5 answers",
                "pass_condition": "All route points checked; exceptions include a note.",
                "urgency": RuleVersion.Urgency.IMPORTANT,
                "source_title": "Aster Foods Energy Control Procedure",
                "source_version": "v4.2",
                "source_section": "Section 3.2 · Shift checks",
                "due_hour": 23,
                "due_minute": 30,
            },
            {
                "code": "RULE-OPS-0018",
                "version": 5,
                "title_template": "Record start-up energy condition",
                "instruction": "Confirm the line entered its approved operating state after start-up.",
                "required_activity_codes": ["line_startup"],
                "evidence_requirement": "Checklist · 3 answers",
                "pass_condition": "Approved start-up state confirmed.",
                "urgency": RuleVersion.Urgency.ROUTINE,
                "source_title": "Production Start-up Standard",
                "source_version": "v2.8",
                "source_section": "Energy checkpoint 2",
                "due_hour": 6,
                "due_minute": 30,
            },
            {
                "code": "RULE-ASSET-0024",
                "version": 2,
                "title_template": "Verify {asset} idle controls",
                "instruction": "Confirm the machine follows its approved idle-state instruction without changing protected settings.",
                "required_asset_types": [item[3] for item in ASSETS],
                "evidence_requirement": "Yes / no · Photo if failed",
                "pass_condition": "Approved idle state confirmed or exception reported.",
                "urgency": RuleVersion.Urgency.ROUTINE,
                "source_title": "Asset Idle Control Standard",
                "source_version": "v3.4",
                "source_section": "Table 2 · Equipment type",
                "due_hour": 23,
                "due_minute": 45,
            },
            {
                "code": "RULE-EVT-0007",
                "version": 4,
                "title_template": "Inspect {asset} after power anomaly",
                "instruction": "Visually check the accessible display and surrounding area. Report a potential issue; do not open, reset, or repair the machine.",
                "required_asset_types": ["Packaging machine"],
                "target_asset_client_ids": ["heat-sealer-hs03"],
                "event_trigger": "power_anomaly",
                "evidence_requirement": "Photo · display and full machine",
                "pass_condition": "Condition documented with no unsafe action taken.",
                "urgency": RuleVersion.Urgency.URGENT,
                "source_title": "Electrical Anomaly Response Guide",
                "source_version": "v2.0",
                "source_section": "Visual inspection only",
                "due_hour": 12,
                "due_minute": 0,
            },
            {
                "code": "RULE-EN-0031",
                "version": 2,
                "title_template": "Confirm previous-shift shutdown exceptions",
                "instruction": "Review the unresolved shutdown exception and confirm the affected area is in a safe approved state.",
                "event_trigger": "seed_history_shutdown",
                "evidence_requirement": "Yes / no · Comment if unresolved",
                "pass_condition": "Exception resolved or escalated with a reason.",
                "urgency": RuleVersion.Urgency.IMPORTANT,
                "source_title": "Aster Foods Shutdown Procedure",
                "source_version": "v3.1",
                "source_section": "Section 5 · Handover",
                "due_hour": 7,
                "due_minute": 45,
            },
            {
                "code": "RULE-EN-0055",
                "version": 1,
                "title_template": "Standby controls evidence",
                "instruction": "Evidence was submitted and is waiting for an authorized reviewer.",
                "event_trigger": "seed_history_review",
                "evidence_requirement": "Photo · 1 image",
                "pass_condition": "Reviewer confirms the approved standby state is visible.",
                "urgency": RuleVersion.Urgency.ROUTINE,
                "source_title": "Equipment Standby Standard",
                "source_version": "v1.6",
                "source_section": "Verification table A",
                "due_hour": 9,
                "due_minute": 0,
            },
        ]
        rule_records = {}
        for payload in rules:
            code = payload.pop("code")
            version = payload.pop("version")
            rule, _ = RuleVersion.objects.update_or_create(
                organization=organization,
                code=code,
                version=version,
                defaults={
                    **payload,
                    "is_published": True,
                    "published_at": timezone.now(),
                },
            )
            rule_records[code] = rule

        SafetyConstraint.objects.update_or_create(
            organization=organization,
            code="SAFETY-PKG-LOCKED-01",
            version=1,
            defaults={
                "title": "Production and approved shutdown sequence take priority",
                "instruction": "Do not power down, isolate, or alter packaging equipment outside the approved production and safety procedure.",
                "target_department": departments["Packaging"],
                "target_asset_types": ["Packaging machine"],
                "blocked_recommendation_tags": ["shutdown", "power_isolation"],
                "source_title": "Aster Foods Packaging Safety Procedure",
                "source_version": "v5.0",
                "source_section": "Locked rule 2 · Equipment state",
                "is_locked": True,
                "is_active": True,
            },
        )
        recommendations = [
            {
                "code": "HABIT-IDLE-001",
                "version": 1,
                "title_template": "Avoid unnecessary idle operation on {asset}",
                "instruction": "At an approved production pause, observe whether the machine is in its documented idle state. Report a mismatch; do not change protected controls.",
                "target_department": departments["Packaging"],
                "target_asset_types": ["Packaging machine"],
                "required_interactions": ["operate"],
                "recommendation_tags": ["idle_reduction"],
                "risk_level": RecommendationTemplate.RiskLevel.LOW,
                "evidence_requirement": "Yes / no · Note if state differs",
                "pass_condition": "Approved idle state observed or mismatch reported.",
                "owner_role": "Packaging team lead",
                "source_title": "Packaging Idle-State Guide",
                "source_version": "v2.1",
                "source_section": "Section 2 · Approved pauses",
                "safety_note": "Production and manufacturer procedures always take priority.",
                "due_hour": 23,
                "due_minute": 40,
            },
            {
                "code": "HABIT-STANDBY-002",
                "version": 1,
                "title_template": "Observe the approved standby state on {asset}",
                "instruction": "Visually confirm the accessible standby indicator after the batch. Do not change setpoints, open panels, reset, or repair equipment.",
                "target_department": departments["Packaging"],
                "target_asset_types": ["Packaging machine"],
                "required_interactions": ["inspect"],
                "recommendation_tags": ["standby_observation"],
                "risk_level": RecommendationTemplate.RiskLevel.LOW,
                "evidence_requirement": "Yes / no · Photo if indicator differs",
                "pass_condition": "Indicator observed in approved state or deviation reported.",
                "owner_role": "Energy coordinator",
                "source_title": "Equipment Standby Standard",
                "source_version": "v1.6",
                "source_section": "Verification table A",
                "safety_note": "Observation only; never alter protected settings.",
                "due_hour": 23,
                "due_minute": 42,
            },
            {
                "code": "HABIT-HVAC-003",
                "version": 1,
                "title_template": "Report simultaneous heating and cooling",
                "instruction": "During the normal quality round, report visible or displayed signs of simultaneous heating and cooling to the facilities owner. Do not adjust controlled setpoints.",
                "target_department": departments["Packaging"],
                "required_activity_codes": ["quality_checks"],
                "recommendation_tags": ["heating_cooling"],
                "risk_level": RecommendationTemplate.RiskLevel.LOW,
                "evidence_requirement": "Yes / no · Location if observed",
                "pass_condition": "Round completed and any potential conflict reported.",
                "owner_role": "Facilities energy manager",
                "source_title": "HVAC Energy Observation Guide",
                "source_version": "v1.3",
                "source_section": "Section 4 · Reportable conditions",
                "safety_note": "Report only; facilities controls remain restricted.",
                "due_hour": 23,
                "due_minute": 44,
            },
            {
                "code": "DEMO-CLASSROOM-001",
                "version": 1,
                "title_template": "Classroom demo: photograph an unoccupied room’s lighting state",
                "instruction": "From floor level, take one clear photo showing an unoccupied classroom and its visible ceiling-light state. Keep faces, names, student work, screens, and other personal information out of frame. Do not climb, open panels, touch wiring, or change protected controls.",
                "target_department": departments["Packaging"],
                "recommendation_tags": ["classroom_demo", "lighting_observation"],
                "risk_level": RecommendationTemplate.RiskLevel.LOW,
                "evidence_requirement": "Photo · unoccupied room and visible lights",
                "pass_condition": "A clear privacy-safe photo shows the unoccupied room and visible lighting state.",
                "owner_role": "Energia demo facilitator",
                "source_title": "Energia Classroom Validation Guide",
                "source_version": "v1.0",
                "source_section": "Photo evidence exercise",
                "safety_note": "Observation only. Do not photograph people or personal information, climb, or touch electrical equipment.",
                "due_hour": 23,
                "due_minute": 45,
            },
            {
                "code": "HABIT-SHUTDOWN-004",
                "version": 1,
                "title_template": "Power down {asset} during short pauses",
                "instruction": "Power down the machine during a short production pause.",
                "target_department": departments["Packaging"],
                "target_asset_types": ["Packaging machine"],
                "required_interactions": ["operate"],
                "recommendation_tags": ["shutdown"],
                "risk_level": RecommendationTemplate.RiskLevel.MEDIUM,
                "evidence_requirement": "Confirmation",
                "pass_condition": "Machine powered down.",
                "owner_role": "Packaging team lead",
                "source_title": "Generic Savings Draft",
                "source_version": "v1.0",
                "source_section": "Draft action 4",
                "safety_note": "Must be excluded when a locked procedure conflicts.",
                "due_hour": 23,
                "due_minute": 46,
            },
            {
                "code": "HABIT-AIR-005",
                "version": 1,
                "title_template": "Check compressed-air isolation at {asset}",
                "instruction": "At the approved end of use, confirm the accessible isolation indicator follows the documented procedure. Report leaks or differences; do not repair them.",
                "target_department": departments["Utilities"],
                "target_asset_types": ["Air compressor"],
                "required_interactions": ["operate", "inspect"],
                "recommendation_tags": ["compressed_air"],
                "risk_level": RecommendationTemplate.RiskLevel.MEDIUM,
                "evidence_requirement": "Yes / no · Note potential leak",
                "pass_condition": "Approved indicator state confirmed or issue reported.",
                "owner_role": "Utilities lead",
                "source_title": "Compressed Air Operating Standard",
                "source_version": "v3.0",
                "source_section": "Section 6 · End of use",
                "safety_note": "Never isolate equipment outside the approved procedure.",
                "due_hour": 23,
                "due_minute": 48,
            },
        ]
        for payload in recommendations:
            code = payload.pop("code")
            version = payload.pop("version")
            RecommendationTemplate.objects.update_or_create(
                organization=organization,
                code=code,
                version=version,
                defaults={
                    **payload,
                    "is_approved": True,
                    "approved_at": timezone.now(),
                },
            )

        now = timezone.now()
        generate_daily_tasks(user=maya, now=now, event_codes={"power_anomaly"})
        generate_energy_habits(user=maya, now=now)
        day = timezone.localdate(now)
        Task.objects.filter(
            assignee=maya,
            rule_code_snapshot="RULE-OPS-0018",
            generated_at__date=day,
        ).update(
            status=Task.Status.COMPLETED,
            completed_at=timezone.make_aware(datetime.combine(day, time(6, 18))),
        )
        self._historical_task(
            organization=organization,
            site=site,
            department=departments["Packaging"],
            user=maya,
            rule=rule_records["RULE-EN-0031"],
            day=day,
            status=Task.Status.OVERDUE,
            due_at=timezone.make_aware(
                datetime.combine(day - timedelta(days=1), time(7, 45))
            ),
            why=["Packaging handover", "Previous check failed"],
        )
        self._historical_task(
            organization=organization,
            site=site,
            department=departments["Packaging"],
            user=maya,
            rule=rule_records["RULE-EN-0055"],
            day=day,
            status=Task.Status.WAITING_REVIEW,
            due_at=timezone.make_aware(datetime.combine(day, time(9, 0))),
            submitted_at=timezone.make_aware(datetime.combine(day, time(8, 44))),
            why=["Production operator", "Evidence requires human review"],
        )

        candidate_id = uuid.UUID("90b89b68-5632-4a18-a72a-64c9fe0fd002")
        candidate, created = AssetCandidate.objects.get_or_create(
            id=candidate_id,
            defaults={
                "organization": organization,
                "site": site,
                "department": departments["Packaging"],
                "submitted_by": maya,
                "name": "Label printer LP-02",
                "asset_type": "Packaging machine",
                "location": "Packaging line 2",
                "identifier": "LP-02",
            },
        )
        if created or not candidate.photo:
            transparent_png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
            candidate.photo.save("label-printer-lp02.png", ContentFile(transparent_png))

        self._seed_admin_workspace(organization, site, departments, admin_user, maya)

        self.stdout.write(self.style.SUCCESS("Energia synchronized demo data is ready"))
        self.stdout.write("Staff login: maya / energia-demo")
        self.stdout.write("Admin login: energia-admin / Energia-Admin-Demo-2026!")

    def _seed_admin_workspace(self, organization, site, departments, admin_user, staff_user):
        from decimal import Decimal

        from core.models import (
            Alert,
            Baseline,
            CustomRule,
            DashboardView,
            ESPDevice,
            EnergyReview,
            EmissionFactor,
            EnergyUse,
            KnowledgeDocument,
            MaintenancePlan,
            Meter,
            MeterSchema,
            Notification,
            Recommendation,
            ScoreSnapshot,
            WeatherForecast,
            WorkOrder,
        )
        from core.services.intelligence import calculate_scores, canonical_energy, ingest_knowledge_document

        schema, _ = MeterSchema.objects.update_or_create(
            organization=organization,
            name="Industrial electricity",
            defaults={
                "field_schema": {
                    "registers": ["import_kwh", "maximum_demand_kw", "power_factor"],
                    "time_of_use": ["peak", "off_peak"],
                }
            },
        )
        base_factors = {"Packaging": 12500, "Production": 18600, "Utilities": 24300, "Warehouse": 6400}
        today = timezone.localdate()
        for index, (name, department) in enumerate(departments.items()):
            meter, _ = Meter.objects.update_or_create(
                organization=organization,
                code=f"M-{index + 1:03d}",
                defaults={
                    "site": site,
                    "department": department,
                    "name": f"{name} main meter",
                    "utility_type": "electricity",
                    "unit": "kWh",
                    "schema": schema,
                    "status": Meter.Status.ACTIVE,
                    "register_config": {"import_registers": 1},
                },
            )
            Baseline.objects.update_or_create(
                organization=organization,
                department=department,
                name=f"{name} 2026 baseline",
                defaults={
                    "site": site,
                    "period_start": today - timedelta(days=365),
                    "period_end": today - timedelta(days=30),
                    "value": base_factors[name],
                    "unit": "kWh",
                    "version": 1,
                    "approved_by": admin_user,
                    "approved_at": timezone.now(),
                },
            )
            for month in range(1, 7):
                period_end = today - timedelta(days=30 * month)
                period_start = period_end - timedelta(days=29)
                value = Decimal(str(base_factors[name] + ((month % 3) - 1) * 480))
                canonical, unit = canonical_energy(value, "kWh")
                from core.models import Reading

                Reading.objects.get_or_create(
                    organization=organization,
                    site=site,
                    department=department,
                    meter=meter,
                    period_start=period_start,
                    period_end=period_end,
                    source="manual",
                    defaults={
                        "value": value,
                        "unit": "kWh",
                        "canonical_value": canonical,
                        "canonical_unit": unit,
                        "cost": value * Decimal("0.18"),
                        "created_by": admin_user,
                    },
                )
            MaintenancePlan.objects.update_or_create(
                organization=organization,
                asset=Asset.objects.filter(department=department).first(),
                defaults={
                    "site": site,
                    "interval_days": 90,
                    "last_service_date": today - timedelta(days=82),
                    "next_due_date": today + timedelta(days=8),
                    "checklist": ["Visual inspection", "Verify operating state", "Record meter reading"],
                    "reason": "Deterministic 90-day service interval",
                },
            )

        compressors = Asset.objects.filter(name__icontains="compressor").first()
        if compressors:
            alert, _ = Alert.objects.get_or_create(
                organization=organization,
                site=site,
                department=compressors.department,
                asset=compressors,
                category="compressed_air",
                title="Potential compressed-air loss",
                defaults={
                    "description": "A potential issue was flagged from a verified photo sequence. A qualified person must confirm before any repair is ordered.",
                    "severity": "warning",
                    "confidence": 0.82,
                    "source_refs": ["deterministic demo summary"],
                },
            )
            Recommendation.objects.get_or_create(
                organization=organization,
                alert=alert,
                title="Schedule a qualified compressed-air inspection",
                defaults={
                    "site": site,
                    "asset": compressors,
                    "category": "compressed_air",
                    "rationale": "Potential loss was supported by evidence but requires professional confirmation.",
                    "safe_next_check": "Confirm with calibrated instruments during the next planned inspection.",
                    "evidence_needed": "Inspection record and before/after reading",
                    "urgency": "important",
                },
            )
            WorkOrder.objects.get_or_create(
                organization=organization,
                title="Inspect compressed-air system AC-04",
                defaults={
                    "site": site,
                    "asset": compressors,
                    "description": "Qualified inspection of potential loss indicated by evidence review.",
                    "owner": "Maintenance lead",
                    "due_date": today + timedelta(days=7),
                    "status": WorkOrder.Status.OPEN,
                    "created_by": admin_user,
                },
            )

        document, _ = KnowledgeDocument.objects.get_or_create(
            organization=organization,
            title="Aster Foods internal energy check procedure",
            defaults={
                "source": "Aster Foods internal procedure",
                "version": "1.0",
                "status": KnowledgeDocument.Status.DRAFT,
                "mime_type": "text/plain",
                "created_by": admin_user,
                "extracted_text": (
                    "Operators must verify that compressed-air isolation valves are closed after the "
                    "approved shutdown sequence. Report any visible air loss to maintenance. Do not "
                    "override safety or manufacturer procedures. Record the meter reading at the end "
                    "of each shift and flag implausible values for review."
                ),
            },
        )
        if not document.chunks.exists():
            ingest_knowledge_document(document)

        rule, _ = CustomRule.objects.get_or_create(
            organization=organization,
            code="CUSTOM-ENERGY-001",
            version=1,
            defaults={
                "title": "End-of-shift meter verification",
                "definition": {
                    "title": "End-of-shift meter verification",
                    "instruction": "Record the end-of-shift meter reading and flag implausible values.",
                    "department_id": departments["Utilities"].id,
                    "roles": ["staff", "team_lead"],
                    "evidence_requirement": "meter_photo",
                    "pass_condition": "Reading is plausible and linked to the correct meter",
                    "source_title": "Aster Foods internal energy check procedure",
                },
                "status": CustomRule.Status.PUBLISHED,
                "created_by": admin_user,
                "approved_by": admin_user,
                "published_at": timezone.now(),
            },
        )
        rule.preview_summary = {"matches": 8, "staff_in_scope": 3, "warnings": []}
        rule.save(update_fields=["preview_summary"])

        review, _ = EnergyReview.objects.get_or_create(
            organization=organization,
            site=site,
            title="2026 significant energy uses",
            defaults={
                "period_start": today - timedelta(days=365),
                "period_end": today,
                "criteria": {"threshold_percent": 10},
                "status": EnergyReview.Status.APPROVED,
                "approved_by": admin_user,
                "approved_at": timezone.now(),
                "created_by": admin_user,
            },
        )
        for name, department in departments.items():
            EnergyUse.objects.get_or_create(
                review=review,
                name=f"{name} energy use",
                defaults={
                    "utility_type": "electricity",
                    "annual_energy": base_factors[name],
                    "unit": "kWh",
                    "contribution": base_factors[name] / sum(base_factors.values()) * 100,
                    "is_significant": base_factors[name] >= 12000,
                    "owner_role": f"{name} supervisor",
                    "rationale": "Ranked by approved annual consumption share.",
                },
            )

        EmissionFactor.objects.get_or_create(
            organization=organization,
            source="Demo grid factor",
            region="Demo region",
            utility_type="electricity",
            effective_from=today - timedelta(days=365),
            defaults={
                "value": Decimal("0.42"),
                "unit": "kgCO2e/kWh",
                "market_method": True,
                "source_reference": "Internal demo factor; replace before production",
                "version": 1,
            },
        )

        DashboardView.objects.get_or_create(
            organization=organization,
            user=admin_user,
            name="Plant overview",
            defaults={
                "scope": {"site_id": site.id},
                "chart_config": {"charts": ["consumption", "health", "flow"]},
                "is_default": True,
            },
        )
        ESPDevice.objects.get_or_create(
            organization=organization,
            name="Lobby display",
            defaults={
                "site": site,
                "token_hash": __import__("hashlib").sha256(b"energia-demo-device-token").hexdigest(),
                "refresh_seconds": 180,
                "allow_top_employee": True,
            },
        )
        Notification.objects.get_or_create(
            organization=organization,
            title="New invoice draft ready for review",
            defaults={
                "body": "The classroom energy bill extraction is ready for administrator confirmation.",
                "category": Notification.Category.SYSTEM,
                "severity": Notification.Severity.INFO,
                "action_url": "/admin/invoices",
            },
        )
        forecast_base = timezone.now().replace(minute=0, second=0, microsecond=0)
        for hours in (6, 12, 18):
            WeatherForecast.objects.get_or_create(
                site=site,
                forecast_for=forecast_base + timedelta(hours=hours),
                source="demo",
                defaults={
                    "organization": organization,
                    "temperature_c": 31.5 if hours == 12 else 26,
                    "humidity": 68,
                    "peak_alert": hours == 12,
                    "recommended_preparation": "Review cooling set-points before the afternoon peak.",
                },
            )
        calculate_scores(organization, "month", admin_user)
        ScoreSnapshot.objects.filter(
            organization=organization, user__isnull=False, top_employee=True
        ).update(top_employee=False)
        top = (
            ScoreSnapshot.objects.filter(organization=organization, user__isnull=False)
            .order_by("-overall_score", "-created_at")
            .first()
        )
        if top:
            top.top_employee = True
            top.save(update_fields=["top_employee"])

    def _historical_task(
        self,
        *,
        organization,
        site,
        department,
        user,
        rule,
        day,
        status,
        due_at,
        why,
        submitted_at=None,
    ):
        Task.objects.update_or_create(
            unique_key=f"seed:{organization.id}:{user.id}:{day}:{rule.code}:{status}",
            defaults={
                "organization": organization,
                "site": site,
                "department": department,
                "assignee": user,
                "rule_version": rule,
                "rule_code_snapshot": rule.code,
                "rule_version_snapshot": rule.version,
                "title": rule.title_template,
                "instruction": rule.instruction,
                "evidence_requirement": rule.evidence_requirement,
                "pass_condition": rule.pass_condition,
                "urgency": rule.urgency,
                "source_title": rule.source_title,
                "source_version": rule.source_version,
                "source_section": rule.source_section,
                "why_assigned": why,
                "due_at": due_at,
                "status": status,
                "submitted_at": submitted_at,
            },
        )
