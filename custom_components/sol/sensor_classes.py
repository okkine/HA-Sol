"""Sensors for Sol integration."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NAME, AZIMUTH_STEP_VALUE_DEFAULT, ELEVATION_STEP_VALUE_DEFAULT, AZIMUTH_SEARCH_WINDOW_HOURS, ELEVATION_SEARCH_WINDOW_HOURS, STEP_CANDIDATE_CACHE_REFILL_THRESHOLD, STEP_CANDIDATE_MIN_GAP_SECONDS, eph, ts, PARALLACTIC_EXTREMUM_TOLERANCE, DEBUG_ATTRIBUTES
from .moon_names import get_moon_name, find_next_full_moon, find_full_moon_near, MOON_PHASE_BOUNDARIES, MOON_PHASE_ICONS
from .body_observer import BodyObserver
from .config_store import get_config_entry_data
from .utils import get_formatted_sensor_name, get_declination_normalized
from .declination_cache import get_cached_declination_normalized

_LOGGER = logging.getLogger(__name__)


def _prune_stale_step_candidates(
    items: list[dict[str, Any]],
    stale_cutoff: datetime,
) -> list[dict[str, Any]]:
    """Drop non-cap rows at or before stale_cutoff; caps are kept."""
    out: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda i: i["time"]):
        if item["time"] <= stale_cutoff and not item.get("is_cap", False):
            continue
        out.append(item)
    return out


def _merge_step_candidates_min_gap(
    items: list[dict[str, Any]],
    min_gap_seconds: float,
) -> list[dict[str, Any]]:
    """Enforce minimum time gap; cap rows (is_cap) are never dropped—non-caps lose conflicts."""
    items = sorted(items, key=lambda i: i["time"])
    out: list[dict[str, Any]] = []
    for item in items:
        is_cap = item.get("is_cap", False)
        if not out:
            out.append(item)
            continue
        prev = out[-1]
        gap_seconds = (item["time"] - prev["time"]).total_seconds()
        if gap_seconds >= min_gap_seconds:
            out.append(item)
            continue
        prev_cap = prev.get("is_cap", False)
        if is_cap and not prev_cap:
            out[-1] = item
            continue
        if prev_cap and not is_cap:
            continue
        if prev_cap and is_cap:
            out.append(item)
            continue
        # both non-cap: keep earlier
        continue
    return out


def _trim_refill_leading_too_close(
    refill: list[dict[str, Any]],
    last_time: datetime,
    min_gap_seconds: float,
) -> None:
    """Drop leading finder rows until the first is at least min_gap_seconds after last_time (in-place)."""
    while refill:
        gap = (refill[0]["time"] - last_time).total_seconds()
        if gap >= min_gap_seconds:
            break
        refill.pop(0)


def _next_azimuth_reversal_checkpoint_after(
    cache: Optional[dict[str, Any]],
    after_time: datetime,
) -> Optional[dict[str, Any]]:
    """First ephemeris checkpoint with event_type reversal strictly after after_time."""
    if not cache:
        return None
    for cp in cache.get("checkpoints", []):
        if cp.get("event_type") != "reversal":
            continue
        cp_time = cp.get("time")
        if cp_time and cp_time > after_time:
            return cp
    return None


class AzimuthSensor(SensorEntity):
    """Azimuth sensor that updates every minute."""
    
    def __init__(self, hass: HomeAssistant, entry_id: str, body, body_key: str):
        """Initialize azimuth sensor.
        
        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID
            body: Skyfield body object
            body_key: Body identifier (e.g., "sun", "moon", "jupiter")
        """
        self.hass = hass
        self._entry_id = entry_id
        self._body = body
        self._body_key = body_key
        
        # Get location_name from config
        config_data = get_config_entry_data(entry_id)
        location_name = config_data.get('location_name') if config_data else None
        
        # Set sensor name using standardized naming method
        self._attr_name = get_formatted_sensor_name("Azimuth", body_key, location_name=location_name)
        # For backward compatibility, use old format for "sun" (no body_key in unique_id)
        if body_key == "sun":
            self._attr_unique_id = f"{DOMAIN}_{entry_id}_azimuth"
        else:
            self._attr_unique_id = f"{DOMAIN}_{entry_id}_{body_key}_azimuth"
        self._attr_has_entity_name = True
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = "°"
        self._attr_available = False
        self._unsub_update = None
        self._observer = None
        self._step_candidates_cache = []
        self._last_search_iterations = 0
        self._last_search_total_time_ms = 0.0
        self._last_search_results_raw = 0
        self._last_search_results_kept = 0
        self._last_search_avg_time_ms = 0.0
        
        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": NAME,
        }
    
    @property
    def should_poll(self) -> bool:
        """Return False as entity should not be polled by Home Assistant."""
 #       if self._body_key in ["jupiter", "uranus"]:
 #           return True #TEMP: For testing, set to True to poll Jupiter azimuth sensor
        return False
    
    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await self.async_update()
    
    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None
        await super().async_will_remove_from_hass()
    
    async def async_update(self, now=None) -> None:
        """Update the sensor state and schedule next update."""
        try:
            # Use current time if not provided
            if now is None:
                now = dt_util.now()
            
            # Create or update observer
            if self._observer is None:
                self._observer = BodyObserver(
                    entry_id=self._entry_id,
                    body=self._body,
                    search_start=now,
                    hass=self.hass
                )
            else:
                # Update search time
                self._observer.set_search_window(search_start=now)
            
            # Get current azimuth (skip refraction since azimuth doesn't need it)
            _, azimuth, _ = self._observer.position(now, apply_refraction=False)
            self._attr_native_value = round(azimuth, 2) if azimuth is not None else None
            
            # Get cache and direction
            cache_manager = self.hass.data.get(DOMAIN, {}).get('cache_manager')
            cache = None
            direction = 1  # Default direction
            reversal_checkpoints = None  # Cache reversal checkpoints to avoid repeated scans
            if cache_manager:
                try:
                    cache = await cache_manager.get_reversals(self._entry_id, self._body_key, now)
                    if cache:
                        direction = cache['last_known_state']['azimuth_direction']
                        
                        # Cache reversal checkpoints once here
                        reversal_checkpoints = [cp for cp in cache.get('checkpoints', []) if cp.get('event_type') == 'reversal']
                except Exception as e:
                    _LOGGER.debug(f"Error getting cache for direction: {e}")
            
            # Step 1: Read step configuration
            config_data = get_config_entry_data(self._entry_id)
            step_value = config_data.get('azimuth_step_value', AZIMUTH_STEP_VALUE_DEFAULT) if config_data else AZIMUTH_STEP_VALUE_DEFAULT
            
            # Step 2: Next update = None
            next_update_time = None
            next_target_value = None
            candidates = []
            cache_source = "none"
            stale_cutoff = now + timedelta(seconds=STEP_CANDIDATE_MIN_GAP_SECONDS)
            
            # Cache-refresh metrics (persisted after each finder-based refresh)
            refresh_search_iterations = 0
            refresh_search_total_time_ms = 0.0
            refresh_search_results_raw = 0
            refresh_search_results_kept = 0
            cache_refreshed = False
            
            # Step 3: Drop stale non-cap rows only (min-gap merge runs when finder/refill adds data)
            self._step_candidates_cache = _prune_stale_step_candidates(
                self._step_candidates_cache, stale_cutoff
            )

            if self._step_candidates_cache:
                first_candidate = self._step_candidates_cache.pop(0)
                next_update_time = first_candidate["time"]
                next_target_value = round(first_candidate["value"], 2)
                cache_source = "cache"

                if len(self._step_candidates_cache) <= STEP_CANDIDATE_CACHE_REFILL_THRESHOLD:
                    refill_anchor = next_update_time if next_update_time is not None else now
                    if self._step_candidates_cache:
                        last_time = self._step_candidates_cache[-1]["time"]
                        refill_start = last_time + timedelta(seconds=1)
                    else:
                        refill_start = refill_anchor + timedelta(seconds=1)
                    cap_checkpoint = next(
                        (
                            cp
                            for cp in (reversal_checkpoints or [])
                            if cp.get("time") is not None and cp["time"] > refill_start
                        ),
                        None,
                    )
                    cap_time = cap_checkpoint["time"] if cap_checkpoint else None
                    refill_iteration_start = time.perf_counter()
                    refill_candidates = self._observer.find_step_aligned_targets(
                        quantity="azimuth",
                        step_value=step_value,
                        direction=direction,
                        search_start=refill_start,
                        search_window_hours=AZIMUTH_SEARCH_WINDOW_HOURS,
                        cap_time=cap_time,
                        ephemeris_checkpoints=cache.get("checkpoints") if cache else None,
                    )
                    refill_elapsed_ms = (time.perf_counter() - refill_iteration_start) * 1000.0
                    refresh_search_total_time_ms += refill_elapsed_ms
                    windows_used = getattr(self._observer, "_last_step_search_windows_used", 1)
                    refresh_search_iterations += max(windows_used - 1, 0)
                    cache_refreshed = True
                    refill_candidates = [
                        {
                            "time": item["time"],
                            "value": round(item["value"], 2),
                            "is_cap": item.get("is_cap", False),
                        }
                        for item in refill_candidates
                    ]
                    refresh_search_results_raw += len(refill_candidates)
                    refill_candidates = _prune_stale_step_candidates(refill_candidates, stale_cutoff)
                    if self._step_candidates_cache:
                        _trim_refill_leading_too_close(
                            refill_candidates,
                            self._step_candidates_cache[-1]["time"],
                            STEP_CANDIDATE_MIN_GAP_SECONDS,
                        )
                    refresh_search_results_kept += len(refill_candidates)
                    existing_times = {item["time"] for item in self._step_candidates_cache}
                    for item in refill_candidates:
                        if item["time"] not in existing_times:
                            self._step_candidates_cache.append(item)
                            existing_times.add(item["time"])
                    self._step_candidates_cache = _merge_step_candidates_min_gap(
                        self._step_candidates_cache, STEP_CANDIDATE_MIN_GAP_SECONDS
                    )
            else:
                cap_checkpoint = next(
                    (
                        cp
                        for cp in (reversal_checkpoints or [])
                        if cp.get("time") is not None and cp["time"] > now
                    ),
                    None,
                )
                cap_time = cap_checkpoint["time"] if cap_checkpoint else None
                iteration_start = time.perf_counter()
                candidates = self._observer.find_step_aligned_targets(
                    quantity="azimuth",
                    step_value=step_value,
                    direction=direction,
                    search_start=now,
                    search_window_hours=AZIMUTH_SEARCH_WINDOW_HOURS,
                    cap_time=cap_time,
                    ephemeris_checkpoints=cache.get("checkpoints") if cache else None,
                )
                search_elapsed_ms = (time.perf_counter() - iteration_start) * 1000.0
                refresh_search_total_time_ms += search_elapsed_ms
                windows_used = getattr(self._observer, "_last_step_search_windows_used", 1)
                refresh_search_iterations += max(windows_used - 1, 0)
                cache_refreshed = True
                candidates = [
                    {
                        "time": item["time"],
                        "value": round(item["value"], 2),
                        "is_cap": item.get("is_cap", False),
                    }
                    for item in candidates
                ]
                refresh_search_results_raw += len(candidates)
                candidates = _prune_stale_step_candidates(candidates, stale_cutoff)
                refresh_search_results_kept += len(candidates)

                if candidates:
                    first_candidate = candidates[0]
                    next_update_time = first_candidate["time"]
                    next_target_value = round(first_candidate["value"], 2)
                    self._step_candidates_cache = candidates[1:]
                    self._step_candidates_cache = _merge_step_candidates_min_gap(
                        self._step_candidates_cache, STEP_CANDIDATE_MIN_GAP_SECONDS
                    )
                    cache_source = "finder"
                else:
                    if cap_time is not None and cap_time > stale_cutoff:
                        next_update_time = cap_time
                        if cap_checkpoint:
                            next_target_value = round(cap_checkpoint.get("azimuth", azimuth), 2)
                        else:
                            next_target_value = round(azimuth, 2) if azimuth is not None else None
                        cache_source = "cap_event"
                    else:
                        next_update_time = "Out of Range"
                        cache_source = "finder_empty"
                    self._step_candidates_cache = []

            if cache_refreshed:
                self._last_search_iterations = refresh_search_iterations
                self._last_search_total_time_ms = round(refresh_search_total_time_ms, 2)
                self._last_search_results_raw = refresh_search_results_raw
                self._last_search_results_kept = refresh_search_results_kept
                self._last_search_avg_time_ms = round(
                    refresh_search_total_time_ms / refresh_search_results_kept, 2
                ) if refresh_search_results_kept > 0 else 0.0
            
            # Handle "Out of Range" - schedule update in 1 minute
            if next_update_time == "Out of Range":
                next_update_time = now + timedelta(minutes=1)
            elif next_update_time is None:
                next_update_time = now + timedelta(minutes=1)
            
            # Ensure next_update_time is a datetime
            if isinstance(next_update_time, str):
                next_update_time = now + timedelta(minutes=1)
            
            if next_target_value is None:
                next_target_value = round(azimuth, 2) if azimuth is not None else None
            
            # Keep only required attributes in non-debug mode
            attributes = {
                "next_update": next_update_time.isoformat() if next_update_time else None,
                "next_target": next_target_value,
            }

            if DEBUG_ATTRIBUTES:
                config_data = get_config_entry_data(self._entry_id)
                attributes["direction"] = direction
                attributes["search_iterations"] = self._last_search_iterations
                attributes["search_total_time_ms"] = self._last_search_total_time_ms
                attributes["search_results_raw_count"] = self._last_search_results_raw
                attributes["search_results_kept_count"] = self._last_search_results_kept
                attributes["search_avg_time_ms"] = self._last_search_avg_time_ms
                attributes["step_cache"] = {
                    "source": cache_source,
                    "count": len(self._step_candidates_cache),
                    "first": {
                        "time": self._step_candidates_cache[0]["time"].isoformat(),
                        "value": round(self._step_candidates_cache[0]["value"], 2),
                        "is_cap": self._step_candidates_cache[0].get("is_cap", False),
                    } if self._step_candidates_cache else None,
                    "items": [
                        {
                            "time": c["time"].isoformat(),
                            "value": round(c["value"], 2),
                            "is_cap": c.get("is_cap", False),
                        }
                        for c in self._step_candidates_cache
                    ],
                }
                attributes["latitude"] = config_data.get('latitude') if config_data else None
                attributes["longitude"] = round(config_data.get('longitude'), 6) if config_data else None

                # Debug-only heavy diagnostics
                temperature, pressure = self._observer._get_refraction_params(now)
                attributes["temperature_C"] = temperature
                attributes["pressure_mbar"] = pressure
                attributes["elongation"] = self._observer.current_elongation
                attributes["percent_illuminated"] = self._observer.percent_illuminated
                attributes["declination"] = self._observer.current_declination

                if cache:
                    try:
                        # Show cache last_known_state with all fields
                        last_known_state = cache['last_known_state'].copy()
                        if 'time' in last_known_state and last_known_state['time']:
                            last_known_state['time'] = last_known_state['time'].isoformat()
                        if 'azimuth' in last_known_state:
                            last_known_state['azimuth'] = round(last_known_state['azimuth'], 2)
                        if 'elevation' in last_known_state:
                            last_known_state['elevation'] = round(last_known_state['elevation'], 2)
                        attributes["cache_last_known_state"] = last_known_state

                        # Show cache checkpoints with all fields
                        checkpoints_formatted = []
                        for cp in cache.get('checkpoints', []):
                            cp_copy = cp.copy()
                            if 'time' in cp_copy and cp_copy['time']:
                                cp_copy['time'] = cp_copy['time'].isoformat()
                            if 'azimuth' in cp_copy:
                                cp_copy['azimuth'] = round(cp_copy['azimuth'], 2)
                            if 'elevation' in cp_copy:
                                cp_copy['elevation'] = round(cp_copy['elevation'], 2)
                            if 'declination' in cp_copy and cp_copy['declination'] is not None:
                                cp_copy['declination'] = round(cp_copy['declination'], 2)
                            checkpoints_formatted.append(cp_copy)
                        attributes["cache_checkpoints"] = checkpoints_formatted
                    except Exception as e:
                        _LOGGER.debug(f"Error adding cache attributes: {e}")
            
            self._attr_extra_state_attributes = attributes

            # Schedule next update
            self._unsub_update = async_track_point_in_time(
                self.hass, self.async_update, next_update_time
            )
            
            self._attr_available = True
            self.async_write_ha_state()
            
        except Exception as e:
            _LOGGER.error(f"Error updating AzimuthSensor: {e}", exc_info=True)
            self._attr_available = False
            # Retry in 5 minutes on error
            if now is None:
                now = dt_util.now()
            next_update = now + timedelta(minutes=1)
            self._unsub_update = async_track_point_in_time(
                self.hass, self.async_update, next_update
            )
            self.async_write_ha_state()


class ElevationSensor(SensorEntity):
    """Elevation sensor that updates based on elevation steps."""
    
    def __init__(self, hass: HomeAssistant, entry_id: str, body, body_key: str):
        """Initialize elevation sensor.
        
        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID
            body: Skyfield body object
            body_key: Body identifier (e.g., "sun", "moon", "jupiter")
        """
        self.hass = hass
        self._entry_id = entry_id
        self._body = body
        self._body_key = body_key
        
        # Get location_name from config
        config_data = get_config_entry_data(entry_id)
        location_name = config_data.get('location_name') if config_data else None
        
        # Set sensor name using standardized naming method
        self._attr_name = get_formatted_sensor_name("Elevation", body_key, location_name=location_name)
        # For backward compatibility, use old format for "sun" (no body_key in unique_id)
        if body_key == "sun":
            self._attr_unique_id = f"{DOMAIN}_{entry_id}_elevation"
        else:
            self._attr_unique_id = f"{DOMAIN}_{entry_id}_{body_key}_elevation"
        self._attr_has_entity_name = True
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = "°"
        self._attr_available = False
        self._unsub_update = None
        self._observer = None
        self._step_candidates_cache = []
        self._last_search_iterations = 0
        self._last_search_total_time_ms = 0.0
        self._last_search_results_raw = 0
        self._last_search_results_kept = 0
        self._last_search_avg_time_ms = 0.0
        
        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": NAME,
        }
    
    @property
    def should_poll(self) -> bool:
        """Return False as entity should not be polled by Home Assistant."""
        return False
    
    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await self.async_update()
    
    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None
        await super().async_will_remove_from_hass()
    
    async def async_update(self, now=None) -> None:
        """Update the sensor state and schedule next update."""
        try:
            # Use current time if not provided
            if now is None:
                now = dt_util.now()
            
            # Create or update observer
            if self._observer is None:
                self._observer = BodyObserver(
                    entry_id=self._entry_id,
                    body=self._body,
                    search_start=now,
                    hass=self.hass
                )
            else:
                # Update search time
                self._observer.set_search_window(search_start=now)
            
            # Get current elevation
            elevation, _, _ = self._observer.position(now)
            elevation = round(elevation, 2)
            self._attr_native_value = elevation
            
            # Get cache to find next transit/antitransit
            cache_manager = self.hass.data.get(DOMAIN, {}).get('cache_manager')
            cache = None
            next_event = None
            next_event_type = None
            next_event_elevation = None
            direction = 1  # Default to increasing
            
            if cache_manager:
                try:
                    cache = await cache_manager.get_reversals(self._entry_id, self._body_key, now)
                    if cache:
                        direction = cache['last_known_state']['elevation_direction']
                        
                        # Find first checkpoint that isn't a reversal for next event
                        for cp in cache.get('checkpoints', []):
                            if cp['time'] > now and cp.get('event_type') != 'reversal':
                                next_event = cp['time']
                                next_event_type = cp.get('event_type')
                                next_event_elevation = cp.get('elevation')
                                break
                except Exception as e:
                    _LOGGER.debug(f"Error getting cache for elevation sensor: {e}")
            
            # Fallback to BodyObserver if cache not available
            if next_event is None:
                if next_event_type is None:
                    # Determine next event type from BodyObserver
                    observer = BodyObserver(entry_id=self._entry_id, body=self._body, search_start=now, hass=self.hass)
                    next_transit = observer.next_transit
                    next_antitransit = observer.next_antitransit
                    
                    if next_transit and next_antitransit:
                        if next_transit < next_antitransit:
                            next_event = next_transit
                            next_event_type = 'transit'
                            direction = 1  # Rising toward transit
                            _LOGGER.debug(f"Elevation sensor {self._entry_id}: Using fallback, direction=1 (rising toward transit)")
                        else:
                            next_event = next_antitransit
                            next_event_type = 'antitransit'
                            direction = -1  # Setting toward antitransit
                            _LOGGER.debug(f"Elevation sensor {self._entry_id}: Using fallback, direction=-1 (setting toward antitransit)")
                    elif next_transit:
                        next_event = next_transit
                        next_event_type = 'transit'
                        direction = 1
                        _LOGGER.debug(f"Elevation sensor {self._entry_id}: Using fallback, direction=1 (rising toward transit)")
                    elif next_antitransit:
                        next_event = next_antitransit
                        next_event_type = 'antitransit'
                        direction = -1
                        _LOGGER.debug(f"Elevation sensor {self._entry_id}: Using fallback, direction=-1 (setting toward antitransit)")
            
            # Read step configuration
            config_data = get_config_entry_data(self._entry_id)
            step_value = config_data.get('elevation_step_value', ELEVATION_STEP_VALUE_DEFAULT) if config_data else ELEVATION_STEP_VALUE_DEFAULT
            
            # Next update = None
            next_update_time = None
            next_target_value = None
            candidates = []
            cache_source = "none"
            stale_cutoff = now + timedelta(seconds=STEP_CANDIDATE_MIN_GAP_SECONDS)
            
            # Cache-refresh metrics (persisted after each finder-based refresh)
            refresh_search_iterations = 0
            refresh_search_total_time_ms = 0.0
            refresh_search_results_raw = 0
            refresh_search_results_kept = 0
            cache_refreshed = False
            
            # Drop stale non-cap rows only (min-gap merge runs when finder/refill adds data)
            self._step_candidates_cache = _prune_stale_step_candidates(
                self._step_candidates_cache, stale_cutoff
            )

            if self._step_candidates_cache:
                first_candidate = self._step_candidates_cache.pop(0)
                next_update_time = first_candidate["time"]
                next_target_value = round(first_candidate["value"], 2)
                cache_source = "cache"

                if len(self._step_candidates_cache) <= STEP_CANDIDATE_CACHE_REFILL_THRESHOLD:
                    refill_anchor = next_update_time if next_update_time is not None else now
                    if self._step_candidates_cache:
                        last_time = self._step_candidates_cache[-1]["time"]
                        refill_start = last_time + timedelta(seconds=1)
                    else:
                        refill_start = refill_anchor + timedelta(seconds=1)
                    refill_iteration_start = time.perf_counter()
                    refill_candidates = self._observer.find_step_aligned_targets(
                        quantity="elevation",
                        step_value=step_value,
                        direction=direction,
                        search_start=refill_start,
                        search_window_hours=ELEVATION_SEARCH_WINDOW_HOURS,
                        cap_time=next_event,
                        cap_value=next_event_elevation,
                    )
                    refill_elapsed_ms = (time.perf_counter() - refill_iteration_start) * 1000.0
                    refresh_search_total_time_ms += refill_elapsed_ms
                    windows_used = getattr(self._observer, "_last_step_search_windows_used", 1)
                    refresh_search_iterations += max(windows_used - 1, 0)
                    cache_refreshed = True
                    refill_candidates = [
                        {
                            "time": item["time"],
                            "value": round(item["value"], 2),
                            "is_cap": item.get("is_cap", False),
                        }
                        for item in refill_candidates
                    ]
                    refresh_search_results_raw += len(refill_candidates)
                    refill_candidates = _prune_stale_step_candidates(refill_candidates, stale_cutoff)
                    if self._step_candidates_cache:
                        _trim_refill_leading_too_close(
                            refill_candidates,
                            self._step_candidates_cache[-1]["time"],
                            STEP_CANDIDATE_MIN_GAP_SECONDS,
                        )
                    refresh_search_results_kept += len(refill_candidates)
                    existing_times = {item["time"] for item in self._step_candidates_cache}
                    for item in refill_candidates:
                        if item["time"] not in existing_times:
                            self._step_candidates_cache.append(item)
                            existing_times.add(item["time"])
                    self._step_candidates_cache = _merge_step_candidates_min_gap(
                        self._step_candidates_cache, STEP_CANDIDATE_MIN_GAP_SECONDS
                    )
            else:
                iteration_start = time.perf_counter()
                candidates = self._observer.find_step_aligned_targets(
                    quantity="elevation",
                    step_value=step_value,
                    direction=direction,
                    search_start=now,
                    search_window_hours=ELEVATION_SEARCH_WINDOW_HOURS,
                    cap_time=next_event,
                    cap_value=next_event_elevation,
                )
                search_elapsed_ms = (time.perf_counter() - iteration_start) * 1000.0
                refresh_search_total_time_ms += search_elapsed_ms
                windows_used = getattr(self._observer, "_last_step_search_windows_used", 1)
                refresh_search_iterations += max(windows_used - 1, 0)
                cache_refreshed = True
                candidates = [
                    {
                        "time": item["time"],
                        "value": round(item["value"], 2),
                        "is_cap": item.get("is_cap", False),
                    }
                    for item in candidates
                ]
                refresh_search_results_raw += len(candidates)
                candidates = _prune_stale_step_candidates(candidates, stale_cutoff)
                refresh_search_results_kept += len(candidates)

                if candidates:
                    first_candidate = candidates[0]
                    next_update_time = first_candidate["time"]
                    next_target_value = round(first_candidate["value"], 2)
                    self._step_candidates_cache = candidates[1:]
                    self._step_candidates_cache = _merge_step_candidates_min_gap(
                        self._step_candidates_cache, STEP_CANDIDATE_MIN_GAP_SECONDS
                    )
                    cache_source = "finder"
                else:
                    if next_event is not None and next_event > stale_cutoff:
                        next_update_time = next_event
                        next_target_value = round(next_event_elevation, 2) if next_event_elevation is not None else None
                        cache_source = "cap_event"
                    else:
                        next_update_time = "Out of Range"
                        cache_source = "finder_empty"
                    self._step_candidates_cache = []

            if cache_refreshed:
                self._last_search_iterations = refresh_search_iterations
                self._last_search_total_time_ms = round(refresh_search_total_time_ms, 2)
                self._last_search_results_raw = refresh_search_results_raw
                self._last_search_results_kept = refresh_search_results_kept
                self._last_search_avg_time_ms = round(
                    refresh_search_total_time_ms / refresh_search_results_kept, 2
                ) if refresh_search_results_kept > 0 else 0.0
            
            # Handle "Out of Range" - schedule update in 1 minute
            if next_update_time == "Out of Range":
                next_update_time = now + timedelta(minutes=1)
            elif next_update_time is None:
                next_update_time = now + timedelta(minutes=1)
            
            # Ensure next_update_time is a datetime
            if isinstance(next_update_time, str):
                next_update_time = now + timedelta(minutes=1)
            
            if next_update_time is None:
                _LOGGER.warning(f"Could not determine next elevation update time, using 1 minute fallback")
            
            if next_target_value is None:
                next_target_value = round(elevation, 2) if elevation is not None else None
            
            # Keep only required attributes in non-debug mode
            attributes = {
                "next_update": next_update_time.isoformat() if next_update_time else None,
                "next_target": next_target_value,
            }

            if DEBUG_ATTRIBUTES:
                attributes["direction"] = direction
                attributes["next_event"] = next_event.isoformat() if next_event else None
                attributes["next_event_type"] = next_event_type
                attributes["next_event_elevation"] = (
                    round(next_event_elevation, 2) if next_event_elevation is not None else None
                )
                attributes["cache_used"] = cache is not None
                attributes["cache_checkpoints_count"] = len(cache.get('checkpoints', [])) if cache else 0
                attributes["using_fallback"] = next_event is not None and next_event_elevation is None and cache is None
                attributes["azimuth"] = self._observer.position(now)[1]
                attributes["search_iterations"] = self._last_search_iterations
                attributes["search_total_time_ms"] = self._last_search_total_time_ms
                attributes["search_results_raw_count"] = self._last_search_results_raw
                attributes["search_results_kept_count"] = self._last_search_results_kept
                attributes["search_avg_time_ms"] = self._last_search_avg_time_ms
                attributes["step_cache"] = {
                    "source": cache_source,
                    "count": len(self._step_candidates_cache),
                    "first": {
                        "time": self._step_candidates_cache[0]["time"].isoformat(),
                        "value": round(self._step_candidates_cache[0]["value"], 2),
                        "is_cap": self._step_candidates_cache[0].get("is_cap", False),
                    } if self._step_candidates_cache else None,
                    "items": [
                        {
                            "time": c["time"].isoformat(),
                            "value": round(c["value"], 2),
                            "is_cap": c.get("is_cap", False),
                        }
                        for c in self._step_candidates_cache
                    ],
                }

                # Debug-only heavy diagnostics
                temperature, pressure = self._observer._get_refraction_params(now)
                attributes["temperature_C"] = temperature
                attributes["pressure_mbar"] = pressure
                attributes["declination"] = self._observer.current_declination
                attributes["elongation"] = self._observer.current_elongation

                if cache:
                    try:
                        # Show cache last_known_state with all fields
                        last_known_state = cache['last_known_state'].copy()
                        if 'time' in last_known_state and last_known_state['time']:
                            last_known_state['time'] = last_known_state['time'].isoformat()
                        if 'azimuth' in last_known_state:
                            last_known_state['azimuth'] = round(last_known_state['azimuth'], 2)
                        if 'elevation' in last_known_state:
                            last_known_state['elevation'] = round(last_known_state['elevation'], 2)
                        attributes["cache_last_known_state"] = last_known_state

                        # Show cache checkpoints with all fields
                        checkpoints_formatted = []
                        for cp in cache.get('checkpoints', []):
                            cp_copy = cp.copy()
                            if 'time' in cp_copy and cp_copy['time']:
                                cp_copy['time'] = cp_copy['time'].isoformat()
                            if 'azimuth' in cp_copy:
                                cp_copy['azimuth'] = round(cp_copy['azimuth'], 2)
                            if 'elevation' in cp_copy:
                                cp_copy['elevation'] = round(cp_copy['elevation'], 2)
                            if 'declination' in cp_copy and cp_copy['declination'] is not None:
                                cp_copy['declination'] = round(cp_copy['declination'], 2)
                            checkpoints_formatted.append(cp_copy)
                        attributes["cache_checkpoints"] = checkpoints_formatted
                    except Exception as e:
                        _LOGGER.debug(f"Error adding cache attributes: {e}")
            
            self._attr_extra_state_attributes = attributes
            
            # Schedule next update
            self._unsub_update = async_track_point_in_time(
                self.hass, self.async_update, next_update_time
            )
            
            self._attr_available = True
            self.async_write_ha_state()
            
        except Exception as e:
            _LOGGER.error(f"Error updating ElevationSensor: {e}", exc_info=True)
            self._attr_available = False
            # Retry in 5 minutes on error
            if now is None:
                now = dt_util.now()
            next_update = now + timedelta(minutes=5)
            self._unsub_update = async_track_point_in_time(
                self.hass, self.async_update, next_update
            )
            self.async_write_ha_state()


class RiseSensor(SensorEntity):
    """Rise sensor that displays today's rise time and updates at midnight."""
    
    def __init__(self, hass: HomeAssistant, entry_id: str, body, body_key: str):
        """Initialize rise sensor.
        
        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID
            body: Skyfield body object
            body_key: Body identifier (e.g., "sun", "moon", "jupiter")
        """
        self.hass = hass
        self._entry_id = entry_id
        self._body = body
        self._body_key = body_key
        
        # Get location_name from config
        config_data = get_config_entry_data(entry_id)
        location_name = config_data.get('location_name') if config_data else None
        
        # Set sensor name using standardized naming method
        self._attr_name = get_formatted_sensor_name("Rise", body_key, location_name=location_name, event_type="rise")
        # For backward compatibility, use old format for "sun" (no body_key in unique_id)
        if body_key == "sun":
            self._attr_unique_id = f"{DOMAIN}_{entry_id}_rise"
        else:
            self._attr_unique_id = f"{DOMAIN}_{entry_id}_{body_key}_rise"
        self._attr_has_entity_name = True
        self._attr_native_value = None
        self._attr_device_class = "timestamp"
        self._attr_available = False
        self._unsub_update = None
        self._unsub_midnight = None
        self._observer = None
        
        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": NAME,
        }
    
    @property
    def should_poll(self) -> bool:
        """Return False as entity should not be polled by Home Assistant."""
        return False
    
    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await self.async_update()
        self._schedule_midnight_update(dt_util.now())
    
    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None
        await super().async_will_remove_from_hass()
    
    def _schedule_midnight_update(self, now: datetime) -> None:
        """Schedule update at next midnight.
        
        Args:
            now: Current datetime
        """
        # Calculate next midnight
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Schedule update at midnight
        self._unsub_midnight = async_track_point_in_time(
            self.hass, self._on_midnight, next_midnight
        )
        _LOGGER.debug(f"Scheduled midnight update for {self._attr_name} at {next_midnight}")
    
    async def _on_midnight(self, now: datetime) -> None:
        """Handler for midnight updates.
        
        Args:
            now: Current datetime (should be midnight)
        """
        _LOGGER.debug(f"Midnight update triggered for {self._attr_name}")
        # Update sensor
        await self.async_update(now)
        # Schedule next midnight
        self._schedule_midnight_update(now)
    
    async def async_update(self, now=None) -> None:
        """Update the sensor state."""
        try:
            # Use current time if not provided
            if now is None:
                now = dt_util.now()
            
            # Create or reuse observer (create once, reuse for today's and tomorrow's calculations)
            if self._observer is None:
                # Create observer with wide search window (today to tomorrow)
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                tomorrow_end = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
                self._observer = BodyObserver(
                    entry_id=self._entry_id,
                    body=self._body,
                    search_start=today_start,
                    search_end=tomorrow_end,
                    hass=self.hass
                )
            else:
                # Update search window if needed
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                tomorrow_end = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
                self._observer.set_search_window(search_start=today_start, search_end=tomorrow_end)
            
            # Calculate date ranges for today and tomorrow
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)
            tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow_end = tomorrow_start + timedelta(days=1)
            
            # Get rise times for today and tomorrow using the same observer
            # use_centre=False only for Sun/Moon
            use_centre_param = self._body_key not in ["sun", "moon"]
            today_result = self._observer.get_time_at_elevation(
                target_elevation=0.0,
                direction=1,
                search_start=today_start,
                search_end=today_end,
                use_centre=use_centre_param
            )
            tomorrow_result = self._observer.get_time_at_elevation(
                target_elevation=0.0,
                direction=1,
                search_start=tomorrow_start,
                search_end=tomorrow_end,
                use_centre=use_centre_param
            )
            
            # Convert results
            today_time = today_result if isinstance(today_result, datetime) else None
            tomorrow_time = tomorrow_result if isinstance(tomorrow_result, datetime) else None

            # Get azimuth at today's rise time
            rise_azimuth = None
            if today_time is not None:
                _, az, _ = self._observer.position(today_time, apply_refraction=False)
                if az is not None:
                    rise_azimuth = round(float(az), 1)
            
            # Get azimuth at tomorrow's rise time
            tomorrow_rise_azimuth = None
            if tomorrow_time is not None:
                _, az, _ = self._observer.position(tomorrow_time, apply_refraction=False)
                if az is not None:
                    tomorrow_rise_azimuth = round(float(az), 1)

            # Set native value (today's time)
            self._attr_native_value = today_time
            
            # Set attributes
            self._attr_extra_state_attributes = {
                "today": today_time.isoformat() if today_time else None,
                "tomorrow": tomorrow_time.isoformat() if tomorrow_time else None,
                "rise_azimuth": rise_azimuth,
                "tomorrow_rise_azimuth": tomorrow_rise_azimuth,
            }
            
            self._attr_available = True
            self.async_write_ha_state()
            
        except Exception as e:
            _LOGGER.error(f"Error updating RiseSensor: {e}", exc_info=True)
            self._attr_available = False
            self.async_write_ha_state()


class SetSensor(SensorEntity):
    """Set sensor that displays today's set time and updates at midnight."""
    
    def __init__(self, hass: HomeAssistant, entry_id: str, body, body_key: str):
        """Initialize set sensor.
        
        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID
            body: Skyfield body object
            body_key: Body identifier (e.g., "sun", "moon", "jupiter")
        """
        self.hass = hass
        self._entry_id = entry_id
        self._body = body
        self._body_key = body_key
        
        # Get location_name from config
        config_data = get_config_entry_data(entry_id)
        location_name = config_data.get('location_name') if config_data else None
        
        # Set sensor name using standardized naming method
        self._attr_name = get_formatted_sensor_name("Set", body_key, location_name=location_name, event_type="set")
        # For backward compatibility, use old format for "sun" (no body_key in unique_id)
        if body_key == "sun":
            self._attr_unique_id = f"{DOMAIN}_{entry_id}_set"
        else:
            self._attr_unique_id = f"{DOMAIN}_{entry_id}_{body_key}_set"
        self._attr_has_entity_name = True
        self._attr_native_value = None
        self._attr_device_class = "timestamp"
        self._attr_available = False
        self._unsub_update = None
        self._unsub_midnight = None
        self._observer = None
        
        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": NAME,
        }
    
    @property
    def should_poll(self) -> bool:
        """Return False as entity should not be polled by Home Assistant."""
        return False
    
    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await self.async_update()
        self._schedule_midnight_update(dt_util.now())
    
    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None
        await super().async_will_remove_from_hass()
    
    def _schedule_midnight_update(self, now: datetime) -> None:
        """Schedule update at next midnight.
        
        Args:
            now: Current datetime
        """
        # Calculate next midnight
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Schedule update at midnight
        self._unsub_midnight = async_track_point_in_time(
            self.hass, self._on_midnight, next_midnight
        )
        _LOGGER.debug(f"Scheduled midnight update for {self._attr_name} at {next_midnight}")
    
    async def _on_midnight(self, now: datetime) -> None:
        """Handler for midnight updates.
        
        Args:
            now: Current datetime (should be midnight)
        """
        _LOGGER.debug(f"Midnight update triggered for {self._attr_name}")
        # Update sensor
        await self.async_update(now)
        # Schedule next midnight
        self._schedule_midnight_update(now)
    
    async def async_update(self, now=None) -> None:
        """Update the sensor state."""
        try:
            # Use current time if not provided
            if now is None:
                now = dt_util.now()
            
            # Create or reuse observer (create once, reuse for today's and tomorrow's calculations)
            if self._observer is None:
                # Create observer with wide search window (today to tomorrow)
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                tomorrow_end = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
                self._observer = BodyObserver(
                    entry_id=self._entry_id,
                    body=self._body,
                    search_start=today_start,
                    search_end=tomorrow_end,
                    hass=self.hass
                )
            else:
                # Update search window if needed
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                tomorrow_end = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
                self._observer.set_search_window(search_start=today_start, search_end=tomorrow_end)
            
            # Calculate date ranges for today and tomorrow
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)
            tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow_end = tomorrow_start + timedelta(days=1)
            
            # Get set times for today and tomorrow using the same observer
            # use_centre=False only for Sun/Moon
            use_centre_param = self._body_key not in ["sun", "moon"]
            today_result = self._observer.get_time_at_elevation(
                target_elevation=0.0,
                direction=-1,
                search_start=today_start,
                search_end=today_end,
                use_centre=use_centre_param
            )
            tomorrow_result = self._observer.get_time_at_elevation(
                target_elevation=0.0,
                direction=-1,
                search_start=tomorrow_start,
                search_end=tomorrow_end,
                use_centre=use_centre_param
            )
            
            # Convert results
            today_time = today_result if isinstance(today_result, datetime) else None
            tomorrow_time = tomorrow_result if isinstance(tomorrow_result, datetime) else None

            # Get azimuth at today's set time
            set_azimuth = None
            if today_time is not None:
                _, az, _ = self._observer.position(today_time, apply_refraction=False)
                if az is not None:
                    set_azimuth = round(float(az), 1)
            
            # Get azimuth at tomorrow's set time
            tomorrow_set_azimuth = None
            if tomorrow_time is not None:
                _, az, _ = self._observer.position(tomorrow_time, apply_refraction=False)
                if az is not None:
                    tomorrow_set_azimuth = round(float(az), 1)

            # Set native value (today's time)
            self._attr_native_value = today_time
            
            # Set attributes
            self._attr_extra_state_attributes = {
                "today": today_time.isoformat() if today_time else None,
                "tomorrow": tomorrow_time.isoformat() if tomorrow_time else None,
                "set_azimuth": set_azimuth,
                "tomorrow_set_azimuth": tomorrow_set_azimuth,
            }
            
            self._attr_available = True
            self.async_write_ha_state()
            
        except Exception as e:
            _LOGGER.error(f"Error updating SetSensor: {e}", exc_info=True)
            self._attr_available = False
            self.async_write_ha_state()


class TransitSensor(SensorEntity):
    """Transit sensor — today's meridian transit time and elevation."""

    def __init__(self, hass: HomeAssistant, entry_id: str, body, body_key: str):
        self.hass = hass
        self._entry_id = entry_id
        self._body = body
        self._body_key = body_key

        config_data = get_config_entry_data(entry_id)
        location_name = config_data.get("location_name") if config_data else None

        self._attr_name = get_formatted_sensor_name("Transit", body_key, location_name=location_name)
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{body_key}_transit"
        self._attr_has_entity_name = True
        self._attr_native_value = None
        self._attr_device_class = "timestamp"
        self._attr_available = False
        self._unsub_update = None
        self._unsub_midnight = None
        self._observer = None

        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": NAME,
        }

    @property
    def should_poll(self) -> bool:
        return False

    async def async_added_to_hass(self) -> None:
        await self.async_update()
        self._schedule_midnight_update(dt_util.now())

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None
        await super().async_will_remove_from_hass()

    def _schedule_midnight_update(self, now: datetime) -> None:
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        self._unsub_midnight = async_track_point_in_time(self.hass, self._on_midnight, next_midnight)

    async def _on_midnight(self, now: datetime) -> None:
        await self.async_update(now)
        self._schedule_midnight_update(now)

    def _get_transit_for_window(self, window_start: datetime, window_end: datetime) -> Optional[datetime]:
        """Return the meridian transit that falls within the given window."""
        try:
            self._observer.set_search_window(search_start=window_start, search_end=window_end)
            result = self._observer.next_transit
            if result is not None and window_start <= result < window_end:
                return result
            return None
        except Exception as e:
            _LOGGER.error(f"Error finding transit in window {window_start}: {e}", exc_info=True)
            return None

    async def async_update(self, now=None) -> None:
        """Update the sensor state."""
        try:
            if now is None:
                now = dt_util.now()

            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

            if self._observer is None:
                self._observer = BodyObserver(
                    entry_id=self._entry_id,
                    body=self._body,
                    search_start=today_start,
                    search_end=tomorrow_start,
                    hass=self.hass,
                )

            today_time = await self.hass.async_add_executor_job(
                self._get_transit_for_window, today_start, tomorrow_start
            )

            transit_elevation = None
            if today_time is not None:
                el, _, _ = self._observer.position(today_time, apply_refraction=False)
                if el is not None:
                    transit_elevation = round(float(el), 1)

            self._attr_native_value = today_time
            self._attr_extra_state_attributes = {
                "today": today_time.isoformat() if today_time else None,
                "transit_elevation": transit_elevation,
            }
            self._attr_available = True
            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.error(f"Error updating TransitSensor: {e}", exc_info=True)
            self._attr_available = False
            self.async_write_ha_state()


class AntitransitSensor(SensorEntity):
    """Antitransit sensor — today's anti-meridian transit time and elevation."""

    def __init__(self, hass: HomeAssistant, entry_id: str, body, body_key: str):
        self.hass = hass
        self._entry_id = entry_id
        self._body = body
        self._body_key = body_key

        config_data = get_config_entry_data(entry_id)
        location_name = config_data.get("location_name") if config_data else None

        self._attr_name = get_formatted_sensor_name("Antitransit", body_key, location_name=location_name)
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{body_key}_antitransit"
        self._attr_has_entity_name = True
        self._attr_native_value = None
        self._attr_device_class = "timestamp"
        self._attr_available = False
        self._unsub_update = None
        self._unsub_midnight = None
        self._observer = None

        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": NAME,
        }

    @property
    def should_poll(self) -> bool:
        return False

    async def async_added_to_hass(self) -> None:
        await self.async_update()
        self._schedule_midnight_update(dt_util.now())

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None
        await super().async_will_remove_from_hass()

    def _schedule_midnight_update(self, now: datetime) -> None:
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        self._unsub_midnight = async_track_point_in_time(self.hass, self._on_midnight, next_midnight)

    async def _on_midnight(self, now: datetime) -> None:
        await self.async_update(now)
        self._schedule_midnight_update(now)

    def _get_antitransit_for_window(self, window_start: datetime, window_end: datetime) -> Optional[datetime]:
        """Return the anti-meridian transit that falls within the given window."""
        try:
            self._observer.set_search_window(search_start=window_start, search_end=window_end)
            result = self._observer.next_antitransit
            if result is not None and window_start <= result < window_end:
                return result
            return None
        except Exception as e:
            _LOGGER.error(f"Error finding antitransit in window {window_start}: {e}", exc_info=True)
            return None

    async def async_update(self, now=None) -> None:
        """Update the sensor state."""
        try:
            if now is None:
                now = dt_util.now()

            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

            if self._observer is None:
                self._observer = BodyObserver(
                    entry_id=self._entry_id,
                    body=self._body,
                    search_start=today_start,
                    search_end=tomorrow_start,
                    hass=self.hass,
                )

            today_time = await self.hass.async_add_executor_job(
                self._get_antitransit_for_window, today_start, tomorrow_start
            )

            antitransit_elevation = None
            if today_time is not None:
                el, _, _ = self._observer.position(today_time, apply_refraction=False)
                if el is not None:
                    antitransit_elevation = round(float(el), 1)

            self._attr_native_value = today_time
            self._attr_extra_state_attributes = {
                "today": today_time.isoformat() if today_time else None,
                "antitransit_elevation": antitransit_elevation,
            }
            self._attr_available = True
            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.error(f"Error updating AntitransitSensor: {e}", exc_info=True)
            self._attr_available = False
            self.async_write_ha_state()


class SolarDeclinationNormalizedSensor(SensorEntity):
    """Sensor for Solar Declination Normalized (-1 to +1 seasonal position)."""
    
    def __init__(self, hass: HomeAssistant, entry_id: str, enabled_by_default: bool = False):
        """Initialize Solar Declination Normalized sensor.
        
        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID
            enabled_by_default: Whether sensor should be enabled by default
        """
        self.hass = hass
        self._entry_id = entry_id
        
        # Get location_name from config
        config_data = get_config_entry_data(entry_id)
        location_name = config_data.get('location_name') if config_data else None
        
        # Set sensor name
        self._attr_name = get_formatted_sensor_name("Declination Normalized", "sun", location_name=location_name)
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_declination_normalized"
        self._attr_has_entity_name = True
        
        # Set entity registry enabled state
        self._attr_entity_registry_enabled_default = enabled_by_default
        
        # Sensor properties
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = "∞"  # Infinity symbol
        self._attr_device_class = None
        self._attr_state_class = "measurement"
        self._attr_available = False
        
        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": NAME,
        }
        
        # Initialize flag and event listener
        self._initialized = False
        self._unsubscribe_cache_events = None
    
    @property
    def icon(self):
        """Return the icon."""
        return "mdi:infinity"
    
    @property
    def should_poll(self) -> bool:
        """Return False as entity should not be polled by Home Assistant."""
        return False
    
    async def async_added_to_hass(self) -> None:
        """Set up when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Register event listener for cache updates
        self._unsubscribe_cache_events = self.hass.bus.async_listen(
            f"{DOMAIN}_declination_cache_updated",
            self._handle_cache_update
        )
        
        # Do initial update
        await self.async_update()
    
    async def async_will_remove_from_hass(self) -> None:
        """Clean up event listeners when sensor is removed."""
        if self._unsubscribe_cache_events:
            self._unsubscribe_cache_events()
            self._unsubscribe_cache_events = None
        await super().async_will_remove_from_hass()
    
    async def _handle_cache_update(self, event):
        """Handle cache update events."""
        if event.data.get("entry_id") == self._entry_id:
            now_local = dt_util.now()
            if now_local.hour < 12:
                next_update = now_local.replace(hour=12, minute=0, second=0, microsecond=0)
            else:
                next_update = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

            # Update sensor immediately with cached data (no rounding)
            self._attr_native_value = event.data["normalized"]
            
            # Get current declination for attributes
            config_data = get_config_entry_data(self._entry_id)
            try:
                from .utils import get_body
                sun = get_body("sun", eph)
                if sun:
                    observer = BodyObserver(
                        entry_id=self._entry_id,
                        body=sun,
                        search_start=dt_util.now(),
                        hass=self.hass
                    )
                    current_declination = observer.current_declination
                else:
                    current_declination = None
            except Exception:
                current_declination = None
            
            attributes = {
                "next_update": next_update.isoformat(),
            }
            if current_declination is not None:
                attributes['declination'] = round(current_declination, 2)
            
            self._attr_extra_state_attributes = attributes
            
            # Mark as initialized
            self._initialized = True
            
            # Schedule state update
            self._attr_available = True
            self.async_write_ha_state()
    
    async def async_update(self, now=None) -> None:
        """Update the sensor state."""
        # If we're already initialized via events, don't do anything
        if self._initialized:
            return
        
        try:
            # Get cached declination data
            normalized, previous_solstice, next_solstice, target_time = get_cached_declination_normalized(
                self.hass, self._entry_id, now
            )
            
            now_local = dt_util.now() if now is None else now
            if now_local.hour < 12:
                next_update = now_local.replace(hour=12, minute=0, second=0, microsecond=0)
            else:
                next_update = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

            if normalized is not None:
                # Update the sensor value (no rounding)
                self._attr_native_value = normalized
                
                # Get current declination for attributes
                config_data = get_config_entry_data(self._entry_id)
                try:
                    from .utils import get_body
                    sun = get_body("sun", eph)
                    if sun:
                        observer = BodyObserver(
                            entry_id=self._entry_id,
                            body=sun,
                            search_start=dt_util.now() if now is None else now,
                            hass=self.hass
                        )
                        current_declination = observer.current_declination
                    else:
                        current_declination = None
                except Exception:
                    current_declination = None
                
                attributes = {
                    "next_update": next_update.isoformat(),
                }
                if current_declination is not None:
                    attributes['declination'] = round(current_declination, 2)
                
                self._attr_extra_state_attributes = attributes
                
                # Mark as initialized
                self._initialized = True
                self._attr_available = True
            else:
                # Fallback to manual calculation if cache unavailable
                config_data = get_config_entry_data(self._entry_id)
                if config_data:
                    try:
                        normalized, previous_solstice, next_solstice, june_solstice, december_solstice = get_declination_normalized(
                            target_time=dt_util.now() if now is None else now,
                            entry_id=self._entry_id,
                            config_data=config_data,
                            cached_solstices=None
                        )
                        self._attr_native_value = normalized
                        
                        # Get current declination for attributes
                        try:
                            from .utils import get_body
                            sun = get_body("sun", eph)
                            if sun:
                                observer = BodyObserver(
                                    entry_id=self._entry_id,
                                    body=sun,
                                    search_start=dt_util.now() if now is None else now,
                                    hass=self.hass
                                )
                                current_declination = observer.current_declination
                            else:
                                current_declination = None
                        except Exception:
                            current_declination = None
                        
                        attributes = {
                            "next_update": next_update.isoformat(),
                        }
                        if current_declination is not None:
                            attributes['declination'] = round(current_declination, 2)
                        
                        self._attr_extra_state_attributes = attributes
                        self._initialized = True
                        self._attr_available = True
                    except Exception as e:
                        _LOGGER.error(f"Error calculating declination normalized for entry {self._entry_id}: {e}", exc_info=True)
                        self._attr_available = False
        except Exception as e:
            _LOGGER.error(f"Error updating SolarDeclinationNormalizedSensor: {e}", exc_info=True)
            self._attr_available = False
        
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Moon Phase Sensor
# ---------------------------------------------------------------------------

def _determine_moon_phase(phase_angle: float):
    """Return (phase_name, exit_angle) for the given phase_angle (0–360°).

    ``exit_angle`` is the upper boundary of the current phase band; the sensor
    schedules its next update for when the phase angle crosses that value.
    """
    phase_name = MOON_PHASE_BOUNDARIES[-1][1]
    band_index = len(MOON_PHASE_BOUNDARIES) - 1

    for i, (lower, name) in enumerate(MOON_PHASE_BOUNDARIES):
        next_lower = MOON_PHASE_BOUNDARIES[i + 1][0] if i + 1 < len(MOON_PHASE_BOUNDARIES) else 360
        if lower <= phase_angle < next_lower:
            phase_name = name
            band_index = i
            break

    next_band_index = (band_index + 1) % len(MOON_PHASE_BOUNDARIES)
    exit_angle = MOON_PHASE_BOUNDARIES[next_band_index][0]

    return phase_name, exit_angle


class MoonPhaseSensor(SensorEntity):
    """Moon phase sensor — state is the current phase name, updates at each phase boundary."""

    def __init__(self, hass: HomeAssistant, entry_id: str, body, body_key: str):
        self.hass = hass
        self._entry_id = entry_id
        self._body = body
        self._body_key = body_key
        self._observer = None
        self._unsub_update = None

        config_data = get_config_entry_data(entry_id)
        location_name = config_data.get('location_name') if config_data else None

        self._attr_name = get_formatted_sensor_name("Phase", body_key, location_name=location_name)
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{body_key}_moon_phase"
        self._attr_has_entity_name = True
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = None
        self._attr_device_class = None
        self._attr_icon = MOON_PHASE_ICONS.get("New Moon")
        self._attr_available = False
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": NAME,
        }

    @property
    def should_poll(self) -> bool:
        return False

    async def async_added_to_hass(self) -> None:
        await self.async_update()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None
        await super().async_will_remove_from_hass()

    async def async_update(self, now=None) -> None:
        """Update state and schedule next update at the upcoming phase boundary."""
        try:
            if now is None:
                now = dt_util.now()

            if self._observer is None:
                self._observer = BodyObserver(
                    entry_id=self._entry_id,
                    body=self._body,
                    search_start=now,
                    hass=self.hass,
                )
            else:
                self._observer.set_search_window(search_start=now)

            phase_angle = self._observer.current_phase_angle
            phase_name, exit_angle = _determine_moon_phase(phase_angle)

            # Get naming convention from config
            config_data = get_config_entry_data(self._entry_id) or {}
            moon_naming_convention = config_data.get("moon_naming_convention", "none")

            # Determine display state
            if phase_name == "Full Moon" and moon_naming_convention != "none":
                # Find the exact full moon time (180° crossing nearest to now)
                full_moon_time = await self.hass.async_add_executor_job(
                    find_full_moon_near, now, eph, ts
                ) or now
                specific_name = await self.hass.async_add_executor_job(
                    get_moon_name, full_moon_time, moon_naming_convention, eph, ts
                )
                display_state = f"Full Moon ({specific_name})" if specific_name else "Full Moon"
            else:
                display_state = phase_name

            self._attr_native_value = display_state
            self._attr_icon = MOON_PHASE_ICONS.get(phase_name, MOON_PHASE_ICONS.get("New Moon"))
            self._attr_available = True

            next_phase_time = await self.hass.async_add_executor_job(
                self._observer.get_time_at_phase_angle,
                exit_angle,
                now,
                now + timedelta(days=35),
            )

            # Determine the name of the next phase band
            current_band = next((b for b in MOON_PHASE_BOUNDARIES if b[1] == phase_name), MOON_PHASE_BOUNDARIES[0])
            current_band_index = MOON_PHASE_BOUNDARIES.index(current_band)
            next_band_index = (current_band_index + 1) % len(MOON_PHASE_BOUNDARIES)
            next_phase_name = MOON_PHASE_BOUNDARIES[next_band_index][1]

            # Compute next_full_moon_name attribute
            if moon_naming_convention != "none":
                search_from = now + timedelta(days=1) if phase_name == "Full Moon" else now
                next_fm_dt = await self.hass.async_add_executor_job(
                    find_next_full_moon, search_from, eph, ts
                )
                if next_fm_dt:
                    next_full_moon_name = await self.hass.async_add_executor_job(
                        get_moon_name, next_fm_dt, moon_naming_convention, eph, ts
                    ) or None
                else:
                    next_full_moon_name = None
            else:
                next_full_moon_name = None

            self._attr_extra_state_attributes = {
                "next_update": next_phase_time.isoformat() if next_phase_time else None,
                "next_phase": next_phase_name,
                "next_full_moon_name": next_full_moon_name,
            }

            if self._unsub_update:
                self._unsub_update()
                self._unsub_update = None

            if next_phase_time:
                self._unsub_update = async_track_point_in_time(
                    self.hass, self.async_update, next_phase_time
                )
            else:
                _LOGGER.warning(
                    f"MoonPhaseSensor [{self._entry_id}]: could not find next phase crossing "
                    f"for exit_angle={exit_angle}°; falling back to 60 s poll"
                )
                self._unsub_update = async_track_point_in_time(
                    self.hass, self.async_update, now + timedelta(minutes=1)
                )

        except Exception as e:
            _LOGGER.error(f"Error updating MoonPhaseSensor: {e}", exc_info=True)
            self._attr_available = False

        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Phase Angle Sensor
# ---------------------------------------------------------------------------

class PhaseAngleSensor(SensorEntity):
    """Phase angle sensor — state is the body's ecliptic phase angle (0–359°), updates every 1°."""

    def __init__(self, hass: HomeAssistant, entry_id: str, body, body_key: str):
        self.hass = hass
        self._entry_id = entry_id
        self._body = body
        self._body_key = body_key
        self._observer = None
        self._unsub_update = None

        config_data = get_config_entry_data(entry_id)
        location_name = config_data.get('location_name') if config_data else None

        self._attr_name = get_formatted_sensor_name("Phase Angle", body_key, location_name=location_name)
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{body_key}_phase_angle"
        self._attr_has_entity_name = True
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = "°"
        self._attr_device_class = None
        self._attr_icon = "mdi:orbit"
        self._attr_available = False
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": NAME,
        }

    @property
    def should_poll(self) -> bool:
        return False

    async def async_added_to_hass(self) -> None:
        await self.async_update()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None
        await super().async_will_remove_from_hass()

    async def async_update(self, now=None) -> None:
        """Update state and schedule next update at the next 1° phase angle boundary."""
        try:
            if now is None:
                now = dt_util.now()

            if self._observer is None:
                self._observer = BodyObserver(
                    entry_id=self._entry_id,
                    body=self._body,
                    search_start=now,
                    hass=self.hass,
                )
            else:
                self._observer.set_search_window(search_start=now)

            phase_angle = self._observer.current_phase_angle
            current_integer = round(phase_angle) % 360

            self._attr_native_value = current_integer
            self._attr_available = True

            # Next update at the next integer degree boundary
            next_target = (current_integer + 1) % 360
            next_update_time = await self.hass.async_add_executor_job(
                self._observer.get_time_at_phase_angle,
                float(next_target),
                now,
                now + timedelta(days=35),
            )

            attributes = {
                "next_update": next_update_time.isoformat() if next_update_time else None,
                "next_target": next_target,
            }
            if DEBUG_ATTRIBUTES:
                attributes["phase_angle_precise"] = round(phase_angle, 4)
            self._attr_extra_state_attributes = attributes

            if self._unsub_update:
                self._unsub_update()
                self._unsub_update = None

            if next_update_time:
                self._unsub_update = async_track_point_in_time(
                    self.hass, self.async_update, next_update_time
                )
            else:
                _LOGGER.warning(
                    f"PhaseAngleSensor [{self._entry_id}]: could not find next 1° crossing "
                    f"for target={next_target}°; falling back to 60 s poll"
                )
                self._unsub_update = async_track_point_in_time(
                    self.hass, self.async_update, now + timedelta(minutes=1)
                )

        except Exception as e:
            _LOGGER.error(f"Error updating PhaseAngleSensor: {e}", exc_info=True)
            self._attr_available = False

        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Parallactic Angle Sensor
# ---------------------------------------------------------------------------

class ParallacticAngleSensor(SensorEntity):
    """Parallactic angle sensor — updates every 1°.

    The parallactic angle is the rotation of the body's disk relative to the
    horizon.  It captures both the observer's hemisphere (positive in northern
    hemisphere, negative in southern) and the body's daily arc across the sky,
    making it suitable for rotating a moon-phase image to match the sky view
    at the sensor's location.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, body, body_key: str):
        self.hass = hass
        self._entry_id = entry_id
        self._body = body
        self._body_key = body_key
        self._observer = None
        self._unsub_update = None

        config_data = get_config_entry_data(entry_id)
        location_name = config_data.get('location_name') if config_data else None

        self._attr_name = get_formatted_sensor_name("Parallactic Angle", body_key, location_name=location_name)
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{body_key}_parallactic_angle"
        self._attr_has_entity_name = True
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = "°"
        self._attr_device_class = None
        self._attr_icon = "mdi:angle-acute"
        self._attr_available = False
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": NAME,
        }

    @property
    def should_poll(self) -> bool:
        return False

    async def async_added_to_hass(self) -> None:
        await self.async_update()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None
        await super().async_will_remove_from_hass()

    async def async_update(self, now=None) -> None:
        """Update state and schedule next update at the next 1° boundary."""
        next_update_time = None
        fallback_seconds = 60

        try:
            if now is None:
                now = dt_util.now()

            if self._observer is None:
                self._observer = BodyObserver(
                    entry_id=self._entry_id,
                    body=self._body,
                    search_start=now,
                    hass=self.hass,
                )
            else:
                self._observer.set_search_window(search_start=now)

            q = self._observer.current_parallactic_angle
            q_int = int(q % 360)

            self._attr_native_value = q_int
            self._attr_available = True

            # Check altitude — near the zenith the angle changes rapidly / is ill-defined
            alt, _, _ = self._observer.position(now, apply_refraction=False)
            near_zenith = bool(alt is not None and alt > 85.0)

            # Determine direction of change analytically
            # sign = sin(φ)·cos(δ)·cos(H) − cos(φ)·sin(δ)
            # Positive → increasing, negative → decreasing, ~0 → at extremum
            import math as _math
            t_sf = self._observer._ts.from_datetime(now)
            apparent = self._observer._earth_observer.at(t_sf).observe(self._body).apparent()
            ra_obj, dec_obj, _ = apparent.radec()
            last = t_sf.gast + self._observer._longitude / 15.0
            H_rad = _math.radians((last - ra_obj.hours) * 15.0)
            dec_rad = _math.radians(dec_obj.degrees)
            lat_rad = _math.radians(self._observer._latitude)
            sign_val = (
                _math.sin(lat_rad) * _math.cos(dec_rad) * _math.cos(H_rad)
                - _math.cos(lat_rad) * _math.sin(dec_rad)
            )
            at_extremum = abs(sign_val) < PARALLACTIC_EXTREMUM_TOLERANCE
            direction = 0 if at_extremum else (1 if sign_val > 0 else -1)

            use_fallback = near_zenith or direction == 0 or q_int >= 358 or q_int <= 1

            # Near the extremum or zenith, poll quickly so we catch the direction reversal
            if at_extremum or near_zenith:
                fallback_seconds = 30

            next_target = None
            if not use_fallback:
                if direction == 1:
                    next_target = (q_int + 1) % 360
                elif direction == -1:
                    next_target = (q_int - 1) % 360
                next_update_time = await self.hass.async_add_executor_job(
                    self._observer.get_time_at_parallactic_angle,
                    q,
                    direction,
                    now,
                    now + timedelta(hours=6),
                )

            attributes = {
                "next_update": next_update_time.isoformat() if next_update_time else None,
                "next_target": next_target,
            }
            if DEBUG_ATTRIBUTES:
                attributes["parallactic_angle_precise"] = round(q, 4)
                attributes["direction"] = direction
                attributes["near_zenith"] = near_zenith
            self._attr_extra_state_attributes = attributes

        except Exception as e:
            _LOGGER.error(f"Error updating ParallacticAngleSensor: {e}", exc_info=True)
            self._attr_available = False

        # Always reschedule — even after an exception — so the sensor self-recovers
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None

        if now is None:
            now = dt_util.now()

        if next_update_time:
            self._unsub_update = async_track_point_in_time(
                self.hass, self.async_update, next_update_time
            )
        else:
            self._unsub_update = async_track_point_in_time(
                self.hass, self.async_update, now + timedelta(seconds=fallback_seconds)
            )

        self.async_write_ha_state()
