"""Declination cache manager for Solar Declination Normalized sensor."""

from __future__ import annotations

import datetime
import logging
import zoneinfo
from datetime import timezone, timedelta
from typing import Dict, Any, Tuple, Optional

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .utils import get_declination_normalized
from .body_observer import BodyObserver
from .config_store import get_config_entry_data
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Module-level cache for declination data
_declination_cache: Dict[str, Dict[str, Any]] = {}


class DeclinationCacheManager:
    """Manages caching for declination normalized calculations."""
    
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._update_timer = None
        self._initialized = False
        self._observers = {}  # Dict[entry_id, BodyObserver] - reuse one observer per entry (sun only)
        
    async def initialize(self):
        """Initialize the cache and start the update timer."""
        if self._initialized:
            return
            
        # DO NOT update caches during initialization - config entries haven't been loaded yet
        # The cache will be updated when:
        # 1. An entry explicitly calls update_entry() after setup
        # 2. The scheduled timer fires (noon/midnight)
        
        # Start the timer for automatic updates
        await self._schedule_next_update()
        
        self._initialized = True
    
    async def update_entry(self, entry_id: str):
        """Update cache for a specific entry (called after entry setup)."""
        try:
            await self._update_cache_for_entry(entry_id)
        except Exception as e:
            _LOGGER.error(f"Error updating declination cache for entry {entry_id}: {e}", exc_info=True)
    
    async def cleanup(self):
        """Clean up the cache and cancel timer."""
        if self._update_timer:
            self._update_timer.cancel()
            self._update_timer = None
        self._initialized = False
        
        # Clear cache and reused observers
        _declination_cache.clear()
        self._observers.clear()
    
    def remove_entry(self, entry_id: str) -> None:
        """Remove the reused observer for an entry when it is unloaded."""
        self._observers.pop(entry_id, None)
    
    async def _schedule_next_update(self):
        """Schedule the next cache update at noon or midnight."""
        if self._update_timer:
            self._update_timer.cancel()
        
        local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
        now = dt_util.now(local_tz)
        
        # Determine if we should schedule for noon or midnight based on current time
        today = now.date()
        if now.hour < 12:  # Before noon
            next_update = datetime.datetime.combine(today, datetime.time(12, 0, 0), tzinfo=local_tz)
        else:  # After noon (including exactly at noon)
            next_update = datetime.datetime.combine(today + timedelta(days=1), datetime.time(0, 0, 0), tzinfo=local_tz)
        
        # Calculate delay in seconds
        delay = (next_update - now).total_seconds()
        
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
                _LOGGER.error(f"Error updating declination cache for entry {entry.entry_id}: {e}", exc_info=True)
    
    async def _update_cache_for_entry(self, entry_id: str):
        """Update cache for a specific entry."""
        local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
        current_time = dt_util.now(local_tz)
        
        # Get config data
        config_data = get_config_entry_data(entry_id)
        if not config_data:
            return
        
        # Check if declination normalized sensor is enabled for this entry
        enable_declination_normalized = config_data.get("enable_declination_normalized", False)
        if not enable_declination_normalized:
            return
        
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
        today_end = today_midnight + timedelta(days=1)
        
        # Get sun body
        from .utils import get_body
        from .const import eph
        sun = get_body("sun", eph)
        if sun is None:
            return
        
        # Get or create reused observer for this entry
        if entry_id not in self._observers:
            self._observers[entry_id] = BodyObserver(
                entry_id=entry_id,
                body=sun,
                search_start=today_midnight,
                search_end=today_end,
                hass=self.hass
            )
        else:
            self._observers[entry_id].set_search_window(search_start=today_midnight, search_end=today_end)
        observer = self._observers[entry_id]
        
        # Get today's rising (0° elevation, direction +1)
        next_rising = observer.get_time_at_elevation(
            target_elevation=0.0,
            direction=1,
            search_end=today_end,
            use_centre=True
        )
        
        # Get today's setting (0° elevation, direction -1)
        next_setting = observer.get_time_at_elevation(
            target_elevation=0.0,
            direction=-1,
            search_end=today_end,
            use_centre=True
        )
        
        # Determine target time based on current time
        if current_time < local_noon:  # AM - use next rising
            if isinstance(next_rising, datetime.datetime):
                target_time = next_rising
            else:
                target_time = current_time
        else:  # PM - use next setting
            if isinstance(next_setting, datetime.datetime):
                target_time = next_setting
            else:
                target_time = current_time
        
        # Get cached solstices if available
        cached_solstices = None
        if entry_id in _declination_cache:
            cached_data = _declination_cache[entry_id]
            cached_solstices = {
                'june_solstice': cached_data.get('june_solstice'),
                'december_solstice': cached_data.get('december_solstice'),
                'next_solstice': cached_data.get('next_solstice'),
                'previous_solstice': cached_data.get('previous_solstice'),
            }
            
            # Check if we need to recalculate solstices (if we've passed the next solstice)
            next_solstice = cached_data.get('next_solstice')
            if next_solstice:
                target_time_utc = target_time.astimezone(timezone.utc)
                next_solstice_utc = next_solstice
                if next_solstice_utc.tzinfo is None:
                    next_solstice_utc = next_solstice_utc.replace(tzinfo=timezone.utc)
                elif next_solstice_utc.tzinfo != timezone.utc:
                    next_solstice_utc = next_solstice_utc.astimezone(timezone.utc)
                
                # If we've passed the next solstice, clear cached solstices to force recalculation
                if target_time_utc >= next_solstice_utc:
                    cached_solstices = None
        
        # Calculate normalized declination
        try:
            normalized, previous_solstice, next_solstice, june_solstice, december_solstice = get_declination_normalized(
                target_time=target_time,
                entry_id=entry_id,
                config_data=config_data,
                cached_solstices=cached_solstices
            )
        except Exception as e:
            _LOGGER.error(f"Error calculating declination normalized for entry {entry_id}: {e}", exc_info=True)
            return
        
        # Update cache
        _declination_cache[entry_id] = {
            'normalized': normalized,
            'previous_solstice': previous_solstice,
            'next_solstice': next_solstice,
            'june_solstice': june_solstice,
            'december_solstice': december_solstice,
            'calculated_at': current_time,
            'target_time': target_time,
            'entry_id': entry_id
        }
        
        # Emit event when cache updates
        self.hass.bus.async_fire(
            f"{DOMAIN}_declination_cache_updated",
            {
                "entry_id": entry_id,
                "normalized": normalized,
                "previous_solstice": previous_solstice,
                "next_solstice": next_solstice,
                "target_time": target_time
            }
        )
    
    def get_cached_declination(
        self, 
        entry_id: str,
        current_time: Optional[datetime.datetime] = None
    ) -> Tuple[float, datetime.datetime, datetime.datetime, Optional[datetime.datetime]]:
        """
        Get cached declination data or calculate and cache if needed.
        
        Args:
            entry_id: Config entry ID for location-specific calculations
            current_time: Current time (defaults to now in local timezone)
            
        Returns:
            Tuple (normalized_value, previous_solstice, next_solstice, target_time)
        """
        if current_time is None:
            local_tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
            current_time = dt_util.now(local_tz)
        
        # Check if we have valid cached data
        if entry_id in _declination_cache:
            cached_data = _declination_cache[entry_id]
            
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
        
        # No valid cache - return None values (caller should trigger update)
        return None, None, None, None


# Global cache instance
_cache_instance: Optional[DeclinationCacheManager] = None

def get_declination_cache_instance(hass: HomeAssistant) -> DeclinationCacheManager:
    """Get the global declination cache instance."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = DeclinationCacheManager(hass)
    return _cache_instance

def get_cached_declination_normalized(
    hass: HomeAssistant,
    entry_id: str,
    current_time: Optional[datetime.datetime] = None
) -> Tuple[Optional[float], Optional[datetime.datetime], Optional[datetime.datetime], Optional[datetime.datetime]]:
    """
    Convenience function to get cached declination normalized data.
    
    Args:
        hass: Home Assistant instance
        entry_id: Config entry ID
        current_time: Current time (optional)
        
    Returns:
        Tuple (normalized_value, previous_solstice, next_solstice, target_time)
    """
    cache = get_declination_cache_instance(hass)
    return cache.get_cached_declination(entry_id, current_time)

