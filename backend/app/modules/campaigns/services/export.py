import json
import os
import textwrap
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import reportlab
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from app.modules.campaigns.domain import CampaignExportError
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan

_BODY_FONT = "CampaignSans"
_BOLD_FONT = "CampaignSans-Bold"


@dataclass(frozen=True, slots=True)
class CampaignExportResult:
    content: bytes
    content_type: str = "application/zip"
    filename: str = "campaign-export.zip"


@dataclass(frozen=True, slots=True)
class _PDFLine:
    text: str
    font: str = "F1"
    size: int = 10
    spacing: int = 14


class CampaignExportService:
    """Render a deterministic in-memory package from persisted Campaign data."""

    ARCHIVE_MEMBERS = (
        "campaign-plan.pdf",
        "campaign-plan.json",
        "campaign-brief.json",
    )

    def export(
        self,
        *,
        brief: CampaignBrief,
        plan: CampaignPlan,
    ) -> CampaignExportResult:
        try:
            plan_json = _json_bytes(plan)
            brief_json = _json_bytes(brief)
            pdf = _render_plan_pdf(plan)
            archive = _archive_bytes(
                {
                    "campaign-plan.pdf": pdf,
                    "campaign-plan.json": plan_json,
                    "campaign-brief.json": brief_json,
                }
            )
        except Exception as exc:
            raise CampaignExportError("Campaign export rendering failed") from exc
        return CampaignExportResult(content=archive)


def _json_bytes(value: CampaignBrief | CampaignPlan) -> bytes:
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return f"{payload}\n".encode()


def _archive_bytes(members: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name in CampaignExportService.ARCHIVE_MEMBERS:
            info = ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, members[name])
    return buffer.getvalue()


def _render_plan_pdf(plan: CampaignPlan) -> bytes:
    lines: list[_PDFLine] = []

    def blank() -> None:
        lines.append(_PDFLine("", spacing=8))

    def title(value: str) -> None:
        lines.extend(_wrapped(value, font="F2", size=22, spacing=27, width=42))
        blank()

    def section(name: str) -> None:
        blank()
        lines.append(_PDFLine(name, font="F2", size=14, spacing=20))

    def paragraph(value: str, *, prefix: str = "") -> None:
        lines.extend(_wrapped(f"{prefix}{value}", width=92))

    def item(value: str) -> None:
        lines.extend(_wrapped(f"- {value}", width=88))

    title(plan.campaign_name)
    section("Executive Summary")
    paragraph(plan.executive_summary)
    section("Objective")
    paragraph(plan.objective.primary, prefix="Primary: ")
    if plan.objective.secondary:
        paragraph(plan.objective.secondary, prefix="Secondary: ")
    section("Target Audience")
    paragraph(plan.target_audience.primary, prefix="Primary: ")
    if plan.target_audience.location:
        paragraph(plan.target_audience.location, prefix="Location: ")
    for need in plan.target_audience.needs_or_motivations:
        item(need)
    if plan.offer:
        section("Offer")
        paragraph(plan.offer)
    for name, value in (
        ("Value Proposition", plan.value_proposition),
        ("Positioning", plan.positioning),
        ("Key Message", plan.key_message),
        ("Strategy", plan.strategy),
    ):
        section(name)
        paragraph(value)
    section("Channel Strategies")
    for channel in plan.channels:
        item(f"{channel.name}: {channel.purpose} Reason: {channel.reason}")
    section("Content Directions")
    for direction in plan.content_direction:
        item(f"{direction.idea}: {direction.purpose}")
    if plan.budget_allocation:
        section("Budget Allocation")
        paragraph(
            f"Total: {plan.budget_allocation.total} {plan.budget_allocation.currency}"
        )
        for budget_item in plan.budget_allocation.items:
            item(
                f"{budget_item.channel}: {budget_item.amount} "
                f"{plan.budget_allocation.currency}. {budget_item.reason}"
            )
    section("Timeline")
    for phase in plan.timeline:
        item(f"{phase.period} - {phase.phase}: {phase.objective}")
        for activity in phase.activities:
            paragraph(activity, prefix="  - ")
    section("KPIs")
    for kpi in plan.kpis:
        item(f"{kpi.name}: {kpi.purpose}")
    section("Assumptions or Risks")
    for risk in plan.assumptions_or_risks:
        item(risk)
    section("Next Steps")
    for next_step in plan.next_steps:
        item(next_step)

    return _pdf_document(_paginate(lines))


def _wrapped(
    value: str,
    *,
    font: str = "F1",
    size: int = 10,
    spacing: int = 14,
    width: int,
) -> list[_PDFLine]:
    normalized = _safe_text(value)
    wrapped: list[_PDFLine] = []
    for source_line in normalized.splitlines() or [""]:
        parts = textwrap.wrap(
            source_line,
            width=width,
            replace_whitespace=True,
            drop_whitespace=True,
            break_long_words=True,
            break_on_hyphens=False,
        ) or [""]
        wrapped.extend(_PDFLine(part, font=font, size=size, spacing=spacing) for part in parts)
    return wrapped


def _safe_text(value: str) -> str:
    return "".join(
        character if character >= " " or character in "\n\r" else " "
        for character in value
    )


def _paginate(lines: list[_PDFLine]) -> list[list[tuple[_PDFLine, int]]]:
    pages: list[list[tuple[_PDFLine, int]]] = [[]]
    y = 790
    for line in lines:
        if y - line.spacing < 48:
            pages.append([])
            y = 790
        pages[-1].append((line, y))
        y -= line.spacing
    return pages


def _pdf_document(pages: list[list[tuple[_PDFLine, int]]]) -> bytes:
    regular, bold = _registered_fonts()
    buffer = BytesIO()
    document = Canvas(
        buffer,
        pagesize=A4,
        pageCompression=0,
        invariant=1,
    )
    document.setTitle("Campaign Plan")
    for page in pages:
        for line, y in page:
            font = bold if line.font == "F2" else regular
            _require_supported_text(line.text, font)
            document.setFont(font.fontName, line.size)
            document.drawString(54, y, line.text)
        document.showPage()
    document.save()
    return buffer.getvalue()


@lru_cache
def _registered_fonts() -> tuple[TTFont, TTFont]:
    regular_path, bold_path = _font_paths()
    regular = TTFont(_BODY_FONT, regular_path)
    bold = TTFont(_BOLD_FONT, bold_path)
    pdfmetrics.registerFont(regular)
    pdfmetrics.registerFont(bold)
    return regular, bold


def _font_paths() -> tuple[str, str]:
    configured = os.getenv("CAMPAIGN_PDF_FONT_PATH")
    candidates = [
        (
            configured,
            os.getenv("CAMPAIGN_PDF_BOLD_FONT_PATH") or configured,
        ),
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ),
        (
            str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"),
            str(Path(reportlab.__file__).parent / "fonts" / "VeraBd.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if regular and bold and Path(regular).is_file() and Path(bold).is_file():
            return regular, bold
    raise CampaignExportError("No Campaign PDF font is available")


def _require_supported_text(text: str, font: TTFont) -> None:
    missing = {
        ord(character)
        for character in text
        if not character.isspace() and ord(character) not in font.face.charWidths
    }
    if missing:
        codepoints = ", ".join(f"U+{value:04X}" for value in sorted(missing))
        raise CampaignExportError(f"Campaign PDF font lacks glyphs for {codepoints}")


__all__ = ["CampaignExportResult", "CampaignExportService"]
