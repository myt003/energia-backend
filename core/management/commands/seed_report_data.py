"""Seed report-ready demo data for the current 30-day reporting window.

Idempotent: re-running updates the generated records instead of duplicating them.
"""

import math
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    AuditFinding,
    AuditPlan,
    Baseline,
    Department,
    EmissionFactor,
    EmissionRecord,
    EnPI,
    Organization,
    Reading,
    ScoreSnapshot,
    Site,
    Task,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Seed readings, tasks, scores, EnPIs, emissions, and audit findings for reports"

    def handle(self, *args, **options):
        organization = Organization.objects.first()
        if organization is None:
            self.stderr.write("No organization found; run seed_demo first.")
            return
        admin = User.objects.filter(username="energia-admin").first() or User.objects.filter(
            is_superuser=True
        ).first()
        site = Site.objects.filter(organization=organization).first()
        if admin is None or site is None:
            self.stderr.write("Missing admin user or site; run seed_demo first.")
            return

        end = timezone.localdate()
        start = end - timedelta(days=29)
        departments = list(Department.objects.filter(site=site).order_by("name"))

        # 1. Daily electricity readings for the reporting window.
        reading_count = 0
        for day_offset in range(30):
            day = start + timedelta(days=day_offset)
            for dept in departments:
                baseline = (
                    Baseline.objects.filter(organization=organization, department=dept)
                    .order_by("-version")
                    .first()
                )
                daily_baseline = float(baseline.value) / 30 if baseline else 400
                variation = 1 + 0.045 * math.sin(day_offset * 0.7 + dept.id)
                canonical = round(daily_baseline * variation, 3)
                cost = round(canonical * 0.18, 2)
                Reading.objects.update_or_create(
                    organization=organization,
                    site=site,
                    department=dept,
                    source=Reading.Source.SYSTEM,
                    utility_type="electricity",
                    period_start=day,
                    period_end=day,
                    defaults={
                        "value": Decimal(str(canonical)),
                        "unit": "kWh",
                        "canonical_value": Decimal(str(canonical)),
                        "canonical_unit": "kWh",
                        "cost": Decimal(str(cost)),
                        "currency": "USD",
                        "recorded_at": timezone.make_aware(datetime.combine(day, time(9, 0))),
                        "quality_flags": [],
                        "created_by": admin,
                    },
                )
                reading_count += 1

        # 2. Completed staff tasks so the report and employee score look real.
        template = Task.objects.filter(organization=organization, assignee__username="maya").first()
        task_count = 0
        if template:
            for index in range(16):
                due_day = start + timedelta(days=(index * 2) % 30)
                due_at = timezone.make_aware(datetime.combine(due_day, time(15, 0)))
                Task.objects.update_or_create(
                    unique_key=f"report-seed-completed-{index + 1:02d}",
                    defaults={
                        "organization": organization,
                        "site": template.site,
                        "department": template.department,
                        "assignee": template.assignee,
                        "rule_version": template.rule_version,
                        "recommendation_template": template.recommendation_template,
                        "task_kind": template.task_kind,
                        "rule_code_snapshot": template.rule_code_snapshot,
                        "rule_version_snapshot": template.rule_version_snapshot,
                        "title": f"Verified check · {template.department.name}",
                        "instruction": "Confirm the check against the approved procedure.",
                        "evidence_requirement": "Photo or meter reading",
                        "pass_condition": "Evidence matches the approved state.",
                        "urgency": template.urgency,
                        "source_title": template.source_title,
                        "source_version": template.source_version,
                        "source_section": template.source_section,
                        "why_assigned": ["Seeded reporting demonstration"],
                        "due_at": due_at,
                        "status": Task.Status.COMPLETED,
                        "completed_at": due_at + timedelta(hours=1),
                        "submitted_at": due_at + timedelta(minutes=45),
                    },
                )
                task_count += 1

            # Keep two overdue items open for realism and close the rest.
            overdue = Task.objects.filter(
                organization=organization,
                assignee__username="maya",
                status=Task.Status.OVERDUE,
                due_at__date__gte=start,
                due_at__date__lte=end,
            ).order_by("due_at")
            for task in list(overdue)[:-2]:
                task.status = Task.Status.COMPLETED
                task.completed_at = task.due_at + timedelta(hours=1)
                task.submitted_at = task.due_at + timedelta(minutes=45)
                task.save(update_fields=["status", "completed_at", "submitted_at"])
                task_count += 1

        period_readings = Reading.objects.filter(
            organization=organization, period_start__gte=start, period_end__lte=end
        )
        period_tasks = Task.objects.filter(
            organization=organization, due_at__date__gte=start, due_at__date__lte=end
        )

        # 3. Top employee snapshot. Created before department snapshots so the
        # device summary resolves the newest department score correctly.
        maya = User.objects.filter(username="maya").first()
        if maya:
            user_tasks = period_tasks.filter(assignee=maya)
            completed = user_tasks.filter(status=Task.Status.COMPLETED).count()
            overdue = user_tasks.filter(status=Task.Status.OVERDUE).count()
            total = user_tasks.count()
            if total:
                user_score = max(0, min(100, round(completed / total * 100 - overdue * 3, 1)))
                ScoreSnapshot.objects.update_or_create(
                    organization=organization,
                    site=site,
                    user=maya,
                    period_start=start,
                    period_end=end,
                    defaults={
                        "department": user_tasks.first().department,
                        "display_name": maya.get_full_name() or maya.username,
                        "overall_score": user_score,
                        "check_component": user_score,
                        "data_coverage": round(min(1, total / 8), 2),
                        "formula_version": "2026-11-v1",
                        "inputs": {"tasks": total, "completed": completed, "overdue": overdue},
                        "provisional": total < 5,
                        "top_employee": False,
                    },
                )

        # 4. Department score snapshots using the production formula. Recreate
        # them so their created_at stays newer than the employee snapshot.
        ScoreSnapshot.objects.filter(
            organization=organization,
            site=site,
            department__in=departments,
            user__isnull=True,
            period_start=start,
            period_end=end,
        ).delete()
        score_count = 0
        for dept in departments:
            dept_readings = period_readings.filter(department=dept)
            dept_tasks = period_tasks.filter(department=dept)
            baseline = (
                Baseline.objects.filter(organization=organization, department=dept)
                .order_by("-version")
                .first()
            )
            energy_component = None
            provisional = False
            if baseline and dept_readings.exists() and float(baseline.value) > 0:
                actual = sum(float(item.canonical_value) for item in dept_readings)
                ratio = actual / float(baseline.value)
                energy_component = max(0, min(100, 100 - abs(ratio - 1) * 100))
            else:
                provisional = True
            verified = dept_tasks.filter(status=Task.Status.COMPLETED).count()
            failed = dept_tasks.filter(status=Task.Status.OVERDUE).count()
            total = dept_tasks.count()
            check_component = (verified / total * 100) if total else 60
            check_component = max(0, min(100, check_component - failed * 2))
            coverage = min(1, (dept_readings.count() / 4) * 0.5 + (total / 8) * 0.5)
            if energy_component is None:
                energy_component = 65
            overall = round(energy_component * 0.6 + check_component * 0.4, 1)
            ScoreSnapshot.objects.update_or_create(
                organization=organization,
                site=site,
                department=dept,
                user=None,
                period_start=start,
                period_end=end,
                defaults={
                    "overall_score": overall,
                    "energy_component": round(energy_component, 1),
                    "check_component": round(check_component, 1),
                    "data_coverage": round(coverage, 2),
                    "formula_version": "2026-11-v1",
                    "inputs": {
                        "readings": dept_readings.count(),
                        "tasks": total,
                        "verified": verified,
                        "overdue": failed,
                        "baseline_id": baseline.id if baseline else None,
                    },
                    "provisional": provisional or coverage < 0.5,
                },
            )
            score_count += 1

        # 5. EnPI, emissions, and audit findings for the report sections.
        baseline = Baseline.objects.filter(organization=organization).order_by("-version").first()
        enpi, _ = EnPI.objects.get_or_create(
            organization=organization,
            name="Electricity intensity",
            version=1,
            defaults={
                "site": site,
                "formula": {"op": "divide", "numerator": "electricity_kwh", "denominator": "units"},
                "unit": "kWh/unit",
                "variables": ["electricity_kwh", "units"],
                "baseline": baseline,
                "is_approved": True,
            },
        )

        factor = EmissionFactor.objects.filter(organization=organization).first()
        if factor:
            total_kwh = sum(float(item.canonical_value) for item in period_readings)
            co2e = Decimal(str(round(total_kwh * float(factor.value) / 1000, 3)))
            EmissionRecord.objects.update_or_create(
                organization=organization,
                site=site,
                factor=factor,
                period_start=start,
                period_end=end,
                defaults={
                    "quantity": Decimal(str(round(total_kwh, 3))),
                    "canonical_unit": "kWh",
                    "co2e_tonnes": co2e,
                    "method": "market",
                    "inputs": {"source": "report seed", "enpi_id": enpi.id},
                },
            )

        audit, _ = AuditPlan.objects.get_or_create(
            organization=organization,
            title="Energy management system readiness review",
            defaults={
                "site": site,
                "period_start": start,
                "period_end": end,
                "scope": {"standard": "ISO 50001", "site": site.name},
                "status": AuditPlan.Status.IN_PROGRESS,
                "created_by": admin,
            },
        )
        for index, (requirement, severity, reference) in enumerate(
            [
                ("Meters are calibrated and traceable", "minor", "ISO 50001 6.6.1"),
                ("Energy baseline is approved and versioned", "major", "ISO 50001 6.3"),
                ("Corrective actions are tracked to closure", "minor", "ISO 50001 10.2"),
            ],
            start=1,
        ):
            AuditFinding.objects.get_or_create(
                audit=audit,
                requirement=requirement,
                defaults={
                    "description": f"Demo finding for {requirement.lower()}.",
                    "finding_type": "observation" if severity == "minor" else "nonconformity",
                    "severity": severity,
                    "iso_reference": reference,
                    "status": AuditFinding.Status.ACTION,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {reading_count} readings, {task_count} completed tasks, "
                f"{score_count} department snapshots, 1 EnPI, 1 emission record, "
                f"and 3 audit findings for {start} to {end}."
            )
        )
