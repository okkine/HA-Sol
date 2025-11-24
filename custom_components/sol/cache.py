"""Caching utilities for the Sol integration."""

from __future__ import annotations

import datetime
import logging
import math
import zoneinfo
from datetime import timezone
from typing import Dict, Any, Tuple, Optional

import ephem

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .utils import get_solstice_curve, get_time_at_elevation
from .config_store import get_config_entry_data
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Module-level cache for solstice curve data
_solstice_curve_cache: Dict[str, Dict[str, Any]] = {}

# Module-level cache for solar events data - per entry instances
_solar_events_cache_instances: Dict[str, Dict[str, Any]] = {}


class SolsticeCurveCache:
    """Manages caching for solstice curve calculations."""
    
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._update_timer = None
        self._initialized = False
        
    async def initialize(self):
        """Initialize the cache and start the update timer."""
        if self._initialized:
            return
            
        # Force initial cache update for all entries
        await self._update_all_caches()
        
        # Start the timer for automatic updates
        await self._schedule_next_update()
        
        self._initialized = True
    
    async def cleanup(self):
        """Clean up the cache and cancel timer."""
        if self._update_timer:
            self._update_timer.cancel()
            self._update_timer = None
        self._initialized = False
    
    async def _schedule_next_update(self):
        """Schedule the next cache update at noon or midnight."""
        if self._update_timer:
            self._update_timer.cancel()
        
        local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
        now = dt_util.now(local_tz)
        
        # Calculate next noon and midnight
        today = now.date()
        next_noon = datetime.datetime.combine(today, datetime.time(12, 0, 0), tzinfo=local_tz)
        next_midnight = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time(0, 0, 0), tzinfo=local_tz)
        
        # If we've passed noon today, use tomorrow's noon
        #if now >= next_noon:
        #    next_noon = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time(12, 0, 0), tzinfo=local_tz)
        
        # If we've passed midnight today, use tomorrow's midnight
        #if now >= next_midnight:
        #    next_midnight = datetime.datetime.combine(today + datetime.timedelta(days=2), datetime.time(0, 0, 0), tzinfo=local_tz)
        
        # Choose the earlier of the two times
        #next_update = min(next_noon, next_midnight)
        
        # Determine if we should schedule for noon or midnight based on current time
        if now.hour < 12:  # Before noon
            next_update = datetime.datetime.combine(today, datetime.time(12, 0, 0), tzinfo=local_tz)
        else:  # After noon (including exactly at noon)
            next_update = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time(0, 0, 0), tzinfo=local_tz)
        
        # Calculate delay in seconds
        delay = (next_update - now).total_seconds()
        
        # Log the timing calculation for debugging
        
        
        # Schedule the update
        self._update_timer = self.hass.loop.call_later(
            delay,
            lambda: self.hass.async_create_task(self._perform_scheduled_update())
        )
    
    async def _perform_scheduled_update(self):
        """Perform the scheduled cache update and schedule the next one."""
        
        
        
        # Update all caches
        await self._update_all_caches()
        
        
        
        # Schedule the next update
        await self._schedule_next_update()
    
    async def _update_all_caches(self):
        """Update cache for all configured locations."""
        # Get all config entries for this integration
        entries = self.hass.config_entries.async_entries(DOMAIN)
        
        for entry in entries:
            try:
                await self._update_cache_for_entry(entry.entry_id)
            except Exception as e:
                pass
    
    async def _update_cache_for_entry(self, entry_id: str):
        """Update cache for a specific entry."""
        
        local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
        current_time = dt_util.now(local_tz)
        
        # Get config data once for this cache update
        from .config_store import get_config_entry_data
        config_data = get_config_entry_data(entry_id)
        
        # Determine target time based on current time
        local_noon = datetime.datetime.combine(
            current_time.date(),
            datetime.time(12, 0, 0),
            tzinfo=local_tz
        )
        
        # Start search from today's midnight
        today_midnight = datetime.datetime.combine(
            current_time.date(),
            datetime.time(0, 0, 0),
            tzinfo=local_tz
        )
        
        # Get today's 0° elevation crossings
        next_rising, next_setting, next_event = get_time_at_elevation(
            self.hass, 0.0, today_midnight, entry_id, use_center=True, next_transit_fallback=True, config_data=config_data
        )
        
        if current_time < local_noon:  # AM
            target_time = next_rising
        else:  # PM
            target_time = next_setting
        
        # Calculate solstice curve for the target time
        if target_time:
            normalized, previous_solstice, next_solstice = get_solstice_curve(
                self.hass, target_time, entry_id, config_data=config_data
            )
        else:
            # Fallback to current time if no elevation crossings found
            normalized, previous_solstice, next_solstice = get_solstice_curve(
                self.hass, current_time, entry_id, config_data=config_data
            )
        
        # Update cache
        _solstice_curve_cache[entry_id] = {
            'normalized': normalized,
            'previous_solstice': previous_solstice,
            'next_solstice': next_solstice,
            'calculated_at': current_time,
            'target_time': target_time,
            'entry_id': entry_id
        }
        
        # Emit event when cache updates
        self.hass.bus.async_fire(
            f"{DOMAIN}_solstice_curve_cache_updated",
            {
                "entry_id": entry_id,
                "normalized": normalized,
                "previous_solstice": previous_solstice,
                "next_solstice": next_solstice,
                "target_time": target_time
            }
        )
        
        
        
    def get_cached_solstice_curve(
        self, 
        entry_id: str,
        current_time: Optional[datetime.datetime] = None
    ) -> Tuple[float, datetime.datetime, datetime.datetime, Optional[datetime.datetime]]:
        """
        Get cached solstice curve data or calculate and cache if needed.
        
        Args:
            entry_id: Config entry ID for location-specific calculations
            current_time: Current time (defaults to now in local timezone)
            
        Returns:
            Tuple (normalized_value, previous_solstice, next_solstice, target_time)
        """
        if current_time is None:
            local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
            current_time = dt_util.now(local_tz)
        
        # Ensure current_time is a datetime object
        if not isinstance(current_time, datetime.datetime):
            local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
            current_time = dt_util.now(local_tz)
        
        # Check if we have valid cached data
        if entry_id in _solstice_curve_cache:
            cached_data = _solstice_curve_cache[entry_id]
            
            # Check if cache is still valid (within 1 hour of calculation)
            calculated_at = cached_data.get('calculated_at')
            if calculated_at:
                time_diff = abs((current_time - calculated_at).total_seconds())
                if time_diff < 3600:  # 1 hour
                    return (
                        cached_data['normalized'],
                        cached_data['previous_solstice'],
                        cached_data['next_solstice'],
                        cached_data.get('target_time')
                    )
        
        # Initialize or update cache
        return self._initialize_cache(entry_id, current_time)
    
    def _initialize_cache(
        self, 
        entry_id: str, 
        current_time: datetime.datetime
    ) -> Tuple[float, datetime.datetime, datetime.datetime, Optional[datetime.datetime]]:
        """
        Initialize cache based on current time (AM/PM).
        
        Logic:
        - If AM: Calculate for today's 0° rising (sunrise)
        - If PM: Calculate for today's 0° setting (sunset)
        - Always start search from today's midnight
        
        Args:
            entry_id: Config entry ID
            current_time: Current datetime
            
        Returns:
            Tuple (normalized_value, previous_solstice, next_solstice, target_time)
        """
        if current_time is None:
            local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
            current_time = dt_util.now(local_tz)
        
        local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
        current_local = current_time.astimezone(local_tz)
        
        # Start search from today's midnight
        today_midnight = datetime.datetime.combine(
            current_local.date(),
            datetime.time(0, 0, 0),
            tzinfo=local_tz
        )
        
        # Get today's 0° elevation crossings
        next_rising, next_setting, next_event = get_time_at_elevation(
            self.hass, 0.0, today_midnight, entry_id, use_center=True, next_transit_fallback=True, config_data=config_data
        )
        
        # Determine if AM or PM and select appropriate target
        local_noon = datetime.datetime.combine(
            current_local.date(),
            datetime.time(12, 0, 0),
            tzinfo=local_tz
        )
        
        if current_local < local_noon:  # AM
            # Use today's 0° rising
            target_time = next_rising
        else:  # PM
            # Use today's 0° setting
            target_time = next_setting
        
        # Calculate solstice curve for the target time
        if target_time:
            normalized, previous_solstice, next_solstice = get_solstice_curve(
                self.hass, target_time, entry_id, config_data=config_data
            )
        else:
            # Fallback to current time if no elevation crossings found
            normalized, previous_solstice, next_solstice = get_solstice_curve(
                self.hass, current_time, entry_id, config_data=config_data
            )
        
        # Cache the data
        _solstice_curve_cache[entry_id] = {
            'normalized': normalized,
            'previous_solstice': previous_solstice,
            'next_solstice': next_solstice,
            'calculated_at': current_time,
            'target_time': target_time,
            'entry_id': entry_id
        }
        
        # Emit event when cache updates
        self.hass.bus.async_fire(
            f"{DOMAIN}_solstice_curve_cache_updated",
            {
                "entry_id": entry_id,
                "normalized": normalized,
                "previous_solstice": previous_solstice,
                "next_solstice": next_solstice,
                "target_time": target_time
            }
        )
        
        return normalized, previous_solstice, next_solstice, target_time


class SolarEventsCache:
    """Manages caching for solar noon/midnight events."""
    
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._update_timers = {}  # Dict[entry_id, timer]
        self._initialized_entries = set()  # Track which entries have been initialized
    
    def _get_entry_cache(self, entry_id: str) -> Dict[str, Any]:
        """Get or create cache storage for a specific entry."""
        if entry_id not in _solar_events_cache_instances:
            _solar_events_cache_instances[entry_id] = {}
        return _solar_events_cache_instances[entry_id]
    
    def _clear_entry_cache(self, entry_id: str):
        """Clear cache for a specific entry."""
        if entry_id in _solar_events_cache_instances:
            del _solar_events_cache_instances[entry_id]
        
    async def initialize(self):
        """Initialize the cache system (but don't force cache creation)."""
        # Don't force cache creation here - let it happen lazily
        # Just ensure the system is ready to schedule updates when needed
        pass
    
    async def cleanup(self):
        """Clean up the cache and cancel all timers."""
        for entry_id, timer in self._update_timers.items():
            if timer:
                timer.cancel()
        self._update_timers.clear()
        self._initialized_entries.clear()
        
        # Clear all entry-specific caches
        _solar_events_cache_instances.clear()
    
    def _schedule_update_for_entry_async(self, entry_id: str):
        """Schedule update for an entry (non-blocking)."""
        # Use asyncio.create_task to avoid blocking the current call
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._schedule_update_for_entry(entry_id))
        except RuntimeError:
            # No event loop running, skip scheduling
            pass

    async def _schedule_update_for_entry(self, entry_id: str):
        """Schedule the next cache update for a specific entry."""
        # Cancel existing timer for this entry
        if entry_id in self._update_timers:
            self._update_timers[entry_id].cancel()
        
        try:
            # Get current cached event
            next_event_time, next_event_type, next_event_elevation, used_cache = self.get_cached_solar_event(
                entry_id, None
            )
            
            if next_event_time:
                # Schedule update a few milliseconds after the event
                update_time = next_event_time + datetime.timedelta(milliseconds=100)
                
                # Convert to local time for delay calculation
                local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
                now_local = dt_util.now(local_tz)
                update_time_local = update_time.astimezone(local_tz)
                
                # Calculate delay in seconds
                delay = (update_time_local - now_local).total_seconds()
                
                # Only schedule if the event is in the future
                if delay > 0:
                    _LOGGER.debug(f"Scheduling solar events cache update for {entry_id} in {delay:.2f} seconds at {update_time_local}")
                    
                    self._update_timers[entry_id] = self.hass.loop.call_later(
                        delay,
                        lambda entry_id=entry_id: self.hass.async_create_task(
                            self._perform_scheduled_update_for_entry(entry_id)
                        )
                    )
                else:
                    # Event is in the past, schedule immediate update
                    _LOGGER.warning(f"Next solar event for {entry_id} is in the past, scheduling immediate update")
                    self._update_timers[entry_id] = self.hass.loop.call_later(
                        0.1,  # 100ms delay
                        lambda entry_id=entry_id: self.hass.async_create_task(
                            self._perform_scheduled_update_for_entry(entry_id)
                        )
                    )
            else:
                # No events found, schedule a fallback update in 1 hour
                _LOGGER.warning(f"No solar events found for {entry_id}, scheduling fallback update in 1 hour")
                self._update_timers[entry_id] = self.hass.loop.call_later(
                    3600,  # 1 hour
                    lambda entry_id=entry_id: self.hass.async_create_task(
                        self._perform_scheduled_update_for_entry(entry_id)
                    )
                )
                
        except Exception as e:
            _LOGGER.error(f"Failed to schedule update for entry {entry_id}: {e}")
            # Schedule a retry in 5 minutes
            self._update_timers[entry_id] = self.hass.loop.call_later(
                300,  # 5 minutes
                lambda entry_id=entry_id: self.hass.async_create_task(
                    self._perform_scheduled_update_for_entry(entry_id)
                )
            )

    async def _perform_scheduled_update_for_entry(self, entry_id: str):
        """Perform the scheduled cache update for a specific entry and reschedule."""
        try:
            _LOGGER.debug(f"Performing scheduled solar events cache update for {entry_id}")
            
            # Force cache refresh by calling with current time
            local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
            current_time = dt_util.now(local_tz)
            
            # This will trigger cache refresh if needed
            self.get_cached_solar_event(entry_id, current_time)
            
            # Reschedule for this entry
            await self._schedule_update_for_entry(entry_id)
            
        except Exception as e:
            _LOGGER.error(f"Error during scheduled solar events cache update for {entry_id}: {e}")
            # Schedule a retry in 5 minutes
            self._update_timers[entry_id] = self.hass.loop.call_later(
                300,  # 5 minutes
                lambda entry_id=entry_id: self.hass.async_create_task(
                    self._perform_scheduled_update_for_entry(entry_id)
                )
            )

    async def add_entry(self, entry_id: str):
        """Add a new entry (will be scheduled when first accessed)."""
        # Entry will be automatically scheduled when get_cached_solar_event is first called
        pass

    async def remove_entry(self, entry_id: str):
        """Remove an entry and clean up its dedicated cache."""
        if entry_id in self._update_timers:
            self._update_timers[entry_id].cancel()
            del self._update_timers[entry_id]
        
        # Clear the entry's dedicated cache
        self._clear_entry_cache(entry_id)
        self._initialized_entries.discard(entry_id)
    
    def _calculate_next_solar_event(self, entry_id: str, current_time_local: datetime.datetime) -> Tuple[Optional[datetime.datetime], Optional[str], Optional[float]]:
        """
        Calculate the next solar event (noon/midnight) and its elevation.
        
        Args:
            entry_id: Config entry ID
            current_time_local: Current time in local timezone
            
        Returns:
            Tuple (next_event_time_utc, next_event_type, next_event_elevation)
        """
        from .utils import get_sun_position
        from .config_store import get_config_entry_data
        
        # Get config data
        config_data = get_config_entry_data(entry_id)
        
        # Get current sun position data to find next solar events
        sun_data = get_sun_position(self.hass, current_time_local, entry_id, config_data=config_data)
        
        solar_noon_dt = sun_data.get('solar_noon')
        solar_midnight_dt = sun_data.get('solar_midnight')
        
        # Determine which event is next
        next_event_time = None
        next_event_type = None
        
        if solar_noon_dt and solar_midnight_dt:
            if solar_noon_dt < solar_midnight_dt:
                next_event_time = solar_noon_dt
                next_event_type = 'noon'
            else:
                next_event_time = solar_midnight_dt
                next_event_type = 'midnight'
        elif solar_noon_dt:
            next_event_time = solar_noon_dt
            next_event_type = 'noon'
        elif solar_midnight_dt:
            next_event_time = solar_midnight_dt
            next_event_type = 'midnight'
        
        # Calculate the exact elevation at the solar event time
        next_event_elevation = None
        if next_event_time:
            try:
                # Convert UTC time back to local time for get_sun_position
                local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
                next_event_local = next_event_time.astimezone(local_tz)
                
                # Get sun position at the event time
                event_sun_data = get_sun_position(self.hass, next_event_local, entry_id, config_data=config_data)
                next_event_elevation = event_sun_data.get('elevation')
            except Exception:
                next_event_elevation = None
        
        return next_event_time, next_event_type, next_event_elevation
    
    
    def get_cached_solar_event(
        self, 
        entry_id: str,
        current_time: Optional[datetime.datetime] = None
    ) -> Tuple[Optional[datetime.datetime], Optional[str], Optional[float], bool]:
        """
        Get cached solar event data or calculate and cache if needed.
        
        Args:
            entry_id: Config entry ID for location-specific calculations
            current_time: Current time (defaults to now in local timezone)
            
        Returns:
            Tuple (next_event_time, next_event_type, next_event_elevation, used_cache)
        """
        if current_time is None:
            local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
            current_time = dt_util.now(local_tz)
        
        # Convert current_time to UTC for comparison
        if current_time.tzinfo is None:
            # If naive, assume it's in local timezone
            local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
            current_time = current_time.replace(tzinfo=local_tz)
        
        current_time_utc = current_time.astimezone(timezone.utc)
        
        # Get entry-specific cache
        entry_cache = self._get_entry_cache(entry_id)
        
        # Check if we have valid cached data for this entry
        if entry_cache and 'calculated_at' in entry_cache:
            # Check if cache is still valid (within 1 hour of calculation)
            calculated_at = entry_cache.get('calculated_at')
            next_event_time = entry_cache.get('next_event_time')
            
            if calculated_at and next_event_time:
                # Check if the cached event time is in the past
                if next_event_time <= current_time_utc:
                    _LOGGER.debug(f"Cached solar event for {entry_id} is in the past, refreshing cache")
                    # Event is in the past, force refresh
                    result = self._initialize_cache(entry_id, current_time)
                    # Schedule next update after refresh
                    self._schedule_update_for_entry_async(entry_id)
                    return result
                
                # Check if cache is still fresh (within 1 hour of calculation)
                time_diff = abs((current_time_utc - calculated_at).total_seconds())
                if time_diff < 3600:  # 1 hour
                    return (
                        entry_cache['next_event_time'],  # This is in UTC
                        entry_cache['next_event_type'],
                        entry_cache['next_event_elevation'],
                        True  # Used cache
                    )
        
        # No valid cache found - calculate and cache on-demand (lazy loading)
        result = self._initialize_cache(entry_id, current_time)
        
        # Schedule next update after first cache creation
        if entry_id not in self._initialized_entries:
            self._initialized_entries.add(entry_id)
            self._schedule_update_for_entry_async(entry_id)
        
        return result
    
    def _initialize_cache(
        self, 
        entry_id: str, 
        current_time: datetime.datetime
    ) -> Tuple[Optional[datetime.datetime], Optional[str], Optional[float], bool]:
        """
        Initialize cache for a specific entry.
        
        Args:
            entry_id: Config entry ID
            current_time: Current datetime (in local timezone)
            
        Returns:
            Tuple (next_event_time, next_event_type, next_event_elevation, used_cache)
        """
        # Convert current_time to UTC if it's not already
        if current_time.tzinfo is None:
            # If naive, assume it's in local timezone
            local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
            current_time = current_time.replace(tzinfo=local_tz)
        
        current_time_utc = current_time.astimezone(timezone.utc)
        
        # Always calculate fresh data when cache is missing (lazy loading)
        # This ensures config data is available when calculation happens
        next_event_time, next_event_type, next_event_elevation = self._calculate_next_solar_event(entry_id, current_time)
        
        # Get config data to include location info in cache
        from .config_store import get_config_entry_data
        config_data = get_config_entry_data(entry_id)
        cache_latitude = config_data.get("latitude", self.hass.config.latitude) if config_data else self.hass.config.latitude
        cache_longitude = config_data.get("longitude", self.hass.config.longitude) if config_data else self.hass.config.longitude
        
        # Debug: store the actual config_data to see what's being retrieved
        debug_config_data = dict(config_data) if config_data else {}
        
        # Get entry-specific cache and store data
        entry_cache = self._get_entry_cache(entry_id)
        
        if next_event_time:
            entry_cache.update({
                'next_event_time': next_event_time,  # UTC time from get_sun_position
                'next_event_type': next_event_type,
                'next_event_elevation': next_event_elevation,  # NEW: elevation at event time
                'calculated_at': current_time_utc,   # UTC time for cache validation
                'entry_id': entry_id,
                'debug_calculated_at_tz': str(current_time_utc.tzinfo) if current_time_utc.tzinfo else 'None',
                'debug_cache_latitude': cache_latitude,
                'debug_cache_longitude': cache_longitude,
                'debug_config_data': debug_config_data
            })
            
            # Emit event when cache updates
            self.hass.bus.async_fire(
                f"{DOMAIN}_solar_events_cache_updated",
                {
                    "entry_id": entry_id,
                    "next_event_time": next_event_time,
                    "next_event_type": next_event_type,
                    "next_event_elevation": next_event_elevation
                }
            )
        
        return next_event_time, next_event_type, next_event_elevation, False  # Did not use cache


# Global cache instance
_cache_instance: Optional[SolsticeCurveCache] = None

def get_cache_instance(hass: HomeAssistant) -> SolsticeCurveCache:
    """Get the global cache instance."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = SolsticeCurveCache(hass)
    return _cache_instance

# Global solar events cache instance
_solar_events_cache_instance: Optional[SolarEventsCache] = None

def get_solar_events_cache_instance(hass: HomeAssistant) -> SolarEventsCache:
    """Get the global solar events cache instance."""
    global _solar_events_cache_instance
    if _solar_events_cache_instance is None:
        _solar_events_cache_instance = SolarEventsCache(hass)
    return _solar_events_cache_instance

# Convenience function for backward compatibility
def get_cached_solstice_curve(
    hass: HomeAssistant,
    entry_id: str,
    current_time: Optional[datetime.datetime] = None
) -> Tuple[float, datetime.datetime, datetime.datetime, Optional[datetime.datetime]]:
    """
    Convenience function to get cached solstice curve data.
    
    Args:
        hass: Home Assistant instance
        entry_id: Config entry ID
        current_time: Current time (optional)
        
    Returns:
        Tuple (normalized_value, previous_solstice, next_solstice, target_time)
    """
    cache = get_cache_instance(hass)
    return cache.get_cached_solstice_curve(entry_id, current_time)

# Convenience function for solar events cache
def get_cached_solar_event(
    hass: HomeAssistant,
    entry_id: str,
    current_time: Optional[datetime.datetime] = None
) -> Tuple[Optional[datetime.datetime], Optional[str], Optional[float], bool]:
    """
    Convenience function to get cached solar event data.
    
    Args:
        hass: Home Assistant instance
        entry_id: Config entry ID
        current_time: Current time (optional)
        
    Returns:
        Tuple (next_event_time, next_event_type, next_event_elevation, used_cache)
    """
    cache = get_solar_events_cache_instance(hass)
    return cache.get_cached_solar_event(entry_id, current_time) 