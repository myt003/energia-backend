"""Layout, charts and exports for Energia management reports."""

from __future__ import annotations

import csv
import html
import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

NAVY = colors.HexColor("#102A43")
SLATE = colors.HexColor("#172B4D")
TEAL = colors.HexColor("#0F766E")
LIME = colors.HexColor("#A3E635")
BACKGROUND = colors.HexColor("#F5F7FA")
WHITE = colors.HexColor("#FFFFFF")
MUTED = colors.HexColor("#6B7C93")
LINE = colors.HexColor("#DCE4EC")
WARN = colors.HexColor("#F59E0B")
DANGER = colors.HexColor("#EF4444")
SOFT_TEAL = colors.HexColor("#D9F0ED")
SOFT_LIME = colors.HexColor("#ECFCCB")

CONTENT_WIDTH = 500


def _text(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return html.escape(str(value))


def _number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _score_color(score: Any) -> colors.Color:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return MUTED
    if value >= 80:
        return TEAL
    if value >= 60:
        return colors.HexColor("#84CC16")
    if value >= 40:
        return WARN
    return DANGER


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "banner_title": ParagraphStyle(
            "BannerTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=21,
            leading=25,
            textColor=WHITE,
            alignment=0,
            spaceAfter=4,
        ),
        "banner_sub": ParagraphStyle(
            "BannerSub",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#D9E7F2"),
        ),
        "h2": ParagraphStyle(
            "SectionHeading",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=NAVY,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11.5,
            textColor=SLATE,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.2,
            leading=9.5,
            textColor=MUTED,
        ),
        "cell": ParagraphStyle(
            "Cell",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.6,
            leading=9.6,
            textColor=SLATE,
        ),
        "cell_bold": ParagraphStyle(
            "CellBold",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=7.6,
            leading=9.6,
            textColor=NAVY,
        ),
        "card_label": ParagraphStyle(
            "CardLabel",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7,
            leading=9,
            textColor=MUTED,
            alignment=1,
        ),
        "card_value": ParagraphStyle(
            "CardValue",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            textColor=NAVY,
            alignment=1,
        ),
    }


class ScoreBars(Flowable):
    """Horizontal department score bars with provisional flags."""

    def __init__(self, scores: list[dict[str, Any]], width: float = CONTENT_WIDTH):
        super().__init__()
        self.scores = scores
        self.width = width
        self.row_height = 25

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        self.width = min(self.width, availWidth)
        self.height = max(1, len(self.scores)) * self.row_height + 6
        return self.width, self.height

    def draw(self) -> None:
        bar_x = 122
        bar_w = self.width - 178
        canvas = self.canv
        for index, item in enumerate(self.scores):
            y = self.height - (index + 1) * self.row_height + 4
            value = _float(item.get("score"), 0.0) or 0.0
            label = f"{_text(item.get('department'))} ({_text(item.get('site'))})"
            canvas.setFillColor(NAVY)
            canvas.setFont("Helvetica-Bold", 7.8)
            canvas.drawString(0, y + 2, label[:28])
            canvas.setFillColor(LINE)
            canvas.roundRect(bar_x, y, bar_w, 9, 4, stroke=0, fill=1)
            canvas.setFillColor(_score_color(value))
            canvas.roundRect(bar_x, y, bar_w * max(0, min(100, value)) / 100, 9, 4, stroke=0, fill=1)
            canvas.setFillColor(NAVY)
            canvas.setFont("Helvetica-Bold", 8.5)
            canvas.drawRightString(self.width, y + 2, _number(value))
            if item.get("provisional"):
                canvas.setFillColor(MUTED)
                canvas.setFont("Helvetica", 6.5)
                canvas.drawString(bar_x, y - 8, "provisional — limited data")


class EnergyChart(Flowable):
    """Actual vs expected daily energy line chart."""

    def __init__(self, energy: dict[str, Any], width: float = CONTENT_WIDTH, height: float = 165):
        super().__init__()
        self.series = [
            item
            for item in energy.get("series", [])
            if isinstance(item.get("actual"), (int, float))
        ]
        self.width = width
        self.height = height

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        self.width = min(self.width, availWidth)
        return self.width, self.height

    def draw(self) -> None:
        canvas = self.canv
        left, bottom = 38, 25
        plot_w = self.width - left - 12
        plot_h = self.height - bottom - 18
        values = [
            float(item["actual"])
            for item in self.series
            if isinstance(item.get("actual"), (int, float))
        ]
        expected = [
            float(item["expected"])
            for item in self.series
            if isinstance(item.get("expected"), (int, float))
        ]
        maximum = max(values + expected + [1.0])
        minimum = 0.0

        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        for step in range(5):
            y = bottom + plot_h * step / 4
            canvas.line(left, y, left + plot_w, y)
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 6.5)
            canvas.drawRightString(left - 4, y - 2, f"{maximum * step / 4:.0f}")
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica-Bold", 6.5)
        canvas.drawString(left, bottom + plot_h + 4, "Energy by day")

        if not self.series:
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 8)
            canvas.drawString(left, bottom + plot_h / 2, "No readings in this period")
            return

        def point(index: int, value: float) -> tuple[float, float]:
            x = left + plot_w * index / max(1, len(self.series) - 1)
            y = bottom + plot_h * (value - minimum) / max(0.0001, maximum - minimum)
            return x, y

        def draw_polyline(points: list[tuple[float, float]], color: colors.Color, width: float) -> None:
            if len(points) < 2:
                return
            canvas.setStrokeColor(color)
            canvas.setLineWidth(width)
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                canvas.line(x1, y1, x2, y2)

        expected_points = [
            point(i, float(item["expected"]))
            for i, item in enumerate(self.series)
            if item.get("expected") is not None
        ]
        canvas.setDash(3, 2)
        draw_polyline(expected_points, colors.HexColor("#84CC16"), 1.2)
        canvas.setDash()

        actual_points = [point(i, float(item["actual"])) for i, item in enumerate(self.series)]
        draw_polyline(actual_points, TEAL, 1.8)
        for x, y in actual_points:
            canvas.setFillColor(TEAL)
            canvas.circle(x, y, 1.7, stroke=0, fill=1)

        label_step = max(1, len(self.series) // 6)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 6)
        for index, item in enumerate(self.series):
            if index % label_step:
                continue
            x, _ = point(index, 0)
            canvas.drawCentredString(x, bottom - 12, str(item.get("label", ""))[-5:])

        canvas.setFillColor(TEAL)
        canvas.setFont("Helvetica-Bold", 6.5)
        canvas.drawString(left, 2, "● Actual")
        canvas.setFillColor(colors.HexColor("#84CC16"))
        canvas.drawString(left + 52, 2, "● Baseline")


class TaskDonut(Flowable):
    """Completion donut with a compact legend."""

    def __init__(self, tasks: dict[str, Any], width: float = CONTENT_WIDTH, height: float = 135):
        super().__init__()
        self.tasks = tasks
        self.width = width
        self.height = height

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        self.width = min(self.width, availWidth)
        return self.width, self.height

    def draw(self) -> None:
        canvas = self.canv
        total = int(self.tasks.get("total") or 0)
        completed = int(self.tasks.get("completed") or 0)
        overdue = int(self.tasks.get("overdue") or 0)
        review = int(self.tasks.get("review") or 0)
        pending = max(0, total - completed - overdue - review)
        segments = [
            ("Completed", completed, TEAL),
            ("Overdue", overdue, DANGER),
            ("In review", review, WARN),
            ("Pending", pending, MUTED),
        ]
        cx, cy, radius = 66, self.height / 2, 48
        angle = 90.0
        for _, value, color in segments:
            if total <= 0 or value <= 0:
                continue
            sweep = 360.0 * value / total
            canvas.setFillColor(color)
            canvas.wedge(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                angle - sweep,
                sweep,
                stroke=0,
                fill=1,
            )
            angle -= sweep
        canvas.setFillColor(WHITE)
        canvas.circle(cx, cy, radius * 0.58, stroke=0, fill=1)
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 15)
        canvas.drawCentredString(cx, cy + 3, f"{(100 * completed / total):.0f}%" if total else "—")
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(cx, cy - 11, "verified")

        legend_x = 140
        for index, (label, value, color) in enumerate(segments):
            y = self.height - 24 - index * 23
            canvas.setFillColor(color)
            canvas.roundRect(legend_x, y, 10, 10, 2, stroke=0, fill=1)
            canvas.setFillColor(NAVY)
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawString(legend_x + 17, y + 1, f"{label}: {value}")


def _table(rows: list[list[Any]], col_widths: list[float], *, header: bool = True) -> Table:
    table = Table(rows, colWidths=col_widths, hAlign="LEFT", repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 7.5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, BACKGROUND]),
            ]
        )
    table.setStyle(TableStyle(style))
    return table


def _section(title: str, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    return [
        Paragraph(_text(title), styles["h2"]),
        HRFlowable(width="100%", thickness=1, color=LIME, spaceAfter=6),
    ]


def build_pdf_report(
    report: dict[str, Any],
    organization: str,
    period_start: str,
    period_end: str,
) -> bytes:
    """Return a branded, multi-section PDF management report."""
    styles = _styles()
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=44,
        bottomMargin=38,
        title=f"Energia management report {period_start} to {period_end}",
        author="Energia",
    )

    def decorate(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, A4[1] - 14, A4[0], 14, stroke=0, fill=1)
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawString(36, A4[1] - 10, "ENERGIA · MANAGEMENT REPORT")
        canvas.drawRightString(A4[0] - 36, A4[1] - 10, f"{period_start} — {period_end}")
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(36, 20, "Generated by Energia · server-calculated scores")
        canvas.drawRightString(A4[0] - 36, 20, f"Page {document.page}")
        canvas.restoreState()

    scores = report.get("scores", [])
    score_values = [
        value
        for value in (_float(item.get("score")) for item in scores)
        if value is not None
    ]
    overall = round(sum(score_values) / len(score_values), 1) if score_values else None
    tasks = report.get("tasks", {})
    energy = report.get("energy", {})
    quality = report.get("data_quality", {})
    unit = energy.get("series", [{}])[0].get("unit", "kWh") if energy.get("series") else "kWh"

    banner = Table(
        [
            [
                Paragraph("Energia management report", styles["banner_title"]),
                Paragraph(
                    f"<b>{_text(organization)}</b><br/>{_text(period_start)} to {_text(period_end)}",
                    styles["banner_sub"],
                ),
            ]
        ],
        colWidths=[330, 170],
    )
    banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBEFORE", (1, 0), (1, 0), 2, LIME),
            ]
        )
    )

    cards = Table(
        [
            [
                Paragraph("OVERALL SCORE", styles["card_label"]),
                Paragraph("ENERGY", styles["card_label"]),
                Paragraph("CHECKS VERIFIED", styles["card_label"]),
                Paragraph("DATA QUALITY", styles["card_label"]),
            ],
            [
                Paragraph(_number(overall), styles["card_value"]),
                Paragraph(f"{_number(energy.get('total'), 0)} {_text(unit)}", styles["card_value"]),
                Paragraph(
                    f"{tasks.get('completed', 0)}/{tasks.get('total', 0)}",
                    styles["card_value"],
                ),
                Paragraph(f"{quality.get('reading_samples', 0)} samples", styles["card_value"]),
            ],
            [
                Paragraph(f"{len(scores)} departments", styles["small"]),
                Paragraph(f"cost {_number(energy.get('total_cost'), 2)}", styles["small"]),
                Paragraph(f"{tasks.get('overdue', 0)} overdue", styles["small"]),
                Paragraph(f"{quality.get('provisional_scores', 0)} provisional", styles["small"]),
            ],
        ],
        colWidths=[CONTENT_WIDTH / 4] * 4,
    )
    cards.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BACKGROUND),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LINEABOVE", (0, 0), (-1, 0), 3, TEAL),
            ]
        )
    )

    story: list[Flowable] = [
        banner,
        Spacer(1, 12),
        cards,
        Spacer(1, 6),
        Paragraph(
            "Scores are calculated on the server: 60% energy performance against the approved "
            "baseline and 40% verified checks, with deductions for overdue work. Readings and "
            "coverage determine whether a score is marked provisional.",
            styles["small"],
        ),
    ]

    story.extend(_section("Department performance", styles))
    chart_scores = scores[:16]
    story.append(ScoreBars(chart_scores))
    if len(scores) > len(chart_scores):
        story.append(
            Paragraph(
                f"Showing the latest {len(chart_scores)} of {len(scores)} department scores; "
                "the complete set follows in the table.",
                styles["small"],
            )
        )
    story.append(Spacer(1, 6))
    score_rows = [["Department", "Site", "Score", "Energy", "Checks", "Coverage", "Status"]]
    for item in scores:
        score_rows.append(
            [
                _text(item.get("department")),
                _text(item.get("site")),
                _number(item.get("score")),
                _number(item.get("energy")),
                _number(item.get("checks")),
                _number(item.get("coverage"), 2),
                "Provisional" if item.get("provisional") else "Current",
            ]
        )
    story.append(_table(score_rows, [110, 90, 50, 50, 50, 60, 90]))

    story.extend(_section("Energy consumption", styles))
    story.append(EnergyChart(energy))
    story.append(Spacer(1, 4))
    energy_rows = [["Date", "Actual", "Baseline", "Cost", "Samples", "Flags", "Δ %"]]
    for item in energy.get("series", [])[:30]:
        energy_rows.append(
            [
                _text(item.get("label")),
                _number(item.get("actual")),
                _number(item.get("expected")),
                _number(item.get("cost"), 2),
                str(item.get("sample_count", 0)),
                _text(", ".join(item.get("quality_flags", []))),
                _number(item.get("change_percent")),
            ]
        )
    if len(energy_rows) == 1:
        energy_rows.append(["No readings", "—", "—", "—", "0", "—", "—"])
    story.append(_table(energy_rows, [70, 62, 62, 55, 50, 150, 51]))

    story.append(PageBreak())
    story.extend(_section("Checks and compliance", styles))
    story.append(TaskDonut(tasks))
    story.append(Spacer(1, 5))
    task_rows = [["Department", "Total", "Completed", "Overdue", "In review"]]
    for item in report.get("tasks_by_department", []):
        task_rows.append(
            [
                _text(item.get("department")),
                str(item.get("total", 0)),
                str(item.get("completed", 0)),
                str(item.get("overdue", 0)),
                str(item.get("review", 0)),
            ]
        )
    if len(task_rows) == 1:
        task_rows.append(["No tasks in period", "0", "0", "0", "0"])
    story.append(_table(task_rows, [190, 70, 80, 75, 85]))

    story.extend(_section("Attention register", styles))
    attention_rows = [["Type", "Item", "Status / severity"]]
    for item in report.get("alerts", []):
        attention_rows.append(["Alert", _text(item.get("title")), f"{_text(item.get('status'))} / {_text(item.get('severity'))}"])
    for item in report.get("findings", []):
        attention_rows.append(["Audit finding", _text(item.get("requirement")), f"{_text(item.get('status'))} / {_text(item.get('severity'))}"])
    for item in report.get("work_orders", []):
        attention_rows.append(
            ["Work order", _text(item.get("title")), f"{_text(item.get('status'))} / due {_text(item.get('due_date'))}"]
        )
    if len(attention_rows) == 1:
        attention_rows.append(["None", "No alerts, findings or work orders", "—"])
    story.append(_table(attention_rows, [85, 250, 165]))

    story.extend(_section("Severity breakdown", styles))
    severity_rows = [["Category", "Severity", "Count"]]
    for item in report.get("alerts_by_severity", []):
        severity_rows.append(["Alert", _text(item.get("severity")), str(item.get("total", 0))])
    for item in report.get("findings_by_severity", []):
        severity_rows.append(["Audit finding", _text(item.get("severity")), str(item.get("total", 0))])
    if len(severity_rows) == 1:
        severity_rows.append(["None", "—", "0"])
    story.append(_table(severity_rows, [160, 220, 120]))

    story.extend(_section("EnPIs and emissions", styles))
    enpi_rows = [["EnPI", "Unit", "Version"]]
    for item in report.get("enpis", []):
        enpi_rows.append([_text(item.get("name")), _text(item.get("unit")), _text(item.get("version"))])
    if len(enpi_rows) == 1:
        enpi_rows.append(["No EnPI configured", "—", "—"])
    story.append(_table(enpi_rows, [270, 110, 120]))
    story.append(Spacer(1, 5))
    story.append(
        Paragraph(
            f"Emission records in period: <b>{report.get('emissions', 0)}</b>. "
            f"Baseline available: <b>{'yes' if energy.get('baseline_available') else 'no'}</b>. "
            "This report is generated from the same Django/PostgreSQL records used by web, "
            "mobile and the ESP32 display.",
            styles["body"],
        )
    )

    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return output.getvalue()


def build_csv_report(report: dict[str, Any]) -> str:
    """Return a flat, granular CSV export of the same report data."""
    output = io.StringIO()
    writer = csv.writer(output)
    period = report.get("period", {})
    quality = report.get("data_quality", {})
    tasks = report.get("tasks", {})
    energy = report.get("energy", {})

    writer.writerow(["section", "label", "value", "detail"])
    writer.writerow(["summary", "period_start", period.get("start", ""), ""])
    writer.writerow(["summary", "period_end", period.get("end", ""), ""])
    writer.writerow(["summary", "reading_samples", quality.get("reading_samples", 0), ""])
    writer.writerow(["summary", "score_snapshots", quality.get("score_snapshots", 0), ""])
    writer.writerow(["summary", "provisional_scores", quality.get("provisional_scores", 0), ""])
    writer.writerow(["summary", "baseline_available", quality.get("baseline_available", False), ""])
    writer.writerow(["summary", "emissions", report.get("emissions", 0), ""])
    for item in report.get("scores", []):
        writer.writerow(
            [
                "score",
                item.get("department", ""),
                item.get("score", ""),
                f"site={item.get('site', '')}; energy={item.get('energy', '')}; "
                f"checks={item.get('checks', '')}; coverage={item.get('coverage', '')}; "
                f"provisional={item.get('provisional', False)}",
            ]
        )
    for key in ("total", "completed", "overdue", "review"):
        writer.writerow(["tasks", key, tasks.get(key, 0), ""])
    for item in report.get("tasks_by_department", []):
        writer.writerow(
            [
                "tasks_by_department",
                item.get("department", ""),
                item.get("total", 0),
                f"completed={item.get('completed', 0)}; overdue={item.get('overdue', 0)}; "
                f"review={item.get('review', 0)}",
            ]
        )
    for item in energy.get("series", []):
        writer.writerow(
            [
                "energy",
                item.get("label", ""),
                item.get("actual", ""),
                f"expected={item.get('expected', '')}; cost={item.get('cost', '')}; "
                f"samples={item.get('sample_count', 0)}; "
                f"flags={','.join(item.get('quality_flags', []))}; "
                f"change_percent={item.get('change_percent', '')}",
            ]
        )
    for item in report.get("alerts", []):
        writer.writerow(["alert", item.get("title", ""), item.get("status", ""), item.get("severity", "")])
    for item in report.get("findings", []):
        writer.writerow(["finding", item.get("requirement", ""), item.get("status", ""), item.get("severity", "")])
    for item in report.get("work_orders", []):
        writer.writerow(["work_order", item.get("title", ""), item.get("status", ""), item.get("due_date", "")])
    for item in report.get("enpis", []):
        writer.writerow(["enpi", item.get("name", ""), item.get("unit", ""), item.get("version", "")])
    return output.getvalue()
