"""Moon naming conventions for the Lunar Phase sensor.

Two public naming functions are provided — one per convention. Both share
the same underlying algorithm:

  Step 1 — Calendar name: simple month-based lookup. If this is the second
            full moon in the calendar month the name is "Blue Moon".

  Step 2 — Equinox override: if this full moon is the Harvest Moon (closest
            full moon to the autumnal equinox) replace the name with
            "Harvest Moon" (or "Harvest/Blue Moon" if it was already a Blue
            Moon). Likewise for the Hunter's Moon (first full moon after the
            Harvest Moon).

The slash format — e.g. "Hunter's/Blue Moon" — is used whenever a named
moon coincides with a Blue Moon.

Additional conventions (e.g. Chinese lunar calendar) can be added as
new public functions that call _apply_naming() with their own month table.
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Optional

from skyfield import almanac

_LOGGER = logging.getLogger(__name__)

# Phase angle boundaries for moon phase (degrees → phase name). Phase angle 0→360 over one lunar cycle.
# Last entry (357°) means anything ≥357° is New Moon; exit angle is 3°.
MOON_PHASE_BOUNDARIES = [
    (0,   "New Moon"),
    (3,   "Waxing Crescent"),
    (90,  "First Quarter"),
    (93,  "Waxing Gibbous"),
    (177, "Full Moon"),
    (183, "Waning Gibbous"),
    (270, "Third Quarter"),
    (273, "Waning Crescent"),
    (357, "New Moon"),
]

# MDI icons for each moon phase (mdi:moon-last-quarter is the correct MDI name for Third Quarter)
MOON_PHASE_ICONS = {
    "New Moon":        "mdi:moon-new",
    "Waxing Crescent": "mdi:moon-waxing-crescent",
    "First Quarter":   "mdi:moon-first-quarter",
    "Waxing Gibbous":  "mdi:moon-waxing-gibbous",
    "Full Moon":       "mdi:moon-full",
    "Waning Gibbous":  "mdi:moon-waning-gibbous",
    "Third Quarter":   "mdi:moon-last-quarter",
    "Waning Crescent": "mdi:moon-waning-crescent",
}

# ---------------------------------------------------------------------------
# Month name tables — October is intentionally absent from both; it is
# always either Harvest Moon or Hunter's Moon (verified astronomically).
# ---------------------------------------------------------------------------

_NORTH_AMERICAN_NAMES: dict[int, str] = {
    1:  "Wolf Moon",
    2:  "Snow Moon",
    3:  "Worm Moon",
    4:  "Pink Moon",
    5:  "Flower Moon",
    6:  "Strawberry Moon",
    7:  "Buck Moon",
    8:  "Sturgeon Moon",
    9:  "Corn Moon",
    11: "Beaver Moon",
    12: "Cold Moon",
}

_CELTIC_PAGAN_NAMES: dict[int, str] = {
    1:  "Wolf Moon",
    2:  "Storm Moon",
    3:  "Chaste Moon",
    4:  "Seed Moon",
    5:  "Hare Moon",
    6:  "Mead Moon",
    7:  "Hay Moon",
    8:  "Grain Moon",
    9:  "Wine Moon",
    11: "Snow Moon",
    12: "Cold Moon",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_all_full_moons_in_window(
    from_dt: datetime,
    to_dt: datetime,
    eph,
    ts,
) -> list[datetime]:
    """Return UTC datetimes for all full moons between from_dt and to_dt."""
    t_start = ts.from_datetime(from_dt)
    t_end   = ts.from_datetime(to_dt)
    times, events = almanac.find_discrete(t_start, t_end, almanac.moon_phases(eph))
    return [
        t.utc_datetime().replace(tzinfo=timezone.utc)
        for t, e in zip(times, events)
        if e == 2  # 2 = Full Moon in Skyfield's moon_phases
    ]


def _find_autumnal_equinox(year: int, eph, ts) -> datetime:
    """Return the UTC datetime of the autumnal equinox for the given year."""
    t_start = ts.from_datetime(datetime(year, 7, 1, tzinfo=timezone.utc))
    t_end   = ts.from_datetime(datetime(year, 11, 1, tzinfo=timezone.utc))
    times, events = almanac.find_discrete(t_start, t_end, almanac.seasons(eph))
    for t, e in zip(times, events):
        if e == 2:  # 2 = autumnal equinox in Skyfield's seasons
            return t.utc_datetime().replace(tzinfo=timezone.utc)
    _LOGGER.warning(f"Autumnal equinox not found for {year}, using Sept 22 fallback")
    return datetime(year, 9, 22, 12, 0, 0, tzinfo=timezone.utc)


def _find_harvest_moon(year: int, eph, ts) -> datetime:
    """Return the Harvest Moon: full moon closest to the autumnal equinox."""
    equinox = _find_autumnal_equinox(year, eph, ts)
    full_moons = _find_all_full_moons_in_window(
        equinox - timedelta(days=45),
        equinox + timedelta(days=45),
        eph, ts,
    )
    if not full_moons:
        _LOGGER.warning(f"No full moons found near {year} autumnal equinox")
        return equinox
    return min(full_moons, key=lambda dt: abs((dt - equinox).total_seconds()))


def _find_hunter_moon(year: int, eph, ts) -> datetime:
    """Return the Hunter's Moon: first full moon after the Harvest Moon."""
    harvest = _find_harvest_moon(year, eph, ts)
    full_moons = _find_all_full_moons_in_window(
        harvest + timedelta(hours=1),
        harvest + timedelta(days=35),
        eph, ts,
    )
    if full_moons:
        return full_moons[0]
    _LOGGER.warning(f"Hunter's Moon not found after {harvest}, using +30 day fallback")
    return harvest + timedelta(days=30)


def _is_second_full_moon_in_month(full_moon_dt: datetime, eph, ts) -> bool:
    """Return True if full_moon_dt is the second (or later) full moon in its calendar month."""
    year, month = full_moon_dt.year, full_moon_dt.month
    days_in_month = monthrange(year, month)[1]
    month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    month_end   = datetime(year, month, days_in_month, 23, 59, 59, tzinfo=timezone.utc)
    full_moons  = sorted(_find_all_full_moons_in_window(month_start, month_end, eph, ts))
    for i, fm in enumerate(full_moons):
        if abs((fm - full_moon_dt).total_seconds()) < 6 * 3600:
            return i >= 1
    return False


def _same_moon(a: datetime, b: datetime) -> bool:
    """True if two datetimes are within 6 hours of each other (same full moon event)."""
    return abs((a - b).total_seconds()) < 6 * 3600


def _apply_naming(calendar_name: str, full_moon_dt: datetime, eph, ts) -> str:
    """Apply Blue Moon and equinox overrides on top of a calendar-based name.

    Shared by both naming conventions.
    """
    year = full_moon_dt.year

    # Step 1 — Blue Moon check
    is_blue = _is_second_full_moon_in_month(full_moon_dt, eph, ts)
    base = "Blue Moon" if is_blue else calendar_name

    # Step 2 — Equinox overrides
    try:
        harvest = _find_harvest_moon(year, eph, ts)
        hunter  = _find_hunter_moon(year, eph, ts)

        if _same_moon(full_moon_dt, harvest):
            return "Harvest/Blue Moon" if is_blue else "Harvest Moon"
        if _same_moon(full_moon_dt, hunter):
            return "Hunter's/Blue Moon" if is_blue else "Hunter's Moon"
    except Exception as e:
        _LOGGER.error(f"Error computing equinox overrides for {full_moon_dt}: {e}")

    return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_north_american_moon_name(full_moon_dt: datetime, eph, ts) -> str:
    """Return the North American moon name for the given full moon datetime."""
    try:
        calendar_name = _NORTH_AMERICAN_NAMES.get(full_moon_dt.month, "")
        return _apply_naming(calendar_name, full_moon_dt, eph, ts)
    except Exception as e:
        _LOGGER.error(f"Error getting North American moon name for {full_moon_dt}: {e}")
        return ""


def get_pagan_moon_name(full_moon_dt: datetime, eph, ts) -> str:
    """Return the Pagan moon name for the given full moon datetime."""
    try:
        calendar_name = _CELTIC_PAGAN_NAMES.get(full_moon_dt.month, "")
        return _apply_naming(calendar_name, full_moon_dt, eph, ts)
    except Exception as e:
        _LOGGER.error(f"Error getting Pagan moon name for {full_moon_dt}: {e}")
        return ""


def get_moon_name(full_moon_dt: datetime, convention: str, eph, ts) -> str:
    """Dispatch to the appropriate naming convention and return the moon name."""
    if convention == "north_american":
        return get_north_american_moon_name(full_moon_dt, eph, ts)
    if convention == "pagan":
        return get_pagan_moon_name(full_moon_dt, eph, ts)
    return ""


def find_next_full_moon(from_dt: datetime, eph, ts) -> Optional[datetime]:
    """Return the UTC datetime of the next full moon at or after from_dt."""
    full_moons = _find_all_full_moons_in_window(
        from_dt,
        from_dt + timedelta(days=35),
        eph, ts,
    )
    return full_moons[0] if full_moons else None


def find_full_moon_near(near_dt: datetime, eph, ts) -> Optional[datetime]:
    """Return the full moon closest to near_dt within a ±2-day window.

    Used when the sensor is in the Full Moon phase and needs the exact 180°
    time for naming (the phase band spans ±3° ≈ ±6 hours around 180°).
    """
    full_moons = _find_all_full_moons_in_window(
        near_dt - timedelta(days=2),
        near_dt + timedelta(days=2),
        eph, ts,
    )
    if not full_moons:
        return None
    return min(full_moons, key=lambda dt: abs((dt - near_dt).total_seconds()))
