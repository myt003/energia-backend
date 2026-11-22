from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from core.models import AssetAssignment, AuditEvent, RuleVersion, Task
from core.services.scope import work_profile_for


def _rule_matches_profile(rule, profile):
    if rule.target_department_id and rule.target_department_id != profile.department_id:
        return False
    if rule.target_roles and profile.job_role not in rule.target_roles:
        return False
    if rule.required_activity_codes:
        selected_codes = set(profile.activities.values_list("code", flat=True))
        if not selected_codes.intersection(rule.required_activity_codes):
            return False
    return True


def _due_at(rule, day):
    naive = datetime.combine(day, time(rule.due_hour, rule.due_minute))
    return timezone.make_aware(naive, timezone.get_current_timezone())


@transaction.atomic
def generate_daily_tasks(*, user, now=None, event_codes=None):
    now = now or timezone.now()
    event_codes = set(event_codes or [])
    profile = work_profile_for(user)
    membership = profile.membership
    day = timezone.localdate(now)
    rules = RuleVersion.objects.filter(
        organization=membership.organization,
        is_published=True,
    ).select_related("target_department")
    assignments = list(
        AssetAssignment.objects.filter(
            user=user,
            is_active=True,
            asset__organization=membership.organization,
            asset__status="active",
        ).select_related("asset__department")
    )
    created_tasks = []
    expected_keys = set()
    for rule in rules:
        if not _rule_matches_profile(rule, profile):
            continue
        if rule.event_trigger and rule.event_trigger not in event_codes:
            continue
        matching_assignments = []
        if rule.required_asset_types:
            matching_assignments = [
                assignment
                for assignment in assignments
                if assignment.asset.asset_type in rule.required_asset_types
                and (
                    not rule.target_asset_client_ids
                    or assignment.asset.client_id in rule.target_asset_client_ids
                )
            ]
            if not matching_assignments:
                continue
        else:
            matching_assignments = [None]

        for assignment in matching_assignments:
            asset = assignment.asset if assignment else None
            unique_key = ":".join(
                [
                    str(membership.organization_id),
                    str(user.id),
                    day.isoformat(),
                    rule.code,
                    str(rule.version),
                    asset.client_id if asset else "department",
                    rule.event_trigger or "scheduled",
                ]
            )
            expected_keys.add(unique_key)
            why_assigned = [
                f"{profile.department.name} department",
                profile.job_role,
                profile.get_shift_display(),
            ]
            if asset:
                why_assigned.insert(0, f"{asset.name} assignment")
            if rule.event_trigger:
                why_assigned.append(f"{rule.event_trigger.replace('_', ' ').title()} trigger")
            title = rule.title_template.format(
                department=profile.department.name,
                asset=asset.name if asset else "",
            ).strip()
            task, created = Task.objects.get_or_create(
                unique_key=unique_key,
                defaults={
                    "organization": membership.organization,
                    "site": membership.site,
                    "department": asset.department if asset else profile.department,
                    "assignee": user,
                    "asset": asset,
                    "rule_version": rule,
                    "rule_code_snapshot": rule.code,
                    "rule_version_snapshot": rule.version,
                    "title": title,
                    "instruction": rule.instruction,
                    "evidence_requirement": rule.evidence_requirement,
                    "pass_condition": rule.pass_condition,
                    "urgency": rule.urgency,
                    "source_title": rule.source_title,
                    "source_version": rule.source_version,
                    "source_section": rule.source_section,
                    "why_assigned": why_assigned,
                    "due_at": now + timedelta(minutes=45)
                    if rule.event_trigger
                    else _due_at(rule, day),
                    "event_triggered": bool(rule.event_trigger),
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
            task_kind=Task.Kind.COMPLIANCE,
            status=Task.Status.DUE,
            generated_at__date=day,
            rule_version__isnull=False,
        )
        .exclude(unique_key__in=expected_keys)
        .update(status=Task.Status.CANCELLED)
    )

    if created_tasks or cancelled:
        AuditEvent.objects.create(
            organization=membership.organization,
            actor=user,
            action="tasks.generated",
            object_type="TaskBatch",
            object_id=day.isoformat(),
            metadata={
                "task_ids": [str(task.id) for task in created_tasks],
                "cancelled_count": cancelled,
            },
        )
    return created_tasks


def refresh_overdue_tasks(*, user, now=None):
    now = now or timezone.now()
    return Task.objects.filter(
        assignee=user,
        status=Task.Status.DUE,
        due_at__lt=now,
        event_triggered=False,
    ).update(status=Task.Status.OVERDUE)
