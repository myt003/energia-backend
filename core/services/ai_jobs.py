from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.models import AIJob
from core.services.evidence import process_evidence
from core.services.invoices import process_invoice_scan


@transaction.atomic
def claim_next_job():
    job = (
        AIJob.objects.select_for_update(skip_locked=True)
        .filter(status=AIJob.Status.PENDING, available_at__lte=timezone.now())
        .order_by("created_at")
        .first()
    )
    if job is None:
        return None
    job.status = AIJob.Status.RUNNING
    job.attempts += 1
    job.locked_at = timezone.now()
    job.save(update_fields=["status", "attempts", "locked_at", "updated_at"])
    return job


def process_next_job(*, gateway=None):
    job = claim_next_job()
    if job is None:
        return None
    try:
        if job.evidence_id:
            if not hasattr(job.evidence, "verification_result"):
                process_evidence(job.evidence, gateway=gateway)
        elif job.invoice_scan_id:
            process_invoice_scan(job.invoice_scan, gateway=gateway)
        job.status = AIJob.Status.COMPLETED
        job.last_error = ""
        job.save(update_fields=["status", "last_error", "updated_at"])
    except Exception as exc:
        job.last_error = f"{type(exc).__name__}: {exc}"[:500]
        if job.attempts < 3:
            job.status = AIJob.Status.PENDING
            job.available_at = timezone.now() + timedelta(seconds=5 * job.attempts)
            job.save(
                update_fields=[
                    "status",
                    "available_at",
                    "last_error",
                    "updated_at",
                ]
            )
        else:
            job.status = AIJob.Status.FAILED
            job.save(update_fields=["status", "last_error", "updated_at"])
    return job
