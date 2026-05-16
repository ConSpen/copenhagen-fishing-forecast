"""Seasonal target species and matching spinning setups for the Øresund.

This is reference data for shore *spinning* specifically — the four species you
can realistically cover with a spinning rod from Nordhavn and Sydhavn. Each
species carries the months it is worth targeting, a sea-surface-temperature
band it prefers, and a concrete setup recommendation.

The recommender ranks species for a given month and water temperature, so the
email can tell you what to go for and what to tie on.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Species:
    name: str                       # English name
    danish_name: str                # Danish name (useful at the tackle shop)
    months: frozenset[int]          # months it is worth targeting (1–12)
    peak_months: frozenset[int]     # months it is at its best
    water_temp_range: tuple[float, float]  # preferred sea-surface temp band, °C
    setup: str                      # the spinning setup to use
    where: str                      # where and how to fish it
    note: str = ""                  # caveats — regulations, technique, etc.
    guide_url: str = ""             # deep link to the matching guide on fishingindenmark.info


@dataclass(frozen=True)
class SpeciesRecommendation:
    primary: Species
    secondary: Species | None
    reasoning: str
    season_status: str              # "peak" | "opening" | "active" | "closing"


# --------------------------------------------------------------------------
# The Øresund shore-spinning species table
# --------------------------------------------------------------------------

SEA_TROUT = Species(
    name="Sea trout",
    danish_name="havørred",
    months=frozenset({1, 2, 3, 4, 5, 9, 10, 11, 12}),
    peak_months=frozenset({3, 4, 10, 11}),
    water_temp_range=(1.0, 14.0),
    setup=(
        "9–10 ft coastal spinning rod, 0.20 mm braid to a 0.28 mm fluorocarbon "
        "leader. 12–24 g coastal spoons (kystblink — a Møresild or Snurrebassen "
        "type) as the workhorse; a small floating wobbler when it is calm. Vary "
        "the retrieve speed and work in pauses."
    ),
    where=(
        "Work points, drop-offs and current edges. The dawn window is the one "
        "to fish hardest — first light is prime."
    ),
    note=(
        "A Danish fishing licence (fisketegn) is required, ages 18–65. The "
        "minimum size in the Øresund is 40 cm; release coloured (spawning) fish."
    ),
    guide_url="https://fishingindenmark.info/de/angeltipps/meerforellenangeln-an-der-kuste",
)

GARFISH = Species(
    name="Garfish",
    danish_name="hornfisk",
    months=frozenset({4, 5, 6, 7}),
    peak_months=frozenset({5, 6}),
    water_temp_range=(7.0, 16.0),
    setup=(
        "Light spinning rod with 8–18 g slim spoons fished fast and high in the "
        "water — or a strip of mackerel under a casting float. The classic "
        "trick: a tuft of red wool above the hook, so their bony beaks tangle "
        "in it and you barely need the hook to hold them."
    ),
    where=(
        "They cruise near the surface, often within a rod-length or two of the "
        "edge. Keep the lure shallow and the retrieve quick."
    ),
    note="",
    guide_url="https://fishingindenmark.info/de/angeltipps/hornhechte-makrelen-und-heringe",
)

MACKEREL = Species(
    name="Mackerel",
    danish_name="makrel",
    months=frozenset({6, 7, 8, 9}),
    peak_months=frozenset({7, 8}),
    water_temp_range=(12.0, 20.0),
    setup=(
        "Small shiny spoons (10–25 g) or a short string of feathers fished on a "
        "fast retrieve. When a shoal is under you it is frantic, then it goes "
        "quiet — keep moving to stay on the fish."
    ),
    where=(
        "Best from the deeper harbour edges and breakwater ends, where they "
        "corner baitfish against the structure."
    ),
    note="",
    guide_url="https://fishingindenmark.info/de/angeltipps/hornhechte-makrelen-und-heringe",
)

COD = Species(
    name="Cod",
    danish_name="torsk",
    months=frozenset({1, 2, 3, 11, 12}),
    peak_months=frozenset({1, 2}),
    water_temp_range=(-1.0, 9.0),
    setup=(
        "A heavier outfit with 20–40 g pirks and soft-plastic jigs worked along "
        "the bottom near the channel edges and the deepest water you can reach."
    ),
    where="Fish close to the bottom by the deepest water within casting range.",
    note=(
        "Øresund cod stocks are under real pressure and the rules change — "
        "check the current Fiskeristyrelsen / DTU Aqua size limits, bag limits "
        "and any closed period before targeting them."
    ),
    guide_url="https://fishingindenmark.info/de/angeltipps/dorsche-von-der-kuste-einer-mole-oder-einem-boot-aus",
)

ALL_SPECIES: list[Species] = [SEA_TROUT, GARFISH, MACKEREL, COD]


# --------------------------------------------------------------------------
# Recommender
# --------------------------------------------------------------------------

def _species_score(
    species: Species, month: int, water_temp_c: float | None
) -> float:
    """Score how well a species fits a month and (optional) water temperature."""
    if month not in species.months:
        return 0.0

    score = 1.0
    if month in species.peak_months:
        score += 1.0

    if water_temp_c is not None:
        low, high = species.water_temp_range
        if low <= water_temp_c <= high:
            score += 0.5
        else:
            # Penalise by how far outside the preferred band we are.
            distance = (
                low - water_temp_c
                if water_temp_c < low
                else water_temp_c - high
            )
            score += max(-0.8, -0.2 * distance)

    return score


def season_status_for(species: Species, month: int) -> str:
    """Where in its season a species sits this month: peak, active, opening, closing."""
    if month not in species.months:
        return "out_of_season"
    if month in species.peak_months:
        return "peak"
    prev_month = 12 if month == 1 else month - 1
    next_month = 1 if month == 12 else month + 1
    prev_in = prev_month in species.months
    next_in = next_month in species.months
    if not prev_in and next_in:
        return "opening"
    if prev_in and not next_in:
        return "closing"
    return "active"


def recommend(
    month: int, water_temp_c: float | None
) -> SpeciesRecommendation:
    """Recommend a primary (and possibly secondary) species for the conditions."""
    scored = sorted(
        ((sp, _species_score(sp, month, water_temp_c)) for sp in ALL_SPECIES),
        key=lambda pair: pair[1],
        reverse=True,
    )

    primary, _ = scored[0]
    secondary: Species | None = None
    if len(scored) > 1:
        candidate, candidate_score = scored[1]
        # Only offer a second option if it is genuinely in season.
        if candidate_score >= 1.0:
            secondary = candidate

    status = season_status_for(primary, month)
    return SpeciesRecommendation(
        primary=primary,
        secondary=secondary,
        reasoning=_reasoning(primary, secondary, month, water_temp_c, status),
        season_status=status,
    )


# Per-status lead phrasing for the primary species. Keep one sentence each.
_STATUS_LEADS = {
    "peak": (
        "{name} ({dk}) are at their seasonal peak this month in the Øresund"
    ),
    "opening": (
        "{name} ({dk}) are just back in season — the run is opening up"
    ),
    "closing": (
        "{name} ({dk}) are tailing off — this could be one of the last "
        "weeks of the run"
    ),
    "active": (
        "{name} ({dk}) are in season and the best bet right now"
    ),
}


def _reasoning(
    primary: Species,
    secondary: Species | None,
    month: int,
    water_temp_c: float | None,
    status: str,
) -> str:
    """A short explanation of why these species were picked."""
    parts = [
        _STATUS_LEADS[status].format(name=primary.name, dk=primary.danish_name)
    ]

    if water_temp_c is not None:
        low, high = primary.water_temp_range
        if low <= water_temp_c <= high:
            parts.append(
                f"the {water_temp_c:.0f} °C water sits right in their range"
            )
        else:
            parts.append(
                f"the {water_temp_c:.0f} °C water is a little outside their "
                f"ideal band, so expect them to be moving"
            )

    sentence = ", and ".join(parts) + "."

    if secondary is not None:
        sentence += (
            f" {secondary.name} ({secondary.danish_name}) are the backup "
            f"option if the {primary.name.lower()} are quiet."
        )

    return sentence
