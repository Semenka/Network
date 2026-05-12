from __future__ import annotations

import html
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .db import record_draft_event
from .drafts import create_custom_draft


@dataclass(frozen=True)
class RotationSlot:
    name: str
    highlight: str
    visual_style: str
    company_a: str
    company_b: str
    source_angle: str
    cta: str


ENERGY_ROTATION: tuple[RotationSlot, ...] = (
    RotationSlot(
        name="company-proof-points",
        highlight="Two positive proof points from energy majors",
        visual_style="conference-stage",
        company_a="TotalEnergies",
        company_b="ADNOC",
        source_angle="Pangea 5, Mistral AI, ENERGYai, and the ADNOC/Microsoft energy-for-AI agreement",
        cta="Which model reaches breakout ROI first?",
    ),
    RotationSlot(
        name="ai-power-demand",
        highlight="AI needs power, and energy companies can shape the answer",
        visual_style="data-center-and-grid",
        company_a="TotalEnergies",
        company_b="ADNOC",
        source_angle="TotalEnergies-Google power deals and ADNOC/Masdar/XRG/Microsoft energy infrastructure collaboration",
        cta="Where is the highest-leverage bottleneck: power, grid, land, or flexibility?",
    ),
    RotationSlot(
        name="agentic-operations",
        highlight="Agentic AI is moving from pilots into control rooms",
        visual_style="control-room",
        company_a="ADNOC",
        company_b="TotalEnergies",
        source_angle="ADNOC ENERGYai, Neuron 5, and TotalEnergies digital factory/industrial AI use cases",
        cta="Which operating workflow should AI agents transform first?",
    ),
    RotationSlot(
        name="hpc-and-industrial-data",
        highlight="Industrial AI starts with proprietary data and serious compute",
        visual_style="compute-stack",
        company_a="TotalEnergies",
        company_b="ADNOC",
        source_angle="TotalEnergies Pangea 5 with Dell/NVIDIA and ADNOC AIQ/Microsoft/G42 stack",
        cta="Is the scarce asset compute, data, workflows, or engineering trust?",
    ),
    RotationSlot(
        name="conference-to-field",
        highlight="The best AI energy ideas are leaving the conference stage",
        visual_style="conference-to-field",
        company_a="ADNOC",
        company_b="TotalEnergies",
        source_angle="ADIPEC and ENACT signals turning into deployed AI tools and major power partnerships",
        cta="What proves a demo is becoming a field deployment?",
    ),
    RotationSlot(
        name="low-carbon-optimization",
        highlight="AI can make energy more reliable, affordable, and lower-carbon",
        visual_style="optimization-flywheel",
        company_a="TotalEnergies",
        company_b="ADNOC",
        source_angle="AI for renewables, customer energy optimization, emissions reduction, and process monitoring",
        cta="Where does AI cut the most waste: assets, customers, planning, or maintenance?",
    ),
    RotationSlot(
        name="partnership-layer",
        highlight="The winning model is partnerships, not solo AI experiments",
        visual_style="partnership-map",
        company_a="TotalEnergies",
        company_b="ADNOC",
        source_angle="Mistral AI, Dell, NVIDIA, Microsoft, G42, AIQ, Masdar, and XRG as industrial AI partners",
        cta="Which partnership pattern will compound fastest?",
    ),
)


SOURCE_REFERENCES: tuple[str, ...] = (
    "TotalEnergies Pangea 5 with Dell Technologies and NVIDIA",
    "TotalEnergies and Mistral AI joint innovation lab",
    "TotalEnergies power agreements supporting Google data centers",
    "ADNOC ENERGYai with AIQ, Microsoft, and G42",
    "ADNOC, Masdar, XRG, and Microsoft AI-for-energy / energy-for-AI agreement",
    "ADIPEC 2025 AI and energy program",
)


def prepare_rotating_linkedin_post(
    con: sqlite3.Connection,
    *,
    industry: str = "energy",
    post_date: date | None = None,
    topic: str | None = None,
    asset_dir: str | Path = "data",
    out: str | Path | None = None,
    rotation_index: int | None = None,
) -> dict[str, Any]:
    if industry.lower() != "energy":
        raise ValueError("Only the energy rotation is implemented.")
    post_date = post_date or datetime.now().date()
    slot = _slot_for_date(post_date, rotation_index=rotation_index)
    topic_text = topic or "AI applications in the energy industry"
    body = _post_body(slot, post_date=post_date, topic=topic_text)
    subject = f"Rotating LinkedIn post: {slot.name} - {post_date.isoformat()}"
    goal_id = _latest_goal_id(con)
    draft_id = create_custom_draft(
        con,
        channel="linkedin_post",
        subject=subject,
        body=body,
        rationale=f"Rotating daily LinkedIn post: highlight={slot.highlight}; visual={slot.visual_style}",
        goal_id=goal_id,
    )

    asset_dir = Path(asset_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    svg_path, png_path = _write_visual(slot, post_date=post_date, asset_dir=asset_dir)
    event_id = record_draft_event(
        con,
        draft_id=draft_id,
        event_type="prepared",
        reason_code="daily_linkedin_rotation",
        note=f"Prepared rotating LinkedIn post with {slot.name} / {slot.visual_style}.",
        external_ref=str(png_path or svg_path),
        metadata={
            "industry": industry,
            "rotation": slot.name,
            "highlight": slot.highlight,
            "visual_style": slot.visual_style,
            "post_date": post_date.isoformat(),
            "svg_path": str(svg_path),
            "png_path": str(png_path) if png_path else None,
        },
    )
    result = {
        "draft_id": draft_id,
        "event_id": event_id,
        "date": post_date.isoformat(),
        "industry": industry,
        "rotation": slot.name,
        "highlight": slot.highlight,
        "visual_style": slot.visual_style,
        "body": body,
        "svg_path": str(svg_path),
        "png_path": str(png_path) if png_path else None,
        "sources": list(SOURCE_REFERENCES),
    }
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(format_rotating_linkedin_report(result), encoding="utf-8")
    return result


def format_rotating_linkedin_report(result: dict[str, Any]) -> str:
    visual_path = result.get("png_path") or result.get("svg_path")
    lines = [
        f"# Rotating LinkedIn Post - {result['date']}",
        "",
        f"- Draft ID: {result['draft_id']}",
        f"- Rotation: {result['rotation']}",
        f"- Highlight: {result['highlight']}",
        f"- Visual style: {result['visual_style']}",
        f"- Visual: {visual_path}",
        "",
        "## Post",
        "",
        result["body"],
        "",
        "## Suggested Attachment",
        "",
    ]
    if visual_path:
        rendered_path = str(Path(visual_path).resolve()) if not Path(visual_path).is_absolute() else str(visual_path)
        lines.append(f"![LinkedIn visual]({rendered_path})")
    lines.extend(["", "## Source Notes", ""])
    lines.extend(f"- {source}" for source in result["sources"])
    lines.extend(
        [
            "",
            "## Tracking",
            "",
            "Record comments by option, plus impressions, reactions, reposts, profile views, follows, useful replies, and follow-up candidates.",
        ]
    )
    return "\n".join(lines)


def preview_rotation(days: int = 14, *, start: date | None = None) -> list[dict[str, str]]:
    start = start or datetime.now().date()
    rows: list[dict[str, str]] = []
    for offset in range(max(0, days)):
        current = date.fromordinal(start.toordinal() + offset)
        slot = _slot_for_date(current)
        rows.append(
            {
                "date": current.isoformat(),
                "rotation": slot.name,
                "highlight": slot.highlight,
                "visual_style": slot.visual_style,
            }
        )
    return rows


def format_rotation_preview(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "No rotation rows."
    return "\n".join(
        f"{row['date']} | {row['rotation']} | {row['visual_style']} | {row['highlight']}"
        for row in rows
    )


def _slot_for_date(post_date: date, *, rotation_index: int | None = None) -> RotationSlot:
    index = rotation_index if rotation_index is not None else post_date.toordinal()
    return ENERGY_ROTATION[index % len(ENERGY_ROTATION)]


def _latest_goal_id(con: sqlite3.Connection) -> str | None:
    row = con.execute("SELECT id FROM goals WHERE status = 'active' ORDER BY created_at DESC LIMIT 1").fetchone()
    return str(row["id"]) if row else None


def _post_body(slot: RotationSlot, *, post_date: date, topic: str) -> str:
    options = _options(slot)
    return (
        f"Daily AI x energy note ({post_date.isoformat()}): {slot.highlight}.\n\n"
        f"The theme today is {topic}, but with a practical lens: what is actually moving from slides into assets, "
        "control rooms, data centers, and engineering workflows?\n\n"
        f"Two positive signals I would put on the map:\n\n"
        f"1. {slot.company_a}: { _company_angle(slot.company_a, slot) }\n\n"
        f"2. {slot.company_b}: { _company_angle(slot.company_b, slot) }\n\n"
        f"The bigger pattern: {slot.source_angle}.\n\n"
        "My take: energy companies with real assets, proprietary data, and serious technology partners are becoming "
        "AI operators, not just power suppliers. That is a much more interesting story than 'AI will use more electricity'.\n\n"
        f"{slot.cta}\n\n"
        f"Comment with one option:\n"
        f"A - {options[0]}\n"
        f"B - {options[1]}\n"
        f"C - {options[2]}\n"
        f"D - {options[3]}\n\n"
        "I will use the best answers to shape tomorrow's energy AI note."
    )


def _company_angle(company: str, slot: RotationSlot) -> str:
    total = (
        "a credible compute-and-power model: Pangea 5 with Dell/NVIDIA, a Mistral AI innovation lab, "
        "and renewable power deals that support data-center growth."
    )
    adnoc = (
        "a credible AI-inside-operations model: ENERGYai, AIQ/Microsoft/G42 collaboration, and a clear push "
        "to embed AI from operational workflows to strategic decisions."
    )
    if company == "TotalEnergies":
        return total
    if company == "ADNOC":
        return adnoc
    return "a practical industrial AI partnership model."


def _options(slot: RotationSlot) -> tuple[str, str, str, str]:
    if slot.name == "ai-power-demand":
        return ("New clean power", "Grid flexibility", "Land and interconnection", "Data-center efficiency")
    if slot.name == "agentic-operations":
        return ("Seismic and subsurface", "Maintenance and reliability", "Process monitoring", "Trading and dispatch")
    if slot.name == "hpc-and-industrial-data":
        return ("Compute", "Proprietary data", "Workflow design", "Engineering trust")
    if slot.name == "conference-to-field":
        return ("Field deployment", "Operator adoption", "Measurable ROI", "Partner ecosystem")
    if slot.name == "low-carbon-optimization":
        return ("Renewables output", "Asset efficiency", "Customer energy use", "Emissions reduction")
    if slot.name == "partnership-layer":
        return ("Energy + AI labs", "Energy + hyperscalers", "Energy + chip/HPC players", "Energy + startups")
    return ("TotalEnergies model", "ADNOC model", "Partnership model", "Too early")


def _write_visual(slot: RotationSlot, *, post_date: date, asset_dir: Path) -> tuple[Path, Path | None]:
    stem = f"linkedin-{post_date.isoformat()}-{slot.name}"
    svg_path = asset_dir / f"{stem}.svg"
    png_path = asset_dir / f"{stem}.png"
    svg_path.write_text(_visual_svg(slot, post_date=post_date), encoding="utf-8")
    converter = shutil.which("rsvg-convert") or "/opt/homebrew/bin/rsvg-convert"
    if Path(converter).exists() or shutil.which(converter):
        try:
            subprocess.run([converter, "-w", "1600", "-h", "900", str(svg_path), "-o", str(png_path)], check=True)
            return svg_path, png_path
        except (OSError, subprocess.CalledProcessError):
            return svg_path, None
    return svg_path, None


def _visual_svg(slot: RotationSlot, *, post_date: date) -> str:
    palette = _palette(slot.visual_style)
    title_block = _svg_text_lines(_wrap_text(slot.highlight, max_chars=48, max_lines=2), x=72, y=150, line_height=56, css_class="title")
    angle_block = _svg_text_lines(_wrap_text(slot.source_angle, max_chars=92, max_lines=2), x=72, y=236, line_height=34, css_class="sub")
    options = [html.escape(item) for item in _options(slot)]
    company_a = html.escape(slot.company_a)
    company_b = html.escape(slot.company_b)
    style_label = html.escape(slot.visual_style.replace("-", " ").title())
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{palette['bg0']}"/>
      <stop offset="0.55" stop-color="{palette['bg1']}"/>
      <stop offset="1" stop-color="{palette['bg2']}"/>
    </linearGradient>
    <pattern id="grid" width="42" height="42" patternUnits="userSpaceOnUse">
      <path d="M42 0H0V42" fill="none" stroke="#e2e8f0" stroke-width="1" opacity="0.06"/>
    </pattern>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="12" stdDeviation="14" flood-color="#000" flood-opacity="0.30"/>
    </filter>
    <style>
      .kicker{{font:800 23px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#a7f3d0;letter-spacing:0}}
      .title{{font:800 52px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#f8fafc;letter-spacing:0}}
      .sub{{font:500 25px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#dbeafe;letter-spacing:0}}
      .cardTitle{{font:800 34px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#0f172a;letter-spacing:0}}
      .cardText{{font:500 22px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#263247;letter-spacing:0}}
      .screen{{font:800 35px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#e0f2fe;letter-spacing:0}}
      .screenSmall{{font:500 22px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#bfdbfe;letter-spacing:0}}
      .option{{font:800 22px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#fef9c3;letter-spacing:0}}
      .footer{{font:500 18px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#cbd5e1;letter-spacing:0}}
    </style>
  </defs>
  <rect width="1600" height="900" fill="url(#bg)"/>
  <rect width="1600" height="900" fill="url(#grid)"/>
  <path d="M1120 0 L1600 0 L1600 250 L1260 250 Z" fill="{palette['accent']}" opacity="0.08"/>
  <path d="M0 690 L520 760 L520 900 L0 900 Z" fill="#f59e0b" opacity="0.07"/>
  <text x="72" y="90" class="kicker">DAILY AI X ENERGY ROTATION - {post_date.isoformat()} - {style_label}</text>
  {title_block}
  {angle_block}

  <g filter="url(#shadow)">
    <rect x="455" y="282" width="690" height="230" rx="30" fill="#102a43" stroke="{palette['accent']}" stroke-width="3"/>
    <text x="800" y="348" text-anchor="middle" class="screen">Industrial AI Deployment</text>
    <text x="800" y="390" text-anchor="middle" class="screenSmall">conference signal -> lab -> asset -> control room</text>
    <path d="M540 440 L1060 440" stroke="{palette['accent']}" stroke-width="5" opacity="0.75"/>
    <circle cx="610" cy="440" r="14" fill="#d9f99d"/>
    <circle cx="800" cy="440" r="14" fill="#d9f99d"/>
    <circle cx="990" cy="440" r="14" fill="#d9f99d"/>
  </g>

  <g filter="url(#shadow)">
    <rect x="88" y="310" width="390" height="190" rx="24" fill="#dbeafe"/>
    <text x="126" y="367" class="cardTitle">{company_a}</text>
    <text x="126" y="410" class="cardText">compute + power + AI lab</text>
    <text x="126" y="446" class="cardText">industrial data at scale</text>
  </g>

  <g filter="url(#shadow)">
    <rect x="1122" y="310" width="390" height="190" rx="24" fill="#dcfce7"/>
    <text x="1160" y="367" class="cardTitle">{company_b}</text>
    <text x="1160" y="410" class="cardText">agentic AI + operations</text>
    <text x="1160" y="446" class="cardText">control-room deployment</text>
  </g>

  <g filter="url(#shadow)">
    <rect x="88" y="582" width="1424" height="135" rx="28" fill="#0f172a" opacity="0.82"/>
    <text x="800" y="632" text-anchor="middle" class="screenSmall">Comment with one option</text>
    <text x="190" y="684" class="option">A {options[0]}</text>
    <text x="525" y="684" class="option">B {options[1]}</text>
    <text x="875" y="684" class="option">C {options[2]}</text>
    <text x="1220" y="684" class="option">D {options[3]}</text>
  </g>

  <text x="72" y="835" class="footer">Rotates daily: company proof point, power demand, operations, HPC/data, conference-to-field, low-carbon optimization, partnerships.</text>
</svg>
'''


def _wrap_text(text: str, *, max_chars: int, max_lines: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,;:") + "..."
    return lines or [text]


def _svg_text_lines(lines: list[str], *, x: int, y: int, line_height: int, css_class: str) -> str:
    return "\n  ".join(
        f'<text x="{x}" y="{y + index * line_height}" class="{css_class}">{html.escape(line)}</text>'
        for index, line in enumerate(lines)
    )


def _palette(visual_style: str) -> dict[str, str]:
    palettes = {
        "conference-stage": {"bg0": "#07111f", "bg1": "#0f2f3a", "bg2": "#182032", "accent": "#38bdf8"},
        "data-center-and-grid": {"bg0": "#08111f", "bg1": "#12342e", "bg2": "#1f2937", "accent": "#34d399"},
        "control-room": {"bg0": "#0b1020", "bg1": "#1e293b", "bg2": "#312e81", "accent": "#a78bfa"},
        "compute-stack": {"bg0": "#09090b", "bg1": "#1f2937", "bg2": "#164e63", "accent": "#22d3ee"},
        "conference-to-field": {"bg0": "#111827", "bg1": "#3f2f16", "bg2": "#0f172a", "accent": "#fbbf24"},
        "optimization-flywheel": {"bg0": "#06281f", "bg1": "#134e4a", "bg2": "#1e293b", "accent": "#5eead4"},
        "partnership-map": {"bg0": "#111827", "bg1": "#3b264f", "bg2": "#172554", "accent": "#c084fc"},
    }
    return palettes.get(visual_style, palettes["conference-stage"])
