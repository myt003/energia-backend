from rest_framework.exceptions import PermissionDenied

from core.models import Membership, StaffWorkProfile


def active_membership_for(user):
    membership = (
        Membership.objects.select_related("organization", "site", "department")
        .filter(user=user, is_active=True)
        .order_by("id")
        .first()
    )
    if membership is None:
        raise PermissionDenied("No active Energia membership is assigned")
    return membership


def work_profile_for(user):
    try:
        return StaffWorkProfile.objects.select_related(
            "membership__organization",
            "membership__site",
            "department",
            "pending_department",
        ).prefetch_related("activities", "custom_activities").get(user=user)
    except StaffWorkProfile.DoesNotExist as exc:
        raise PermissionDenied("No Energia work profile is assigned") from exc
