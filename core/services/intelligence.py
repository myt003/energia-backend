"""Shared energy-intelligence services used by the synchronized admin workspace."""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db.models import Count, Q
from django.utils import timezone

from core.models import (
    Alert,
    AuditEvent,
    AuditFinding,
    Baseline,
    CustomRule,
    Department,
    EmissionRecord,
    EnPI,
    FieldScan,
    ImportBatch,
    ImportRow,
    KnowledgeChunk,
    KnowledgeDocument,
    MaintenancePlan,
    Meter,
    Reading,
    RuleVersion,
    ScoreSnapshot,
    Site,
    Task,
    WeatherForecast,
    WorkOrder,
)
from core.services.model_gateway import ModelGateway, ModelGatewayUnavailable

TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "that", "the", "to", "with", "shall", "must",
}
UNIT_FACTORS = {
    "kwh": ("kWh", Decimal("1")),
    "mwh": ("kWh", Decimal("1000")),
    "wh": ("kWh", Decimal("0.001")),
    "m3": ("m3", Decimal("1")),
    "m³": ("m3", Decimal("1")),
    "therm": ("kWh", Decimal("29.3071")),
    "litre": ("L", Decimal("1")),
    "liter": ("L", Decimal("1")),
    "l": ("L", Decimal("1")),
    "gj": ("kWh", Decimal("277.7778")),
}


def audit(organization, actor, action, object_type, object_id, metadata=None):
    AuditEvent.objects.create(
        organization=organization,
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        metadata=metadata or {},
    )


def canonical_energy(value, unit):
    normalized = (unit or "").strip().lower()
    canonical_unit, factor = UNIT_FACTORS.get(normalized, (normalized or "unit", Decimal("1")))
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        numeric = Decimal("0")
    return numeric * factor, canonical_unit


def extract_document_text(path, filename, mime_type=""):
    suffix = Path(filename or path).suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            pages.append((index, page.extract_text() or ""))
        return "\n\n".join(text for _, text in pages), pages
    if suffix in {".docx", ".doc"}:
        from docx import Document

        document = Document(str(path))
        paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    paragraphs.append(" | ".join(cells))
        text = "\n".join(paragraphs)
        return text, [(None, text)]
    data = Path(path).read_bytes()
    text = data.decode("utf-8", errors="replace")
    return text, [(None, text)]


def _chunks(text, size=900, overlap=120):
    words = text.split()
    if not words:
        return []
    result = []
    start = 0
    while start < len(words):
        chunk = " ".join(words[start : start + size])
        if chunk.strip():
            result.append(chunk)
        if start + size >= len(words):
            break
        start += max(1, size - overlap)
    return result


def ingest_knowledge_document(document):
    try:
        if document.file:
            text, pages = extract_document_text(
                document.file.path, document.file.name, document.mime_type
            )
        else:
            text = document.extracted_text or ""
            pages = [(None, text)]
        document.extracted_text = text[:2_000_000]
        document.status = KnowledgeDocument.Status.ACTIVE
        document.ingestion_error = ""
        document.chunks.all().delete()
        chunk_index = 0
        for page, page_text in pages:
            for chunk in _chunks(page_text):
                tokens = sorted(set(TOKEN_RE.findall(chunk.lower())) - STOP_WORDS)
                KnowledgeChunk.objects.create(
                    document=document,
                    chunk_index=chunk_index,
                    section=f"Page {page}" if page else "Section",
                    page=page,
                    text=chunk,
                    keywords=tokens[:200],
                    checksum_sha256=hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                )
                chunk_index += 1
        document.chunk_count = chunk_index
        document.save(update_fields=["extracted_text", "status", "ingestion_error", "chunk_count"])
        return document
    except Exception as exc:  # keep the source and expose the ingestion error
        document.status = KnowledgeDocument.Status.ERROR
        document.ingestion_error = f"{type(exc).__name__}: {exc}"[:1000]
        document.save(update_fields=["status", "ingestion_error"])
        return document


def _query_tokens(query):
    return [token for token in TOKEN_RE.findall(query.lower()) if token not in STOP_WORDS]


def rag_retrieve(organization, query, *, top_k=5, site_id=None):
    tokens = _query_tokens(query)
    chunks = KnowledgeChunk.objects.filter(
        document__organization=organization,
        document__status=KnowledgeDocument.Status.ACTIVE,
        is_active=True,
    )
    if site_id:
        chunks = chunks.filter(Q(document__site_id=site_id) | Q(document__site__isnull=True))
    ranked = []
    for chunk in chunks.select_related("document"):
        haystack = f"{chunk.text} {chunk.section} {chunk.document.title}".lower()
        score = sum(1 for token in tokens if token in haystack)
        if not tokens:
            score = 1
        if score:
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "chunk_id": str(chunk.id),
            "score": score,
            "text": chunk.text,
            "citation": {
                "document_id": str(chunk.document_id),
                "title": chunk.document.title,
                "source": chunk.document.source,
                "version": chunk.document.version,
                "section": chunk.section,
                "page": chunk.page,
            },
        }
        for score, chunk in ranked[:top_k]
    ]


def rag_answer(organization, actor, query, *, site_id=None, use_model=True):
    citations = rag_retrieve(organization, query, site_id=site_id)
    if not citations:
        return {
            "answer": "No reliable approved source was found for this question.",
            "citations": [],
            "grounded": False,
            "model": None,
        }
    context = "\n\n".join(
        f"[{index + 1}] {item['citation']['title']} v{item['citation']['version']} "
        f"{item['citation']['section']}: {item['text']}"
        for index, item in enumerate(citations)
    )
    if use_model:
        gateway = ModelGateway()
        try:
            result, request_id = gateway.structured_completion(
                organization=organization,
                actor=actor,
                purpose="rag.grounded_answer",
                system_prompt=(
                    "You answer only from the supplied approved citations. If the citations "
                    "do not contain the answer, say so. Do not invent requirements. Return "
                    'JSON with keys "answer" and "citation_numbers".'
                ),
                user_prompt=f"Question: {query}\n\nApproved citations:\n{context}",
                json_schema={
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                        "citation_numbers": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["answer", "citation_numbers"],
                    "additionalProperties": False,
                },
            )
            return {
                "answer": str(result.get("answer", ""))[:4000],
                "citations": citations,
                "grounded": True,
                "model": gateway.model,
                "provider_request_id": request_id,
            }
        except (ModelGatewayUnavailable, Exception):
            pass
    summary = citations[0]["text"]
    return {
        "answer": f"Approved source: {summary[:1200]}",
        "citations": citations,
        "grounded": True,
        "model": None,
        "fallback": True,
    }


def preview_rule(organization, definition):
    department_id = definition.get("department_id")
    roles = definition.get("roles") or []
    asset_types = definition.get("asset_types") or []
    queryset = Task.objects.filter(organization=organization)
    if department_id:
        queryset = queryset.filter(department_id=department_id)
    if roles:
        queryset = queryset.filter(assignee__memberships__role__in=roles)
    if asset_types:
        queryset = queryset.filter(asset__asset_type__in=asset_types)
    count = queryset.distinct().count()
    staff = (
        Task.objects.filter(organization=organization)
        .values("assignee_id")
        .distinct()
        .count()
    )
    return {
        "matches": count,
        "staff_in_scope": staff,
        "estimated_tasks_per_day": math.ceil(count / max(1, staff)) if staff else 0,
        "warnings": [] if count < staff * 8 else ["High projected task volume"],
        "definition": definition,
    }


def publish_rule(rule, actor):
    definition = rule.definition or {}
    title_template = definition.get("title") or rule.title
    version = RuleVersion.objects.create(
        organization=rule.organization,
        code=rule.code,
        version=rule.version,
        title_template=title_template[:240],
        instruction=definition.get("instruction", "") or rule.title,
        target_department_id=definition.get("department_id"),
        target_roles=definition.get("roles") or [],
        required_activity_codes=definition.get("activity_codes") or [],
        required_asset_types=definition.get("asset_types") or [],
        target_asset_client_ids=definition.get("asset_codes") or [],
        event_trigger=definition.get("event_trigger", ""),
        evidence_requirement=definition.get("evidence_requirement", "photo"),
        pass_condition=definition.get("pass_condition", "Administrator review"),
        urgency=definition.get("urgency", "routine"),
        source_title=definition.get("source_title", rule.title),
        source_version=definition.get("source_version", str(rule.version)),
        source_section=definition.get("source_section", "Custom rule"),
        due_hour=int(definition.get("due_hour", 12)),
        due_minute=int(definition.get("due_minute", 0)),
        is_published=True,
        published_at=timezone.now(),
    )
    rule.status = CustomRule.Status.PUBLISHED
    rule.published_at = timezone.now()
    rule.approved_by = actor
    rule.preview_summary = preview_rule(rule.organization, definition)
    rule.save(update_fields=["status", "published_at", "approved_by", "preview_summary"])
    audit(rule.organization, actor, "rule.published", "customrule", rule.id, {"version": rule.version})
    return version


def create_reading(organization, actor, payload, *, source="manual"):
    meter_id = payload.get("meter_id")
    meter = Meter.objects.filter(id=meter_id, organization=organization).first() if meter_id else None
    site_id = payload.get("site_id") or (meter.site_id if meter else None)
    if not site_id:
        raise ValueError("site_id is required")
    value, unit = canonical_energy(payload.get("value"), payload.get("unit", "kWh"))
    previous = None
    if meter_id:
        previous = Reading.objects.filter(meter_id=meter_id, organization=organization).order_by("-period_end").first()
    revision = 1
    if payload.get("corrects_reading_id"):
        previous = Reading.objects.filter(
            id=payload["corrects_reading_id"], organization=organization
        ).first()
        revision = (previous.revision + 1) if previous else 1
    flags = list(payload.get("quality_flags") or [])
    if previous is not None and value < Decimal("0"):
        flags.append("negative_value")
    if previous is not None and payload.get("value") is not None:
        try:
            delta = value - Decimal(str(previous.canonical_value))
            if Decimal(str(previous.canonical_value)) != 0 and abs(delta) / abs(
                Decimal(str(previous.canonical_value))
            ) > Decimal("1.5"):
                flags.append("large_delta_review")
        except (InvalidOperation, TypeError):
            pass
    return Reading.objects.create(
        organization=organization,
        site_id=site_id,
        department_id=payload.get("department_id") or (meter.department_id if meter else None),
        meter=meter,
        source=source,
        utility_type=payload.get("utility_type") or (meter.utility_type if meter else "electricity"),
        value=payload.get("value", 0),
        unit=payload.get("unit", "kWh"),
        canonical_value=value,
        canonical_unit=unit,
        cost=payload.get("cost"),
        currency=payload.get("currency", "USD"),
        period_start=payload["period_start"],
        period_end=payload["period_end"],
        recorded_at=payload.get("recorded_at"),
        quality_flags=flags,
        revision=revision,
        correction_reason=payload.get("correction_reason", ""),
        previous=previous,
        field_scan_id=payload.get("field_scan_id"),
        created_by=actor,
    )


def stage_import(organization, actor, batch):
    from openpyxl import load_workbook

    workbook = load_workbook(batch.file, read_only=True, data_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        batch.status = ImportBatch.Status.FAILED
        batch.summary = {"error": "Workbook is empty"}
        batch.save(update_fields=["status", "summary"])
        return batch
    headers = [str(cell or "").strip().lower() for cell in rows[0]]
    mapping = batch.column_mapping or {}
    accepted = 0
    rejected = 0
    for index, row in enumerate(rows[1:], start=2):
        raw = {headers[i] if i < len(headers) else f"column_{i}": value for i, value in enumerate(row)}
        parsed = {}
        errors = []
        for target, source in mapping.items():
            value = raw.get(str(source).lower())
            if value in (None, ""):
                errors.append(f"Missing {source}")
            else:
                parsed[target] = value
        if not mapping:
            parsed = {key: value for key, value in raw.items() if value is not None}
            for required in ("value", "unit", "period_start", "period_end"):
                if required not in parsed:
                    errors.append(f"Missing {required}")
        status = ImportRow.Status.ACCEPTED if not errors else ImportRow.Status.REJECTED
        accepted += status == ImportRow.Status.ACCEPTED
        rejected += status == ImportRow.Status.REJECTED
        ImportRow.objects.create(
            batch=batch,
            row_index=index,
            raw_data={str(key): str(value) for key, value in raw.items()},
            parsed_data={str(key): str(value) for key, value in parsed.items()},
            errors=errors,
            status=status,
        )
    batch.status = ImportBatch.Status.VALIDATED
    batch.summary = {"rows": len(rows) - 1, "accepted": accepted, "rejected": rejected}
    batch.save(update_fields=["status", "summary"])
    audit(organization, actor, "import.staged", "importbatch", batch.id, batch.summary)
    return batch


def confirm_import(batch, actor):
    imported = 0
    duplicates = 0
    for row in batch.rows.filter(status=ImportRow.Status.ACCEPTED):
        payload = dict(row.parsed_data)
        payload.setdefault("site_id", batch.site_id)
        try:
            existing = Reading.objects.filter(
                organization=batch.organization,
                site_id=batch.site_id,
                period_start=payload["period_start"],
                period_end=payload["period_end"],
                value=payload["value"],
                unit=payload["unit"],
            ).exists()
            if existing:
                row.status = ImportRow.Status.DUPLICATE
                duplicates += 1
            else:
                create_reading(batch.organization, actor, payload, source="import")
                row.status = ImportRow.Status.IMPORTED
                imported += 1
            row.save(update_fields=["status"])
        except Exception as exc:
            row.status = ImportRow.Status.REJECTED
            row.errors = list(row.errors) + [str(exc)[:200]]
            row.save(update_fields=["status", "errors"])
    batch.status = ImportBatch.Status.CONFIRMED
    batch.confirmed_at = timezone.now()
    batch.confirmed_by = actor
    batch.summary = {
        **(batch.summary or {}),
        "imported": imported,
        "duplicates": duplicates,
    }
    batch.save(update_fields=["status", "confirmed_at", "confirmed_by", "summary"])
    audit(batch.organization, actor, "import.confirmed", "importbatch", batch.id, batch.summary)
    return batch


def _period_bounds(period="month"):
    today = timezone.localdate()
    if period == "week":
        start = today - timedelta(days=30)
    elif period == "quarter":
        start = today - timedelta(days=90)
    elif period == "year":
        start = today - timedelta(days=365)
    else:
        start = today - timedelta(days=30)
    return start, today


def calculate_scores(organization, period="month", actor=None):
    start, end = _period_bounds(period)
    readings = Reading.objects.filter(
        organization=organization, period_start__gte=start, period_end__lte=end
    )
    tasks = Task.objects.filter(organization=organization, due_at__date__gte=start, due_at__date__lte=end)
    sites = Site.objects.filter(organization=organization)
    snapshots = []
    for site in sites:
        departments = Department.objects.filter(site=site)
        for department in departments:
            dept_readings = readings.filter(department=department)
            dept_tasks = tasks.filter(department=department)
            baseline = (
                Baseline.objects.filter(organization=organization, department=department)
                .order_by("-version")
                .first()
                or Baseline.objects.filter(organization=organization, site=site, department__isnull=True)
                .order_by("-version")
                .first()
            )
            energy_component = None
            provisional = False
            if baseline and dept_readings.exists():
                actual = sum(float(item.canonical_value) for item in dept_readings)
                expected = float(baseline.value)
                if expected > 0:
                    ratio = actual / expected
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
            snapshot = ScoreSnapshot.objects.create(
                organization=organization,
                site=site,
                department=department,
                period_start=start,
                period_end=end,
                overall_score=overall,
                energy_component=round(energy_component, 1),
                check_component=round(check_component, 1),
                data_coverage=round(coverage, 2),
                inputs={
                    "readings": dept_readings.count(),
                    "tasks": total,
                    "verified": verified,
                    "overdue": failed,
                    "baseline_id": baseline.id if baseline else None,
                },
                provisional=provisional or coverage < 0.5,
            )
            snapshots.append(snapshot)

    for user_id in tasks.values_list("assignee_id", flat=True).distinct():
        user_tasks = tasks.filter(assignee_id=user_id)
        completed = user_tasks.filter(status=Task.Status.COMPLETED).count()
        overdue = user_tasks.filter(status=Task.Status.OVERDUE).count()
        total = user_tasks.count()
        if not total:
            continue
        score = max(0, min(100, round(completed / total * 100 - overdue * 3, 1)))
        user = user_tasks.first().assignee
        ScoreSnapshot.objects.create(
            organization=organization,
            site=user_tasks.first().site,
            department=user_tasks.first().department,
            user=user,
            display_name=user.get_full_name() or user.username,
            period_start=start,
            period_end=end,
            overall_score=score,
            check_component=score,
            data_coverage=round(min(1, total / 8), 2),
            inputs={"tasks": total, "completed": completed, "overdue": overdue},
            provisional=total < 5,
        )
    if actor:
        audit(organization, actor, "scores.recalculated", "scoresnapshot", organization.id, {"period": period})
    return snapshots


def chart_series(organization, *, site_id=None, department_id=None, meter_id=None, days=90, group="day", metric="energy"):
    end = timezone.localdate()
    start = end - timedelta(days=days)
    readings = Reading.objects.filter(
        organization=organization, period_start__gte=start, period_end__lte=end
    )
    if site_id:
        readings = readings.filter(site_id=site_id)
    if department_id:
        readings = readings.filter(department_id=department_id)
    if meter_id:
        readings = readings.filter(meter_id=meter_id)
    buckets = defaultdict(lambda: {"actual": 0.0, "cost": 0.0, "count": 0, "flags": set()})
    for reading in readings:
        key = reading.period_start.isoformat()
        if group == "week":
            key = f"{reading.period_start.isocalendar()[0]}-W{reading.period_start.isocalendar()[1]:02d}"
        elif group == "month":
            key = reading.period_start.strftime("%Y-%m")
        entry = buckets[key]
        entry["actual"] += float(reading.canonical_value)
        entry["cost"] += float(reading.cost or 0)
        entry["count"] += 1
        entry["flags"].update(reading.quality_flags or [])
    baseline_value = None
    baseline_qs = Baseline.objects.filter(organization=organization)
    if site_id:
        baseline_qs = baseline_qs.filter(site_id=site_id)
    baseline = baseline_qs.order_by("-version").first()
    if baseline:
        baseline_value = float(baseline.value)
    series = []
    previous = None
    for key in sorted(buckets):
        entry = buckets[key]
        expected = baseline_value / max(1, len(buckets)) if baseline_value else None
        series.append(
            {
                "label": key,
                "actual": round(entry["actual"], 3),
                "unit": readings.first().canonical_unit if readings.exists() else "kWh",
                "cost": round(entry["cost"], 2),
                "expected": round(expected, 3) if expected else None,
                "previous": previous,
                "change_percent": (
                    round(((entry["actual"] - previous) / previous) * 100, 1) if previous else None
                ),
                "sample_count": entry["count"],
                "quality_flags": sorted(entry["flags"]),
            }
        )
        previous = entry["actual"]
    return {
        "series": series,
        "metric": metric,
        "group": group,
        "total": round(sum(item["actual"] for item in series), 3),
        "total_cost": round(sum(item["cost"] for item in series), 2),
        "baseline_available": baseline_value is not None,
    }


def build_report(organization, period_start, period_end):
    tasks = Task.objects.filter(
        organization=organization,
        due_at__date__gte=period_start,
        due_at__date__lte=period_end,
    )
    scores = (
        ScoreSnapshot.objects.filter(
            organization=organization,
            period_start__gte=period_start,
            period_end__lte=period_end,
            department__isnull=False,
            user__isnull=True,
        )
        # Current snapshots take precedence over provisional reruns for the
        # same site/department; reports should show one score per department.
        .order_by("provisional", "-period_end", "-created_at")
    )
    alerts = Alert.objects.filter(
        organization=organization, created_at__date__gte=period_start, created_at__date__lte=period_end
    )
    work_orders = WorkOrder.objects.filter(
        organization=organization, created_at__date__gte=period_start, created_at__date__lte=period_end
    )
    findings = AuditFinding.objects.filter(
        audit__organization=organization,
        created_at__date__gte=period_start,
        created_at__date__lte=period_end,
    )
    energy = chart_series(organization, days=max(1, (period_end - period_start).days))
    department_tasks = tasks.values("department__name").annotate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=Task.Status.COMPLETED)),
        overdue=Count("id", filter=Q(status=Task.Status.OVERDUE)),
        review=Count("id", filter=Q(status=Task.Status.WAITING_REVIEW)),
    )
    status_counts = tasks.values("status").annotate(total=Count("id"))
    alert_counts = alerts.values("severity").annotate(total=Count("id"))
    finding_counts = findings.values("severity").annotate(total=Count("id"))
    seen_scores = set()
    score_items = []
    for item in scores:
        key = (item.site_id, item.department_id)
        if key in seen_scores:
            continue
        seen_scores.add(key)
        score_items.append(
            {
                "department": item.department.name,
                "site": item.site.name,
                "score": item.overall_score,
                "energy": item.energy_component,
                "checks": item.check_component,
                "coverage": item.data_coverage,
                "provisional": item.provisional,
            }
        )
    return {
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "data_quality": {
            "reading_samples": sum(item["sample_count"] for item in energy["series"]),
            "provisional_scores": sum(1 for item in score_items if item["provisional"]),
            "score_snapshots": scores.count(),
            "baseline_available": energy["baseline_available"],
        },
        "scores": score_items,
        "tasks": {
            "total": tasks.count(),
            "completed": tasks.filter(status=Task.Status.COMPLETED).count(),
            "overdue": tasks.filter(status=Task.Status.OVERDUE).count(),
            "review": tasks.filter(status=Task.Status.WAITING_REVIEW).count(),
        },
        "tasks_by_department": [
            {
                "department": item["department__name"] or "Unassigned",
                "total": item["total"],
                "completed": item["completed"],
                "overdue": item["overdue"],
                "review": item["review"],
            }
            for item in department_tasks
        ],
        "tasks_by_status": [
            {"status": item["status"], "total": item["total"]}
            for item in status_counts
        ],
        "alerts_by_severity": [
            {"severity": item["severity"], "total": item["total"]}
            for item in alert_counts
        ],
        "findings_by_severity": [
            {"severity": item["severity"], "total": item["total"]}
            for item in finding_counts
        ],
        "energy": energy,
        "alerts": [
            {"title": item.title, "status": item.status, "severity": item.severity}
            for item in alerts
        ],
        "work_orders": [
            {"title": item.title, "status": item.status, "due_date": item.due_date}
            for item in work_orders
        ],
        "findings": [
            {"requirement": item.requirement, "status": item.status, "severity": item.severity}
            for item in findings
        ],
        "enpis": [
            {"name": item.name, "unit": item.unit, "version": item.version}
            for item in EnPI.objects.filter(organization=organization)
        ],
        "emissions": EmissionRecord.objects.filter(
            organization=organization,
            period_start__gte=period_start,
            period_end__lte=period_end,
        ).count(),
    }


def refresh_weather(organization):
    created = 0
    for site in Site.objects.filter(organization=organization):
        params = urllib.parse.urlencode(
            {
                "latitude": 0,
                "longitude": 0,
                "hourly": "temperature_2m,relative_humidity_2m",
                "forecast_days": 2,
            }
        )
        try:
            with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{params}", timeout=8) as response:
                payload = json.loads(response.read().decode())
        except Exception:
            payload = None
        times = (payload or {}).get("hourly", {}).get("time", [])[:12]
        temps = (payload or {}).get("hourly", {}).get("temperature_2m", [])[:12]
        humidities = (payload or {}).get("hourly", {}).get("relative_humidity_2m", [])[:12]
        if not times:
            now = timezone.now()
            times = [(now + timedelta(hours=index)).isoformat() for index in range(8)]
            temps = [20 + index for index in range(8)]
            humidities = [55] * 8
        for index, stamp in enumerate(times):
            forecast_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if timezone.is_naive(forecast_at):
                forecast_at = timezone.make_aware(forecast_at)
            temperature = float(temps[index]) if index < len(temps) else 20
            humidity = float(humidities[index]) if index < len(humidities) else 55
            peak = temperature >= 30 or temperature <= 2
            _, made = WeatherForecast.objects.update_or_create(
                site=site,
                forecast_for=forecast_at,
                source="open-meteo",
                defaults={
                    "organization": organization,
                    "temperature_c": temperature,
                    "humidity": humidity,
                    "peak_alert": peak,
                    "recommended_preparation": (
                        "Review HVAC set-points and operating schedule before the peak."
                        if peak
                        else ""
                    ),
                    "inputs": {"source": "open-meteo", "fallback": payload is None},
                },
            )
            created += int(made)
    return created


def maintenance_due(organization):
    result = []
    for plan in MaintenancePlan.objects.filter(organization=organization, is_active=True):
        due = plan.next_due_date
        overdue = bool(due and due < timezone.localdate())
        soon = bool(due and not overdue and due <= timezone.localdate() + timedelta(days=14))
        alerts = Alert.objects.filter(asset=plan.asset, status=Alert.Status.NEW).count()
        if overdue or soon or alerts:
            result.append(
                {
                    "plan_id": plan.id,
                    "asset": plan.asset.name,
                    "due_date": due,
                    "overdue": overdue,
                    "due_soon": soon,
                    "reason": plan.reason or "Date/running-hour threshold",
                    "anomaly_alerts": alerts,
                }
            )
    return result


def device_summary(device):
    latest = (
        ScoreSnapshot.objects.filter(
            organization=device.organization,
            department__isnull=False,
            site=device.site if device.site_id else None,
        )
        if device.site_id
        else ScoreSnapshot.objects.filter(organization=device.organization, department__isnull=False)
    )
    by_department = {}
    for snapshot in latest.order_by("created_at"):
        if snapshot.department_id:
            by_department[snapshot.department_id] = snapshot
    departments = []
    for department_id, snapshot in by_department.items():
        top = None
        if device.allow_top_employee:
            candidate = (
                ScoreSnapshot.objects.filter(
                    organization=device.organization,
                    department_id=department_id,
                    user__isnull=False,
                    top_employee=False,
                )
                .order_by("-overall_score", "-created_at")
                .first()
            )
            if candidate and candidate.data_coverage >= 0.5:
                top = {
                    "display_name": candidate.display_name,
                    "score": candidate.overall_score,
                }
        departments.append(
            {
                "id": str(snapshot.department_id),
                "name": snapshot.department.name,
                "score": snapshot.overall_score,
                "status": "provisional" if snapshot.provisional else "current",
                "updated_at": snapshot.created_at.isoformat(),
                "top_employee": top,
            }
        )
    scores = [
        department["score"]
        for department in departments
        if isinstance(department.get("score"), (int, float))
    ]
    overall_score = round(sum(scores) / len(scores), 1) if scores else None
    eligible_top_employees = [
        department["top_employee"]
        for department in departments
        if department.get("top_employee")
    ]
    top_employee = (
        max(eligible_top_employees, key=lambda item: item.get("score") or 0)
        if eligible_top_employees
        else None
    )
    return {
        "organization": device.organization.name,
        "device": device.name,
        "generated_at": timezone.now().isoformat(),
        "refresh_seconds": device.refresh_seconds,
        "overall_score": overall_score,
        "top_employee": top_employee,
        "departments": departments,
    }


def confirm_field_scan(scan, actor, values):
    corrected = dict(values or scan.corrected_fields or scan.raw_fields)
    created = None
    if scan.kind == FieldScan.Kind.METER:
        readings = []
        for key, value in corrected.items():
            if "reading" in key.lower() or key.lower() in {"value", "register_1"}:
                readings.append(value)
        if readings:
            created = create_reading(
                scan.organization,
                actor,
                {
                    "site_id": scan.site_id,
                    "meter_id": scan.meter_id,
                    "value": readings[0],
                    "unit": corrected.get("unit", "kWh"),
                    "period_start": corrected.get("period_start") or timezone.localdate().replace(day=1),
                    "period_end": corrected.get("period_end") or timezone.localdate(),
                    "field_scan_id": scan.id,
                },
                source="scan",
            )
    from core.models import FieldScan as FieldScanModel

    scan.status = FieldScanModel.Status.CONFIRMED
    scan.corrected_fields = corrected
    scan.confirmed_by = actor
    scan.confirmed_at = timezone.now()
    scan.save(update_fields=["status", "corrected_fields", "confirmed_by", "confirmed_at"])
    audit(scan.organization, actor, "field_scan.confirmed", "fieldscan", scan.id, {"kind": scan.kind})
    return created
