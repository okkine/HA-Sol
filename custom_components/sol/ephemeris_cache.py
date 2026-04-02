"""Ephemeris cache manager (transits, antitransits, and reversals)."""

from __future__ import annotations

import datetime
import json
import logging
import math
import time
from datetime import timedelta, timezone
from typing import Any, Dict, List, Optional
import numpy as np

from skyfield import searchlib

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CACHED_TRANSIT_ANTITRANSIT_COUNT,
    TRANSIT_THRESHOLD,
    REVERSAL_THRESHOLD,
    REVERSAL_SCAN_BUFFER,
    AZIMUTH_TOLERANCE_BASE,
    DEBUG_ATTRIBUTES,
    EPHEMERIS_CACHE_VERSION,
    ts,
    eph,
)
from .utils import get_body, is_within_reversal_range
from .ephemeris_store import load_ephemeris_cache, save_ephemeris_cache, remove_ephemeris_cache
from .config_store import get_config_entry_data
from .body_observer import BodyObserver

_LOGGER = logging.getLogger(__name__)


class EphemerisCacheManager:
    """Manages ephemeris cache (transits, antitransits, and reversals) with persistent storage."""
    
    def __init__(self, hass: HomeAssistant, domain: str):
        """Initialize the checkpoint cache manager."""
        self.hass = hass
        self._domain = domain
        self._maintenance_timers = {}  # Dict[(entry_id, body_key), timer]
        self._initialized_entries = {}  # Dict[entry_id, set(body_keys)]
        self._memory_cache = {}  # Dict[(entry_id, body_key), cache_dict] - in-memory cache to avoid repeated disk loads
        self._observers = {}  # Dict[(entry_id, body_key), BodyObserver] - reuse one observer per entry/body

    def _cache_metadata(self) -> Dict[str, Any]:
        """Return metadata for persisted ephemeris caches."""
        return {
            "cache_version": EPHEMERIS_CACHE_VERSION,
            "debug_attributes_enabled": DEBUG_ATTRIBUTES,
        }

    def _apply_cache_metadata(self, cache: Dict[str, Any]) -> Dict[str, Any]:
        """Attach metadata to cache payload before persisting."""
        cache.update(self._cache_metadata())
        return cache

    def _add_debug_cache_fields(
        self,
        payload: Dict[str, Any],
        *,
        declination: Optional[float] = None,
        within_reversal_range: Optional[bool] = None,
        reversal_search: Optional[str] = None,
    ) -> None:
        """Add debug-only cache fields when debug mode is enabled."""
        if not DEBUG_ATTRIBUTES:
            return
        payload["declination"] = declination
        payload["within_reversal_range"] = within_reversal_range
        payload["reversal_search"] = reversal_search

    def _calculate_azimuth_rate_deg_per_sec(
        self,
        observer: BodyObserver,
        event_time: datetime.datetime,
    ) -> Optional[float]:
        """Local azimuth rate (deg/s) from Skyfield apparent longitude rate in the observer horizon frame.

        Uses ``frame_latlon_and_rates`` on the apparent position (same geometry as ``altaz()``),
        so it is an instantaneous rate rather than a sampled finite difference.
        """
        try:
            if not isinstance(event_time, datetime.datetime):
                return None
            utc_time = dt_util.as_utc(event_time)
            t = observer._ts.from_datetime(utc_time)
            apparent = observer._earth_observer.at(t).observe(observer._body).apparent()
            _, _, _, _, lon_rate, _ = apparent.frame_latlon_and_rates(observer._observer_location)
            per_sec = lon_rate.degrees.per_second
            val = float(per_sec)
            if math.isnan(val):
                return None
            return val
        except Exception as e:
            _LOGGER.debug("Failed to estimate azimuth rate at %s: %s", event_time, e)
            return None
    
    async def initialize_entry(self, entry_id: str, body_key: str) -> None:
        """
        Initialize checkpoint cache for an entry and body.
        Single code path: no latitude/declination branch. Always transit/antitransit first, then reversals in gaps.
        """
        if entry_id in self._initialized_entries and body_key in self._initialized_entries[entry_id]:
            return
        
        try:
            body = get_body(body_key, eph)
            if body is None:
                _LOGGER.error(f"Invalid body_key {body_key} for entry {entry_id}")
                return
            
            cache = await load_ephemeris_cache(self.hass, entry_id, body_key)
            if cache is None:
                cache = await self._initialize_cache(entry_id, body_key, body)
            else:
                cache = await self._validate_and_cleanup_cache(entry_id, body_key, cache)
            
            # Store in memory cache (init and validate already save and update memory cache, but ensure it's set)
            cache_key = (entry_id, body_key)
            if cache is not None:
                self._memory_cache[cache_key] = cache
            
            await self._schedule_next_maintenance(entry_id, body_key)
            if entry_id not in self._initialized_entries:
                self._initialized_entries[entry_id] = set()
            self._initialized_entries[entry_id].add(body_key)
            
        except Exception as e:
            _LOGGER.error(f"Error initializing checkpoint cache for entry {entry_id}, body {body_key}: {e}", exc_info=True)
    
    async def remove_entry(self, entry_id: str, body_key: str = None) -> None:
        """
        Remove checkpoint cache for an entry and body (or all bodies for entry if body_key is None).
        
        Args:
            entry_id: Config entry ID
            body_key: Body identifier (optional, if None removes all bodies for entry)
        """
        if body_key is None:
            # Remove all bodies for this entry
            if entry_id in self._initialized_entries:
                body_keys = list(self._initialized_entries[entry_id])
                for bk in body_keys:
                    await self.remove_entry(entry_id, bk)
            return
        
        # Cancel maintenance timer
        timer_key = (entry_id, body_key)
        if timer_key in self._maintenance_timers:
            self._maintenance_timers[timer_key].cancel()
            del self._maintenance_timers[timer_key]
        
        # Remove from initialized set
        if entry_id in self._initialized_entries:
            self._initialized_entries[entry_id].discard(body_key)
            if not self._initialized_entries[entry_id]:
                del self._initialized_entries[entry_id]
        
        # Remove from memory cache
        cache_key = (entry_id, body_key)
        self._memory_cache.pop(cache_key, None)
        
        # Remove reused observer
        self._observers.pop(cache_key, None)
        
        # Remove persistent storage
        await remove_ephemeris_cache(self.hass, entry_id, body_key)
    
    async def get_reversals(self, entry_id: str, body_key: str, current_time: Optional[datetime.datetime] = None) -> Dict[str, Any]:
        """
        Get checkpoint cache for an entry and body.
        
        Args:
            entry_id: Config entry ID
            body_key: Body identifier (e.g., "sun", "moon", "jupiter")
            current_time: Current time (defaults to now)
            
        Returns:
            Cache dictionary with last_known_state and checkpoints
        """
        if current_time is None:
            current_time = dt_util.now()
        
        # Ensure initialized
        if entry_id not in self._initialized_entries or body_key not in self._initialized_entries[entry_id]:
            await self.initialize_entry(entry_id, body_key)
        
        # Check in-memory cache first
        cache_key = (entry_id, body_key)
        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]
        
        # Load from disk if not in memory
        cache = await load_ephemeris_cache(self.hass, entry_id, body_key)
        
        if cache is None:
            _LOGGER.warning(f"Cache missing for initialized entry {entry_id}, body {body_key} - using fallback")
        else:
            # Store in memory cache
            self._memory_cache[cache_key] = cache
        
        return cache
    
    def _get_observer(
        self,
        entry_id: str,
        body_key: str,
        body,
        search_start: Optional[datetime.datetime] = None,
        search_end: Optional[datetime.datetime] = None,
    ) -> BodyObserver:
        """Get or create a BodyObserver for (entry_id, body_key). Reuse and set_search_window if window given."""
        key = (entry_id, body_key)
        if key not in self._observers:
            start = search_start if search_start is not None else dt_util.now()
            end = search_end if search_end is not None else (start + timedelta(hours=24))
            self._observers[key] = BodyObserver(entry_id, body, search_start=start, search_end=end, hass=self.hass)
        else:
            observer = self._observers[key]
            if search_start is not None or search_end is not None:
                observer.set_search_window(search_start=search_start, search_end=search_end)
        return self._observers[key]
    
    def _get_next_transit_or_antitransit(
        self,
        entry_id: str,
        body_key: str,
        body,
        after_time: datetime.datetime,
        last_event_type: Optional[str],
    ) -> tuple[Optional[datetime.datetime], Optional[str], BodyObserver]:
        """
        Get the next single transit or antitransit after after_time.
        Uses 13-hour window. Alternate rule: if last_event_type is 'transit', get next antitransit first;
        if 'antitransit', get next transit first; if None, get the earlier of the two.
        Returns (event_time, event_type, observer) or (None, None, observer).
        """
        search_end = after_time + timedelta(hours=13)
        observer = self._get_observer(entry_id, body_key, body, search_start=after_time, search_end=search_end)
        next_transit = observer.next_transit
        next_antitransit = observer.next_antitransit
        
        if last_event_type == 'transit':
            if next_antitransit is not None:
                return next_antitransit, 'antitransit', observer
            if next_transit is not None:
                return next_transit, 'transit', observer
        elif last_event_type == 'antitransit':
            if next_transit is not None:
                return next_transit, 'transit', observer
            if next_antitransit is not None:
                return next_antitransit, 'antitransit', observer
        else:
            if next_transit is not None and next_antitransit is not None:
                if next_transit < next_antitransit:
                    return next_transit, 'transit', observer
                return next_antitransit, 'antitransit', observer
            if next_transit is not None:
                return next_transit, 'transit', observer
            if next_antitransit is not None:
                return next_antitransit, 'antitransit', observer
        return None, None, observer
    
    async def _initialize_cache(self, entry_id: str, body_key: str, body) -> Dict[str, Any]:
        """
        Initialize cache: previous T/A (closer to now) + next 2 future T/A, then reversals in gaps where azimuth direction changes.
        Single code path; no latitude check. Uses 13-hour windows. Maintains exactly CACHED_TRANSIT_ANTITRANSIT_COUNT future T/A.
        """
        now = dt_util.now()
        window_13h = timedelta(hours=13)
        config_data = get_config_entry_data(entry_id)
        latitude = config_data.get('latitude', self.hass.config.latitude)
        
        # 2.1 Previous transit or antitransit (whichever is closer to now)
        observer = self._get_observer(entry_id, body_key, body, search_start=now, search_end=now + window_13h)
        prev_transit = observer.previous_transit
        prev_antitransit = observer.previous_antitransit
        if prev_transit is None and prev_antitransit is None:
            prev_time = now
            prev_type = None
        elif prev_transit is None:
            prev_time = prev_antitransit
            prev_type = 'antitransit'
        elif prev_antitransit is None:
            prev_time = prev_transit
            prev_type = 'transit'
        else:
            if abs((prev_transit - now).total_seconds()) <= abs((prev_antitransit - now).total_seconds()):
                prev_time = prev_transit
                prev_type = 'transit'
            else:
                prev_time = prev_antitransit
                prev_type = 'antitransit'
        
        # 2.2 Next two future transit/antitransit (alternate rule, 13h windows)
        ta_events = []  # list of dicts: time, event_type, azimuth, elevation, azimuth_direction, elevation_direction, declination, within_reversal_range
        observer = self._get_observer(entry_id, body_key, body, search_start=prev_time, search_end=prev_time + window_13h)
        event_time, event_type, observer = self._get_next_transit_or_antitransit(entry_id, body_key, body, prev_time, prev_type)
        if event_time is None:
            # No next event found; build minimal cache from prev
            prev_elev = observer.position(prev_time)[0]
            prev_az = observer.position(prev_time)[1] % 360.0
            prev_az_dir = observer.calculate_azimuth_direction_from_subsolar(prev_time)
            prev_elev_dir = -1 if prev_type == 'transit' else 1 if prev_type == 'antitransit' else 1
            last_known_state = {
                'time': prev_time, 'event_type': prev_type or 'transit', 'azimuth': prev_az, 'elevation': prev_elev,
                'azimuth_direction': prev_az_dir, 'elevation_direction': prev_elev_dir,
            }
            if DEBUG_ATTRIBUTES:
                obs_prev = self._get_observer(entry_id, body_key, body, search_start=prev_time)
                decl = obs_prev.current_declination
                within_rr = is_within_reversal_range(latitude, abs(decl))
                self._add_debug_cache_fields(
                    last_known_state,
                    declination=decl,
                    within_reversal_range=within_rr,
                    reversal_search='n/a',
                )
            cache = {'last_known_state': last_known_state, 'checkpoints': []}
            cache = self._apply_cache_metadata(cache)
            await save_ephemeris_cache(self.hass, entry_id, body_key, cache)
            
            # Update memory cache
            cache_key = (entry_id, body_key)
            self._memory_cache[cache_key] = cache
            
            return cache
        
        # Build prev event data (for direction and reversal search)
        obs_prev = self._get_observer(entry_id, body_key, body, search_start=prev_time)
        prev_az = obs_prev.position(prev_time)[1] % 360.0
        prev_elev = obs_prev.position(prev_time)[0]
        prev_az_dir = obs_prev.calculate_azimuth_direction_from_subsolar(prev_time)
        prev_elev_dir = -1 if prev_type == 'transit' else 1 if prev_type == 'antitransit' else 1
        prev_ev = {'time': prev_time, 'event_type': prev_type or 'transit', 'azimuth': prev_az, 'elevation': prev_elev,
                   'azimuth_direction': prev_az_dir, 'elevation_direction': prev_elev_dir}
        if prev_ev['event_type'] in ('transit', 'antitransit'):
            prev_ev['azimuth_rate_deg_per_sec'] = self._calculate_azimuth_rate_deg_per_sec(obs_prev, prev_time)
        if DEBUG_ATTRIBUTES:
            prev_decl = obs_prev.current_declination
            self._add_debug_cache_fields(
                prev_ev,
                declination=prev_decl,
                within_reversal_range=is_within_reversal_range(latitude, abs(prev_decl)),
            )
        
        event1_time, event1_type = event_time, event_type
        obs1 = self._get_observer(entry_id, body_key, body, search_start=event1_time)
        e1_az = obs1.position(event1_time)[1] % 360.0
        e1_elev = obs1.position(event1_time)[0]
        e1_az_dir = obs1.calculate_azimuth_direction_from_subsolar(event1_time)
        e1_elev_dir = -1 if event1_type == 'transit' else 1
        ev1 = {'time': event1_time, 'event_type': event1_type, 'azimuth': e1_az, 'elevation': e1_elev,
               'azimuth_direction': e1_az_dir, 'elevation_direction': e1_elev_dir}
        if event1_type in ('transit', 'antitransit'):
            ev1['azimuth_rate_deg_per_sec'] = self._calculate_azimuth_rate_deg_per_sec(obs1, event1_time)
        if DEBUG_ATTRIBUTES:
            e1_decl = obs1.current_declination
            self._add_debug_cache_fields(
                ev1,
                declination=e1_decl,
                within_reversal_range=is_within_reversal_range(latitude, abs(e1_decl)),
            )
        
        # Second future event: from event1_time + small delta
        after_event1 = event1_time + timedelta(minutes=1)
        event2_time, event2_type, _ = self._get_next_transit_or_antitransit(entry_id, body_key, body, after_event1, event1_type)
        if event2_time is None:
            ta_events = [prev_ev, ev1]
        else:
            obs2 = self._get_observer(entry_id, body_key, body, search_start=event2_time)
            e2_az = obs2.position(event2_time)[1] % 360.0
            e2_elev = obs2.position(event2_time)[0]
            e2_az_dir = obs2.calculate_azimuth_direction_from_subsolar(event2_time)
            e2_elev_dir = -1 if event2_type == 'transit' else 1
            ev2 = {'time': event2_time, 'event_type': event2_type, 'azimuth': e2_az, 'elevation': e2_elev,
                   'azimuth_direction': e2_az_dir, 'elevation_direction': e2_elev_dir}
            if event2_type in ('transit', 'antitransit'):
                ev2['azimuth_rate_deg_per_sec'] = self._calculate_azimuth_rate_deg_per_sec(obs2, event2_time)
            if DEBUG_ATTRIBUTES:
                e2_decl = obs2.current_declination
                self._add_debug_cache_fields(
                    ev2,
                    declination=e2_decl,
                    within_reversal_range=is_within_reversal_range(latitude, abs(e2_decl)),
                )
            ta_events = [prev_ev, ev1, ev2]
        
        # 2.3 Reversals between consecutive T/A where direction changed
        checkpoints = []
        for i, ev in enumerate(ta_events):
            if i > 0 and ta_events[i - 1]['azimuth_direction'] != ev['azimuth_direction']:
                reversal_result = await self._find_azimuth_reversal(ta_events[i - 1]['time'], ev['time'], entry_id, None, body_key)
                if reversal_result:
                    rev_time, rev_az = reversal_result
                    rev_az = rev_az % 360.0
                    obs_rev = self._get_observer(entry_id, body_key, body, search_start=rev_time)
                    rev_elev = obs_rev.position(rev_time)[0]
                    rev_dir = -ta_events[i - 1]['azimuth_direction']
                    reversal_cp = {
                        'time': rev_time, 'event_type': 'reversal', 'azimuth': rev_az, 'elevation': rev_elev,
                        'azimuth_direction': rev_dir, 'elevation_direction': ta_events[i - 1]['elevation_direction'],
                    }
                    if DEBUG_ATTRIBUTES:
                        rev_decl = obs_rev.current_declination
                        self._add_debug_cache_fields(
                            reversal_cp,
                            declination=rev_decl,
                            within_reversal_range=is_within_reversal_range(latitude, abs(rev_decl)),
                            reversal_search='searched',
                        )
                    checkpoints.append(reversal_cp)
                else:
                    _LOGGER.debug("Direction changed but no reversal found between %s and %s", ta_events[i - 1]['time'], ev['time'])
            gap_searched = i > 0 and ta_events[i - 1]['azimuth_direction'] != ev['azimuth_direction']
            cp = {
                'time': ev['time'], 'event_type': ev['event_type'], 'azimuth': ev['azimuth'], 'elevation': ev['elevation'],
                'azimuth_direction': ev['azimuth_direction'], 'elevation_direction': ev['elevation_direction'],
            }
            if 'azimuth_rate_deg_per_sec' in ev:
                cp['azimuth_rate_deg_per_sec'] = ev['azimuth_rate_deg_per_sec']
            if DEBUG_ATTRIBUTES:
                reversal_search = 'searched' if gap_searched else ('skipped' if i > 0 else 'n/a')
                self._add_debug_cache_fields(
                    cp,
                    declination=ev.get('declination'),
                    within_reversal_range=ev.get('within_reversal_range'),
                    reversal_search=reversal_search,
                )
            checkpoints.append(cp)
        
        checkpoints.sort(key=lambda c: c['time'])
        
        # 2.4 Remove past (by threshold)
        now_utc = dt_util.as_utc(now)
        def is_passed(cp):
            t = dt_util.as_utc(cp['time'])
            delta = timedelta(seconds=REVERSAL_THRESHOLD if cp.get('event_type') == 'reversal' else TRANSIT_THRESHOLD) - timedelta(milliseconds=1)
            return t <= now_utc + delta
        passed_list = [cp for cp in checkpoints if is_passed(cp)]
        future_checkpoints = [cp for cp in checkpoints if not is_passed(cp)]
        
        # last_known_state: full snapshot of last passed checkpoint (including declination); elevation_direction from last T/A among passed
        if passed_list:
            last_cp = passed_list[-1]
            last_ta = None
            for cp in reversed(passed_list):
                if cp.get('event_type') in ('transit', 'antitransit'):
                    last_ta = cp
                    break
            elev_dir = last_ta['elevation_direction'] if last_ta else last_cp['elevation_direction']
            last_known_state = {
                'time': last_cp['time'], 'event_type': last_cp.get('event_type'), 'azimuth': last_cp['azimuth'],
                'elevation': last_cp['elevation'], 'azimuth_direction': last_cp['azimuth_direction'],
                'elevation_direction': elev_dir,
            }
            if 'azimuth_rate_deg_per_sec' in last_cp:
                last_known_state['azimuth_rate_deg_per_sec'] = last_cp['azimuth_rate_deg_per_sec']
            if DEBUG_ATTRIBUTES:
                self._add_debug_cache_fields(
                    last_known_state,
                    declination=last_cp.get('declination'),
                    within_reversal_range=last_cp.get('within_reversal_range'),
                    reversal_search=last_cp.get('reversal_search', 'n/a'),
                )
        else:
            last_known_state = {
                'time': prev_ev['time'], 'event_type': prev_ev.get('event_type', 'transit'),
                'azimuth': prev_ev['azimuth'], 'elevation': prev_ev['elevation'],
                'azimuth_direction': prev_ev['azimuth_direction'], 'elevation_direction': prev_ev['elevation_direction'],
            }
            if 'azimuth_rate_deg_per_sec' in prev_ev:
                last_known_state['azimuth_rate_deg_per_sec'] = prev_ev['azimuth_rate_deg_per_sec']
            if DEBUG_ATTRIBUTES:
                self._add_debug_cache_fields(
                    last_known_state,
                    declination=prev_ev.get('declination'),
                    within_reversal_range=prev_ev.get('within_reversal_range'),
                    reversal_search='n/a',
                )
        
        cache = {'last_known_state': last_known_state, 'checkpoints': future_checkpoints}
        cache = self._apply_cache_metadata(cache)
        await save_ephemeris_cache(self.hass, entry_id, body_key, cache)
        
        # Update memory cache
        cache_key = (entry_id, body_key)
        self._memory_cache[cache_key] = cache
        
        return cache
    
    async def _validate_and_cleanup_cache(
        self, 
        entry_id: str,
        body_key: str,
        cache: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate and clean up loaded cache. Single path: no latitude branch. Refill by T/A count."""
        now_utc = dt_util.as_utc(dt_util.now())

        # Rebuild cache when schema version changes.
        if cache.get("cache_version") != EPHEMERIS_CACHE_VERSION:
            _LOGGER.info(
                "Ephemeris cache version mismatch for %s/%s (stored=%s, expected=%s), reinitializing",
                entry_id,
                body_key,
                cache.get("cache_version"),
                EPHEMERIS_CACHE_VERSION,
            )
            body = get_body(body_key, eph)
            if body is None:
                _LOGGER.error(f"Invalid body_key {body_key} for entry {entry_id}")
                return cache
            return await self._initialize_cache(entry_id, body_key, body)

        # Rebuild when debug mode is toggled from False -> True.
        stored_debug_mode = cache.get("debug_attributes_enabled")
        if DEBUG_ATTRIBUTES and stored_debug_mode is not True:
            _LOGGER.info(
                "Ephemeris cache debug mode upgraded for %s/%s (stored=%s, current=%s), reinitializing",
                entry_id,
                body_key,
                stored_debug_mode,
                DEBUG_ATTRIBUTES,
            )
            body = get_body(body_key, eph)
            if body is None:
                _LOGGER.error(f"Invalid body_key {body_key} for entry {entry_id}")
                return cache
            return await self._initialize_cache(entry_id, body_key, body)
        
        if 'checkpoints' not in cache or 'last_known_state' not in cache:
            _LOGGER.warning(f"Invalid cache structure for {entry_id}, body {body_key}, reinitializing")
            body = get_body(body_key, eph)
            if body is None:
                _LOGGER.error(f"Invalid body_key {body_key} for entry {entry_id}")
                return cache
            return await self._initialize_cache(entry_id, body_key, body)
        
        def is_passed(cp):
            t = dt_util.as_utc(cp['time'])
            event_type = cp.get('event_type', 'transit')
            delta = timedelta(seconds=REVERSAL_THRESHOLD if event_type == 'reversal' else TRANSIT_THRESHOLD) - timedelta(milliseconds=1)
            return t <= now_utc + delta
        
        passed_list = [cp for cp in cache['checkpoints'] if is_passed(cp)]
        cache['checkpoints'] = [cp for cp in cache['checkpoints'] if not is_passed(cp)]
        
        if passed_list:
            last_cp = passed_list[-1]
            last_ta = None
            for cp in reversed(passed_list):
                if cp.get('event_type') in ('transit', 'antitransit'):
                    last_ta = cp
                    break
            elev_dir = last_ta['elevation_direction'] if last_ta else last_cp['elevation_direction']
            cache['last_known_state'] = {
                'time': last_cp['time'], 'event_type': last_cp.get('event_type'), 'azimuth': last_cp['azimuth'],
                'elevation': last_cp['elevation'], 'azimuth_direction': last_cp['azimuth_direction'],
                'elevation_direction': elev_dir,
            }
            if DEBUG_ATTRIBUTES:
                self._add_debug_cache_fields(
                    cache['last_known_state'],
                    declination=last_cp.get('declination'),
                    within_reversal_range=last_cp.get('within_reversal_range'),
                    reversal_search=last_cp.get('reversal_search', 'n/a'),
                )
        
        if not cache['checkpoints']:
            body = get_body(body_key, eph)
            if body is None:
                _LOGGER.error(f"Invalid body_key {body_key} for entry {entry_id}")
                return cache
            return await self._initialize_cache(entry_id, body_key, body)
        
        ta_count = sum(1 for cp in cache['checkpoints'] if cp.get('event_type') in ('transit', 'antitransit'))
        if ta_count < CACHED_TRANSIT_ANTITRANSIT_COUNT:
            body = get_body(body_key, eph)
            if body is None:
                _LOGGER.error(f"Invalid body_key {body_key} for entry {entry_id}")
            else:
                cache = await self._refill_checkpoints(entry_id, body_key, body, cache)
        
        cache = self._apply_cache_metadata(cache)
        await save_ephemeris_cache(self.hass, entry_id, body_key, cache)
        
        # Update memory cache
        cache_key = (entry_id, body_key)
        self._memory_cache[cache_key] = cache
        
        return cache
    
    async def _refill_checkpoints(
        self,
        entry_id: str,
        body_key: str,
        body,
        cache: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Refill until we have exactly CACHED_TRANSIT_ANTITRANSIT_COUNT future transit/antitransit checkpoints."""
        ta_count = sum(1 for cp in cache['checkpoints'] if cp.get('event_type') in ('transit', 'antitransit'))
        if ta_count >= CACHED_TRANSIT_ANTITRANSIT_COUNT:
            return cache
        
        config_data = get_config_entry_data(entry_id)
        latitude = config_data.get('latitude', self.hass.config.latitude)
        safety_limit = 10
        added = 0
        
        while ta_count < CACHED_TRANSIT_ANTITRANSIT_COUNT and added < safety_limit:
            last_cp = cache['checkpoints'][-1]
            search_start = last_cp['time']
            last_type = last_cp.get('event_type')
            last_elev_dir = last_cp.get('elevation_direction')
            prev_ta_cp = last_cp if last_type in ('transit', 'antitransit') else None
            if not prev_ta_cp:
                for cp in reversed(cache['checkpoints'][:-1]):
                    if cp.get('event_type') in ('transit', 'antitransit'):
                        prev_ta_cp = cp
                        break
            
            event_time, event_type, _ = self._get_next_transit_or_antitransit(
                entry_id, body_key, body, search_start, last_type if last_type in ('transit', 'antitransit') else None
            )
            if event_time is None:
                break
            
            obs = self._get_observer(entry_id, body_key, body, search_start=event_time)
            e_az = obs.position(event_time)[1] % 360.0
            e_elev = obs.position(event_time)[0]
            e_az_dir = obs.calculate_azimuth_direction_from_subsolar(event_time)
            e_elev_dir = -1 if event_type == 'transit' else 1
            
            gap_searched = prev_ta_cp is not None and prev_ta_cp['azimuth_direction'] != e_az_dir
            new_cp = {
                'time': event_time, 'event_type': event_type, 'azimuth': e_az, 'elevation': e_elev,
                'azimuth_direction': e_az_dir, 'elevation_direction': e_elev_dir,
            }
            if event_type in ('transit', 'antitransit'):
                new_cp['azimuth_rate_deg_per_sec'] = self._calculate_azimuth_rate_deg_per_sec(obs, event_time)
            if DEBUG_ATTRIBUTES:
                e_decl = obs.current_declination
                self._add_debug_cache_fields(
                    new_cp,
                    declination=e_decl,
                    within_reversal_range=is_within_reversal_range(latitude, abs(e_decl)),
                    reversal_search='searched' if gap_searched else ('skipped' if prev_ta_cp is not None else 'n/a'),
                )
            
            if gap_searched:
                rev_result = await self._find_azimuth_reversal(prev_ta_cp['time'], event_time, entry_id, None, body_key)
                if rev_result:
                    rev_time, rev_az = rev_result
                    rev_az = rev_az % 360.0
                    obs_rev = self._get_observer(entry_id, body_key, body, search_start=rev_time)
                    rev_elev = obs_rev.position(rev_time)[0]
                    rev_cp = {
                        'time': rev_time, 'event_type': 'reversal', 'azimuth': rev_az, 'elevation': rev_elev,
                        'azimuth_direction': -prev_ta_cp['azimuth_direction'],
                        'elevation_direction': prev_ta_cp['elevation_direction'],
                    }
                    if DEBUG_ATTRIBUTES:
                        rev_decl = obs_rev.current_declination
                        self._add_debug_cache_fields(
                            rev_cp,
                            declination=rev_decl,
                            within_reversal_range=is_within_reversal_range(latitude, abs(rev_decl)),
                            reversal_search='searched',
                        )
                    cache['checkpoints'].append(rev_cp)
                else:
                    _LOGGER.debug("Direction changed but no reversal found between %s and %s", search_start, event_time)
            
            cache['checkpoints'].append(new_cp)
            cache['checkpoints'].sort(key=lambda c: c['time'])
            ta_count = sum(1 for cp in cache['checkpoints'] if cp.get('event_type') in ('transit', 'antitransit'))
            added += 1
        
        return cache
    
    async def _find_azimuth_reversal(
        self,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        entry_id: str,
        observer: Optional[BodyObserver] = None,
        body_key: Optional[str] = None
    ) -> Optional[tuple[datetime.datetime, float]]:
        """
        Find azimuth reversal by bisecting on the zero crossing of sin(φ) - cos(φ)·tan(δ)·cos(H).
        Uses find_discrete; works for all geometries including reversals near T/A events.
        """
        try:
            if observer is None and body_key is None:
                return None
            if observer is None:
                body = get_body(body_key, eph)
                if body is None:
                    return None
                observer = self._get_observer(entry_id, body_key, body, search_start=start_time, search_end=end_time)
            earth_observer = observer._earth_observer
            body = observer._body
            longitude = observer._longitude
            
            t_start = ts.from_datetime(start_time)
            t_end = ts.from_datetime(end_time)
            config_data = get_config_entry_data(entry_id)
            latitude = config_data.get('latitude', 0) if config_data else 0
            phi_rad = math.radians(latitude)
            
            def direction_at(t):
                """Return 1 when azimuth direction is +1, 0 when -1. Handles scalar and array Time."""
                astrometric = earth_observer.at(t).observe(body)
                apparent = astrometric.apparent()
                ra, dec, _ = apparent.radec()
                lst = t.gmst + (longitude / 15.0)
                H_hours = ((lst - ra.hours + 12) % 24) - 12
                H_rad = np.radians(H_hours * 15.0)
                delta_rad = np.radians(dec.degrees)
                values = np.sin(phi_rad) - np.cos(phi_rad) * np.tan(delta_rad) * np.cos(H_rad)
                return (values >= 0).astype(int)
            
            window_days = (end_time - start_time).total_seconds() / 86400.0
            N = math.ceil(1.0 / 0.2)  # 5 equal segments; last sample always lands on t_end
            direction_at.step_days = window_days / N
            
            times, _ = searchlib.find_discrete(t_start, t_end, direction_at, epsilon=AZIMUTH_TOLERANCE_BASE)
            
            if len(times) == 0:
                return None
            
            reversal_time = times[0].utc_datetime()
            if not reversal_time.tzinfo:
                reversal_time = reversal_time.replace(tzinfo=timezone.utc)
            reversal_time = dt_util.as_local(reversal_time)
            
            reversal_azimuth = observer.position(reversal_time, apply_refraction=False)[1] % 360.0
            return reversal_time, reversal_azimuth
            
        except Exception as e:
            _LOGGER.error(f"Error finding azimuth reversal: {e}", exc_info=True)
            return None
    
    async def _schedule_next_maintenance(self, entry_id: str, body_key: str) -> None:
        """Schedule maintenance for before next checkpoint."""
        # Cancel existing timer
        timer_key = (entry_id, body_key)
        if timer_key in self._maintenance_timers:
            self._maintenance_timers[timer_key].cancel()
        
        try:
            # Use memory cache if available
            cache_key = (entry_id, body_key)
            if cache_key in self._memory_cache:
                cache = self._memory_cache[cache_key]
            else:
                cache = await load_ephemeris_cache(self.hass, entry_id, body_key)
                if cache is not None:
                    self._memory_cache[cache_key] = cache
            
            if cache and cache.get('checkpoints'):
                next_checkpoint = cache['checkpoints'][0]
                next_checkpoint_time = next_checkpoint['time']
                event_type = next_checkpoint.get('event_type', 'transit')
                
                # Determine threshold based on event type
                if event_type in ('transit', 'antitransit'):
                    threshold_delta = timedelta(seconds=TRANSIT_THRESHOLD) - timedelta(milliseconds=100)
                elif event_type == 'reversal':
                    threshold_delta = timedelta(seconds=REVERSAL_THRESHOLD) - timedelta(milliseconds=100)
                else:
                    # Use the lesser of the two thresholds (or either if equal)
                    threshold_delta = timedelta(seconds=min(TRANSIT_THRESHOLD, REVERSAL_THRESHOLD)) - timedelta(milliseconds=100)
                
                # Schedule for (THRESHOLD - 100ms) before checkpoint
                maintenance_time = next_checkpoint_time - threshold_delta
                
                now = dt_util.now()
                delay = (maintenance_time - now).total_seconds()
                
                if delay > 0:
                    self._maintenance_timers[timer_key] = self.hass.loop.call_later(
                        delay,
                        lambda: self.hass.async_create_task(
                            self._perform_maintenance(entry_id, body_key)
                        )
                    )
                else:
                    # Checkpoint already passed, run maintenance immediately
                    await self._perform_maintenance(entry_id, body_key)
        
        except Exception as e:
            _LOGGER.error(f"Error scheduling maintenance for {entry_id}, body {body_key}: {e}", exc_info=True)
    
    async def _perform_maintenance(self, entry_id: str, body_key: str) -> None:
        """Perform scheduled maintenance."""
        try:
            # Use memory cache if available
            cache_key = (entry_id, body_key)
            if cache_key in self._memory_cache:
                cache = self._memory_cache[cache_key]
            else:
                cache = await load_ephemeris_cache(self.hass, entry_id, body_key)
                if cache is not None:
                    self._memory_cache[cache_key] = cache
            
            if not cache:
                _LOGGER.warning(f"No cache found during maintenance for {entry_id}")
                return
            
            now_utc = dt_util.as_utc(dt_util.now())
            
            # Check if any checkpoints have passed
            # Treat checkpoints as passed if they're within threshold of now (minus 1ms to avoid race conditions)
            passed_checkpoints = []
            for cp in cache['checkpoints']:
                cp_time_utc = dt_util.as_utc(cp['time'])
                event_type = cp.get('event_type', 'transit')
                
                # Determine threshold based on event type
                if event_type in ('transit', 'antitransit'):
                    threshold_delta = timedelta(seconds=TRANSIT_THRESHOLD) - timedelta(milliseconds=1)
                elif event_type == 'reversal':
                    threshold_delta = timedelta(seconds=REVERSAL_THRESHOLD) - timedelta(milliseconds=1)
                else:
                    # Default to transit threshold for unknown types
                    threshold_delta = timedelta(seconds=TRANSIT_THRESHOLD) - timedelta(milliseconds=1)
                
                # Checkpoint is "passed" if: checkpoint_time <= now + (threshold - 1ms)
                if cp_time_utc <= now_utc + threshold_delta:
                    passed_checkpoints.append(cp)
            
            if passed_checkpoints:
                # Update last_known_state from last passed checkpoint
                last_checkpoint = passed_checkpoints[-1]
                
                # For elevation_direction, find the last transit/antitransit (not reversal)
                # Reversals don't change elevation_direction, so we need the direction from the last transit/antitransit
                last_transit_antitransit = None
                for cp in reversed(passed_checkpoints):
                    if cp.get('event_type') in ('transit', 'antitransit'):
                        last_transit_antitransit = cp
                        break
                
                # Use elevation_direction from last transit/antitransit if found, otherwise use last checkpoint
                if last_transit_antitransit:
                    elevation_direction = last_transit_antitransit['elevation_direction']
                else:
                    # No transit/antitransit found, use last checkpoint (shouldn't happen normally)
                    elevation_direction = last_checkpoint['elevation_direction']
                
                cache['last_known_state'] = {
                    'time': last_checkpoint['time'],
                    'event_type': last_checkpoint.get('event_type'),
                    'azimuth_direction': last_checkpoint['azimuth_direction'],
                    'elevation_direction': elevation_direction,
                    'azimuth': last_checkpoint['azimuth'],
                    'elevation': last_checkpoint['elevation'],
                }
                if DEBUG_ATTRIBUTES:
                    self._add_debug_cache_fields(
                        cache['last_known_state'],
                        declination=last_checkpoint.get('declination'),
                        within_reversal_range=last_checkpoint.get('within_reversal_range'),
                        reversal_search=last_checkpoint.get('reversal_search', 'n/a'),
                    )
                
                # Remove passed checkpoints
                # Remove checkpoints that are within threshold of now
                remaining_checkpoints = []
                for cp in cache['checkpoints']:
                    cp_time_utc = dt_util.as_utc(cp['time'])
                    event_type = cp.get('event_type', 'transit')
                    
                    # Determine threshold based on event type
                    if event_type in ('transit', 'antitransit'):
                        threshold_delta = timedelta(seconds=TRANSIT_THRESHOLD) - timedelta(milliseconds=1)
                    elif event_type == 'reversal':
                        threshold_delta = timedelta(seconds=REVERSAL_THRESHOLD) - timedelta(milliseconds=1)
                    else:
                        threshold_delta = timedelta(seconds=TRANSIT_THRESHOLD) - timedelta(milliseconds=1)
                    
                    # Keep checkpoint if it's still beyond the threshold
                    if cp_time_utc > now_utc + threshold_delta:
                        remaining_checkpoints.append(cp)
                
                cache['checkpoints'] = remaining_checkpoints
            
            # If all checkpoints were removed, reinitialize from scratch
            body = get_body(body_key, eph)
            if body is None:
                _LOGGER.error(f"Invalid body_key {body_key} during maintenance for {entry_id}")
            elif not cache['checkpoints']:
                cache = await self._initialize_cache(entry_id, body_key, body)
            else:
                cache = await self._refill_checkpoints(entry_id, body_key, body, cache)
            
            cache = self._apply_cache_metadata(cache)
            await save_ephemeris_cache(self.hass, entry_id, body_key, cache)
            
            # Update memory cache
            cache_key = (entry_id, body_key)
            self._memory_cache[cache_key] = cache
            
            # Reschedule for next checkpoint
            await self._schedule_next_maintenance(entry_id, body_key)
            
        except Exception as e:
            _LOGGER.error(f"Error during maintenance for {entry_id}, body {body_key}: {e}", exc_info=True)

