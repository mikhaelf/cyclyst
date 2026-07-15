"""Builds the PDF activity report for a credential — pure document layout, no Flask/DB
concerns (routes.py fetches the data, this just lays it out).
"""

import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HEADER_STYLE = TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#161a23")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#eaecf0")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
])


def build_activity_report_pdf(credential_row, in_cycle_activities, category_results, earned):
    """Returns PDF bytes: an itemized activity report for one credential's current cycle."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.75 * inch, bottomMargin=0.75 * inch)
    styles = getSampleStyleSheet()
    # Table() does not wrap plain strings — a long title just overflows past its column
    # and overlaps neighboring cells. Wrapping cell text in Paragraph() makes it a proper
    # flowable that wraps within colWidths instead.
    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8, leading=10)
    story = []

    title = credential_row["name"]
    if credential_row["member_id"]:
        title += f" · {credential_row['member_id']}"
    story.append(Paragraph(title, styles["Title"]))

    subtitle_bits = [credential_row["issuer"]]
    if credential_row["cycle_start_date"] and credential_row["cycle_end_date"]:
        subtitle_bits.append(f"{credential_row['cycle_start_date']} to {credential_row['cycle_end_date']}")
    story.append(Paragraph(" &middot; ".join(subtitle_bits), styles["Normal"]))
    story.append(Spacer(1, 14))

    if credential_row["total"] is not None:
        total = credential_row["total"]
        unit = credential_row["unit"] or ""
        story.append(Paragraph(f"<b>{earned:g} of {total:g} {unit}</b> earned this cycle", styles["Normal"]))
        story.append(Spacer(1, 10))

    if category_results:
        cat_data = [["Category", "Rule", "Earned", "Target", "Result"]]
        for c in category_results:
            rule = "at least" if c["rule_type"] == "min" else "at most"
            cat_data.append([c["name"], rule, f"{c['earned']:g}", f"{c['target']:g}", "Met" if c["ok"] else "Not met"])
        cat_table = Table(cat_data, hAlign="LEFT")
        cat_table.setStyle(HEADER_STYLE)
        story.append(cat_table)
        story.append(Spacer(1, 16))

    if in_cycle_activities:
        act_data = [["Date", "Provider", "Title", "Hours", "Category", "Status"]]
        for a in in_cycle_activities:
            act_data.append([
                a["date"],
                Paragraph(a["provider"] or "", cell_style),
                Paragraph(a["title"], cell_style),
                f"{a['hours']:g}",
                a["category"] or "",
                a["status"],
            ])
        act_table = Table(
            act_data, hAlign="LEFT",
            colWidths=[0.7 * inch, 1.1 * inch, 2.5 * inch, 0.5 * inch, 1 * inch, 0.9 * inch],
        )
        act_table.setStyle(HEADER_STYLE)
        story.append(act_table)
    else:
        story.append(Paragraph("No activities logged in this cycle.", styles["Normal"]))

    story.append(Spacer(1, 24))
    story.append(Paragraph(f"Generated {date.today().isoformat()}", styles["Normal"]))

    doc.build(story)
    return buf.getvalue()
