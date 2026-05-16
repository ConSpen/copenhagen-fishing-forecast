"""Builds the recommendation email and sends it via iCloud SMTP.

The module owns the shape of a "report" — WindowCandidate and Recommendation —
because it is the component that renders them. The pipeline assembles these
objects and hands them here.

Email rendering is deliberately old-fashioned: inline styles, table-based
layout, a plain-text alternative. Email clients are not browsers, and this
keeps the message looking right in Apple Mail, Gmail and Outlook alike.

Sending uses Apple's SMTP server with an app-specific password — never your
main Apple ID password. Credentials come from the environment, never the repo.
"""
from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

from .astro import Window
from .config import Config, Spot
from .scoring import ComponentScore, WindowScore
from .species import SpeciesRecommendation

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.mail.me.com"
SMTP_PORT = 587
_SMTP_TIMEOUT = 30  # seconds

# Order in which scoring factors are shown in the conditions table.
_FACTOR_ORDER = [
    "wind",
    "solunar",
    "pressure",
    "cloud",
    "precipitation",
    "water_temp",
    "wave",
]

# External resources surfaced in the email footer.
_SPOTS_MAP_URL = "https://fishingindenmark.info/de/angelplatze"
_RULES_URL = "https://fishingindenmark.info/de/informationen-und-regeln"
_LICENCE_URL = "https://fisketegn.dk/"
_FACTOR_LABELS = {
    "wind": "Wind",
    "solunar": "Sun &amp; moon",
    "pressure": "Pressure",
    "cloud": "Cloud",
    "precipitation": "Rain",
    "water_temp": "Water temp",
    "wave": "Waves",
}

# Palette — muted, calm, readable in every mail client.
_C_BG = "#f4f5f7"
_C_CARD = "#ffffff"
_C_TEXT = "#1f2933"
_C_MUTED = "#677788"
_C_ACCENT = "#2c6e6b"
_C_ACCENT_DARK = "#1f4f4d"
_C_BORDER = "#e3e6ea"
_C_STAR = "#e0a32e"
_C_STAR_EMPTY = "#d4d8dd"
_C_GOOD = "#3f7d4f"
_C_WARN = "#b9892f"
_C_BAD = "#b4452f"


class EmailError(RuntimeError):
    """Raised when the email cannot be sent."""


@dataclass(frozen=True)
class WindowCandidate:
    """A scored, fully-described fishing window — a candidate for the email."""

    spot: Spot
    window: Window
    score: WindowScore
    species: SpeciesRecommendation


@dataclass(frozen=True)
class Recommendation:
    """The single best window plus any other windows worth knowing about."""

    best: WindowCandidate
    alternatives: list[WindowCandidate]
    generated_at: datetime


# --------------------------------------------------------------------------
# Subject + body
# --------------------------------------------------------------------------

def build_subject(rec: Recommendation, config: Config) -> str:
    best = rec.best
    day = best.window.start.strftime("%a %d %b")
    return (
        f"{config.email.subject_prefix}: {day}, {best.window.daypart} "
        f"at {best.spot.name} ({_stars_text(best.score.stars)})"
    )


def build_html(rec: Recommendation) -> str:
    """Render the full HTML email."""
    best = rec.best
    win = best.window
    score = best.score
    species = best.species

    days_away = _days_away(win.date, rec.generated_at.date())
    conditions_rows = "".join(
        _condition_row(score.components[f])
        for f in _FACTOR_ORDER
        if f in score.components
    )
    alternatives_block = _alternatives_block(rec.alternatives)
    guide_block = _guide_link_block(species.primary)
    secondary_block = ""
    if species.secondary is not None:
        secondary_block = (
            f'<p style="margin:10px 0 0;font-size:14px;color:{_C_MUTED};">'
            f"<strong style=\"color:{_C_TEXT};\">Backup:</strong> "
            f"{species.secondary.name} ({species.secondary.danish_name}). "
            f"{_escape(species.secondary.where)}</p>"
        )
    species_note = ""
    if species.primary.note:
        species_note = (
            f'<p style="margin:12px 0 0;font-size:13px;color:{_C_MUTED};'
            f'font-style:italic;">{_escape(species.primary.note)}</p>'
        )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{_C_BG};">
<div style="display:none;max-height:0;overflow:hidden;">\
{win.start.strftime('%A %d %B')}, {win.daypart} at {best.spot.name} — \
{score.headline}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="background:{_C_BG};padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" \
style="max-width:600px;width:100%;background:{_C_CARD};border:1px solid \
{_C_BORDER};border-radius:10px;overflow:hidden;font-family:-apple-system,\
BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">

  <!-- Header -->
  <tr><td style="background:{_C_ACCENT};padding:22px 28px;">
    <p style="margin:0;font-size:13px;letter-spacing:.08em;text-transform:\
uppercase;color:#bfe0dd;">Copenhagen fishing forecast</p>
    <p style="margin:6px 0 0;font-size:22px;font-weight:700;color:#ffffff;">\
A window worth planning for</p>
    <p style="margin:8px 0 0;font-size:16px;color:#dff0ee;">\
{_render_stars(score.stars)}</p>
  </td></tr>

  <!-- The window -->
  <tr><td style="padding:24px 28px 8px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td style="padding:4px 0;font-size:14px;color:{_C_MUTED};width:90px;">\
When</td>
        <td style="padding:4px 0;font-size:16px;color:{_C_TEXT};\
font-weight:600;">{win.start.strftime('%A %d %B')} \
&nbsp;<span style="color:{_C_MUTED};font-weight:400;">({days_away})</span></td>
      </tr>
      <tr>
        <td style="padding:4px 0;font-size:14px;color:{_C_MUTED};">Time</td>
        <td style="padding:4px 0;font-size:16px;color:{_C_TEXT};\
font-weight:600;">{win.start.strftime('%H:%M')}–{win.end.strftime('%H:%M')} \
&nbsp;<span style="color:{_C_MUTED};font-weight:400;">\
({win.daypart}, around {'sunrise' if win.daypart == 'dawn' else 'sunset'})\
</span></td>
      </tr>
      <tr>
        <td style="padding:4px 0;font-size:14px;color:{_C_MUTED};">Where</td>
        <td style="padding:4px 0;font-size:16px;color:{_C_TEXT};\
font-weight:600;">{best.spot.name}</td>
      </tr>
      <tr>
        <td></td>
        <td style="padding:2px 0 4px;font-size:13px;color:{_C_MUTED};">\
{_escape(best.spot.notes)}</td>
      </tr>
    </table>
  </td></tr>

  <!-- Why now -->
  <tr><td style="padding:14px 28px 4px;">
    <p style="margin:0 0 4px;font-size:13px;letter-spacing:.06em;\
text-transform:uppercase;color:{_C_ACCENT_DARK};font-weight:700;">Why now</p>
    <p style="margin:0 0 12px;font-size:14px;color:{_C_TEXT};">\
{_escape(score.headline)}</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="border:1px solid {_C_BORDER};border-radius:8px;border-collapse:\
separate;overflow:hidden;">
      {conditions_rows}
    </table>
  </td></tr>

  <!-- Target -->
  <tr><td style="padding:20px 28px 4px;">
    <p style="margin:0 0 4px;font-size:13px;letter-spacing:.06em;\
text-transform:uppercase;color:{_C_ACCENT_DARK};font-weight:700;">\
What to target</p>
    <p style="margin:0;font-size:17px;color:{_C_TEXT};font-weight:600;">\
{species.primary.name} \
<span style="color:{_C_MUTED};font-weight:400;font-size:15px;">\
({species.primary.danish_name})</span></p>
    <p style="margin:6px 0 0;font-size:14px;color:{_C_TEXT};">\
{_escape(species.reasoning)}</p>
    {guide_block}
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="margin-top:12px;background:#f0f5f4;border-radius:8px;">
      <tr><td style="padding:14px 16px;">
        <p style="margin:0 0 4px;font-size:12px;letter-spacing:.05em;\
text-transform:uppercase;color:{_C_ACCENT_DARK};font-weight:700;">Setup</p>
        <p style="margin:0;font-size:14px;color:{_C_TEXT};line-height:1.5;">\
{_escape(species.primary.setup)}</p>
        <p style="margin:8px 0 0;font-size:14px;color:{_C_TEXT};\
line-height:1.5;">{_escape(species.primary.where)}</p>
      </td></tr>
    </table>
    {secondary_block}
    {species_note}
  </td></tr>

  {alternatives_block}

  <!-- Footer -->
  <tr><td style="padding:18px 28px 24px;">
    <hr style="border:none;border-top:1px solid {_C_BORDER};margin:0 0 14px;">
    <p style="margin:0 0 6px;font-size:12px;letter-spacing:.06em;\
text-transform:uppercase;color:{_C_ACCENT_DARK};font-weight:700;">Resources</p>
    <p style="margin:0 0 10px;font-size:13px;color:{_C_TEXT};line-height:1.55;">\
<a href="{_SPOTS_MAP_URL}" style="color:{_C_ACCENT};">Spot map (1,600+ spots \
across Denmark)</a> &nbsp;·&nbsp; \
<a href="{_RULES_URL}" style="color:{_C_ACCENT};">Rules &amp; minimum sizes</a> \
&nbsp;·&nbsp; \
<a href="{_LICENCE_URL}" style="color:{_C_ACCENT};">Buy / renew fisketegn</a>\
</p>
    <p style="margin:0 0 8px;font-size:12px;color:{_C_MUTED};line-height:1.6;">\
This is a planning heads-up, not a guarantee. Forecasts this far out still \
shift, so check the conditions again a day or two before you commit. A Danish \
fishing licence (fisketegn) is required ages 18–65, and sea trout in the \
Øresund have a 40 cm minimum size.</p>
    <p style="margin:0;font-size:12px;color:{_C_MUTED};">\
Generated {rec.generated_at.strftime('%Y-%m-%d %H:%M')} · data from \
Open-Meteo and solunar.org · solunar timing is a soft heuristic.</p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>
"""


def build_plaintext(rec: Recommendation) -> str:
    """A plain-text alternative for clients that do not render HTML."""
    best = rec.best
    win = best.window
    species = best.species
    lines = [
        "COPENHAGEN FISHING FORECAST",
        f"A window worth planning for — {_stars_text(best.score.stars)}",
        "",
        f"When:  {win.start.strftime('%A %d %B')} "
        f"({_days_away(win.date, rec.generated_at.date())})",
        f"Time:  {win.start.strftime('%H:%M')}-{win.end.strftime('%H:%M')} "
        f"({win.daypart})",
        f"Where: {best.spot.name} — {best.spot.notes}",
        "",
        "WHY NOW",
        f"  {best.score.headline}",
    ]
    for factor in _FACTOR_ORDER:
        comp = best.score.components.get(factor)
        if comp:
            lines.append(
                f"  - {_FACTOR_LABELS[factor].replace('&amp;', '&')}: "
                f"{comp.note}"
            )
    lines += [
        "",
        "WHAT TO TARGET",
        f"  {species.primary.name} ({species.primary.danish_name})",
        f"  {species.reasoning}",
        f"  Setup: {species.primary.setup}",
        f"  Where: {species.primary.where}",
    ]
    if species.primary.guide_url:
        lines.append(f"  Guide: {species.primary.guide_url}")
    if species.primary.note:
        lines.append(f"  Note: {species.primary.note}")
    if rec.alternatives:
        lines += ["", "OTHER WINDOWS WORTH KNOWING"]
        for alt in rec.alternatives:
            lines.append(
                f"  - {alt.window.start.strftime('%a %d %b')}, "
                f"{alt.window.daypart} at {alt.spot.name} "
                f"({_stars_text(alt.score.stars)}) — {alt.score.headline}"
            )
    lines += [
        "",
        "RESOURCES",
        f"  Spot map:  {_SPOTS_MAP_URL}",
        f"  Rules:     {_RULES_URL}",
        f"  Fisketegn: {_LICENCE_URL}",
        "",
        "This is a planning heads-up, not a guarantee — re-check a day or two "
        "before. A Danish fishing licence is required ages 18-65, and sea "
        "trout in the Oresund have a 40 cm minimum size.",
        f"Generated {rec.generated_at.strftime('%Y-%m-%d %H:%M')} · "
        f"data from Open-Meteo and solunar.org.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Sending / dry-run output
# --------------------------------------------------------------------------

def send_email(
    subject: str,
    html: str,
    plaintext: str,
    recipient: str,
    smtp_user: str,
    smtp_password: str,
) -> None:
    """Send the email through iCloud SMTP. Raises EmailError on failure."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_user
    message["To"] = recipient
    message.set_content(plaintext)
    message.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=_SMTP_TIMEOUT) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"Could not send the email: {exc}") from exc
    logger.info("Email sent to %s.", recipient)


def save_dry_run(
    subject: str, html: str, plaintext: str, output_dir: str | Path
) -> Path:
    """Write the email to disk instead of sending it (used by --dry-run)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    html_path = output_dir / f"email-{stamp}.html"
    text_path = output_dir / f"email-{stamp}.txt"
    html_path.write_text(html, encoding="utf-8")
    text_path.write_text(
        f"Subject: {subject}\n\n{plaintext}\n", encoding="utf-8"
    )
    logger.info("Dry run — email written to %s", html_path)
    return html_path


# --------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------

def _condition_row(component: ComponentScore) -> str:
    label = _FACTOR_LABELS.get(component.factor, component.factor.title())
    colour = _score_colour(component.score)
    pct = round(component.score * 100)
    return f"""<tr>
      <td style="padding:9px 14px;font-size:13px;color:{_C_MUTED};\
border-bottom:1px solid {_C_BORDER};width:96px;vertical-align:top;">{label}</td>
      <td style="padding:9px 6px;font-size:13px;color:{_C_TEXT};\
border-bottom:1px solid {_C_BORDER};">{_escape(component.note)}</td>
      <td style="padding:9px 14px;font-size:12px;font-weight:700;\
color:{colour};border-bottom:1px solid {_C_BORDER};text-align:right;\
white-space:nowrap;vertical-align:top;">{pct}%</td>
    </tr>"""


def _guide_link_block(species) -> str:
    """A small linked line pointing at the full species guide, if one is set."""
    if not species.guide_url:
        return ""
    return (
        f'<p style="margin:10px 0 0;font-size:13px;">'
        f'<a href="{species.guide_url}" style="color:{_C_ACCENT};'
        f'text-decoration:underline;">'
        f'Full {species.name.lower()} guide on fishingindenmark.info &rarr;'
        f"</a></p>"
    )


def _alternatives_block(alternatives: list[WindowCandidate]) -> str:
    if not alternatives:
        return ""
    rows = ""
    for alt in alternatives:
        rows += f"""<tr>
        <td style="padding:7px 0;font-size:14px;color:{_C_TEXT};">\
{alt.window.start.strftime('%a %d %b')}, {alt.window.daypart} \
&middot; {alt.spot.name}</td>
        <td style="padding:7px 0;font-size:13px;color:{_C_STAR};\
text-align:right;white-space:nowrap;">{_stars_text(alt.score.stars)}</td>
      </tr>
      <tr><td colspan="2" style="padding:0 0 6px;font-size:12px;\
color:{_C_MUTED};border-bottom:1px solid {_C_BORDER};">\
{_escape(alt.score.headline)}</td></tr>"""
    return f"""<tr><td style="padding:18px 28px 4px;">
    <p style="margin:0 0 6px;font-size:13px;letter-spacing:.06em;\
text-transform:uppercase;color:{_C_ACCENT_DARK};font-weight:700;">\
Other windows worth knowing</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      {rows}
    </table>
  </td></tr>"""


def _render_stars(stars: float) -> str:
    """An HTML star row: filled, an optional half, then empty — plus the number."""
    full = int(stars)
    half = (stars - full) >= 0.5
    empty = 5 - full - (1 if half else 0)
    filled_span = f'<span style="color:{_C_STAR};">{"&#9733;" * full}</span>'
    half_span = (
        f'<span style="color:{_C_STAR};">&frac12;</span>' if half else ""
    )
    empty_span = f'<span style="color:{_C_STAR_EMPTY};">{"&#9734;" * empty}</span>'
    return (
        f'{filled_span}{half_span}{empty_span} &nbsp;'
        f'<span style="font-size:14px;">{stars:.1f} / 5</span>'
    )


def _stars_text(stars: float) -> str:
    """A compact text star rating, e.g. '4.5*'."""
    return f"{stars:.1f}★"


def _score_colour(score: float) -> str:
    if score >= 0.70:
        return _C_GOOD
    if score >= 0.45:
        return _C_WARN
    return _C_BAD


def _days_away(window_date: date, today: date) -> str:
    delta = (window_date - today).days
    if delta <= 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    return f"in {delta} days"


def _escape(text: str) -> str:
    """Minimal HTML escaping for text taken from config/data."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
