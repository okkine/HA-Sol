"""Azimuth checkpoint cache manager (reversals + solar noon checkpoints)."""

from __future__ import annotations

import datetime
import logging
import math
import zoneinfo
from datetime import timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import ephem

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    AZIMUTH_REVERSAL_CACHE_LENGTH,
    AZIMUTH_REVERSAL_SEARCH_MAX_ITERATIONS,
    AZIMUTH_DEGREE_TOLERANCE,
    TROPICAL_LATITUDE_THRESHOLD,
)
from .reversal_store import load_reversal_cache, save_reversal_cache, remove_reversal_cache
from .utils import get_sun_position, calculate_azimuth_derivative
from .config_store import get_config_entry_data

_LOGGER = logging.getLogger(__name__)


class ReversalCacheManager:
    """Manages azimuth checkpoint cache (reversals + solar noon) with persistent storage."""
    
    def __init__(self, hass: HomeAssistant):
        """Initialize the checkpoint cache manager."""
        self.hass = hass
        self._maintenance_timers = {}  # Dict[entry_id, timer]
        self._initialized_entries = set()
    
    async def initialize_entry(self, entry_id: str) -> None:
        """
        Initialize checkpoint cache for an entry.
        
        Args:
            entry_id: Config entry ID
        """
        if entry_id in self._initialized_entries:
            _LOGGER.debug(f"Entry {entry_id} already initialized")
            return
        
        try:
            # Try to load existing cache
            cache = await load_reversal_cache(self.hass, entry_id)
            
            # Check for old cache format and force reinitialization
            if cache and 'reversals' in cache and 'checkpoints' not in cache:
                _LOGGER.info(f"Migrating old cache format to checkpoint system for {entry_id}")
                cache = None
            
            if cache is None:
                # No cache exists, initialize from scratch
                _LOGGER.info(f"Initializing new checkpoint cache for entry {entry_id}")
                cache = await self._initialize_from_solar_noon(entry_id)
            else:
                # Cache exists, validate and clean up
                _LOGGER.info(f"Loaded existing checkpoint cache for entry {entry_id}")
                cache = await self._validate_and_cleanup_cache(entry_id, cache)
            
            # Schedule maintenance
            await self._schedule_next_maintenance(entry_id)
            
            self._initialized_entries.add(entry_id)
            
            reversal_count = sum(1 for cp in cache['checkpoints'] if cp['is_reversal'])
            _LOGGER.info(
                f"Checkpoint cache initialized for {entry_id}: "
                f"{len(cache['checkpoints'])} checkpoints ({reversal_count} reversals)"
            )
            
        except Exception as e:
            _LOGGER.error(f"Error initializing checkpoint cache for entry {entry_id}: {e}", exc_info=True)
    
    async def remove_entry(self, entry_id: str) -> None:
        """
        Remove checkpoint cache for an entry.
        
        Args:
            entry_id: Config entry ID
        """
        # Cancel maintenance timer
        if entry_id in self._maintenance_timers:
            self._maintenance_timers[entry_id].cancel()
            del self._maintenance_timers[entry_id]
        
        # Remove from initialized set
        self._initialized_entries.discard(entry_id)
        
        # Remove persistent storage
        await remove_reversal_cache(self.hass, entry_id)
        
        _LOGGER.info(f"Removed checkpoint cache for entry {entry_id}")
    
    async def get_reversals(self, entry_id: str, current_time: Optional[datetime.datetime] = None) -> Dict[str, Any]:
        """
        Get checkpoint cache for an entry.
        
        Args:
            entry_id: Config entry ID
            current_time: Current time (defaults to now)
            
        Returns:
            Cache dictionary with last_known_state and checkpoints
        """
        if current_time is None:
            current_time = dt_util.now()
        
        # Ensure initialized
        if entry_id not in self._initialized_entries:
            await self.initialize_entry(entry_id)
        
        # Load cache
        cache = await load_reversal_cache(self.hass, entry_id)
        
        if cache is None:
            # Something went wrong, reinitialize
            _LOGGER.warning(f"Cache missing for initialized entry {entry_id}, reinitializing")
            cache = await self._initialize_from_solar_noon(entry_id)
        
        return cache
    
    def get_current_direction(self, cache: Dict[str, Any], current_time: datetime.datetime) -> int:
        """
        Calculate current azimuth direction from cache.
        
        Args:
            cache: Cache dictionary
            current_time: Current time
            
        Returns:
            Current direction (1 or -1)
        """
        direction = cache['last_known_state']['direction']
        last_known_time = cache['last_known_state']['time']
        
        # Handle both old and new cache formats during migration
        if 'checkpoints' in cache:
            # New checkpoint format - only flip direction for actual reversals
            for checkpoint in cache['checkpoints']:
                if last_known_time < checkpoint['time'] <= current_time and checkpoint.get('is_reversal', False):
                    direction *= -1
        elif 'reversals' in cache:
            # Old format - all entries are reversals
            for reversal in cache['reversals']:
                if last_known_time < reversal['time'] <= current_time:
                    direction *= -1
        
        return direction
    
    async def _initialize_from_solar_noon(self, entry_id: str) -> Dict[str, Any]:
        """
        Initialize cache from previous solar noon.
        
        Args:
            entry_id: Config entry ID
            
        Returns:
            Initialized cache dictionary
        """
        config_data = get_config_entry_data(entry_id)
        now = dt_util.now()
        now_utc = now.astimezone(timezone.utc)
        
        # Get previous solar noon
        observer = ephem.Observer()
        observer.lat = str(config_data.get('latitude', self.hass.config.latitude))
        observer.lon = str(config_data.get('longitude', self.hass.config.longitude))
        observer.elevation = config_data.get('elevation', self.hass.config.elevation)
        observer.date = now_utc
        
        sun = ephem.Sun()
        prev_solar_noon = observer.previous_transit(sun)
        prev_solar_noon_dt = prev_solar_noon.datetime().replace(tzinfo=timezone.utc)
        
        _LOGGER.debug(f"Initializing from previous solar noon: {prev_solar_noon_dt}")
        
        # Sample direction at previous solar noon
        noon_minus_10 = prev_solar_noon_dt - timedelta(minutes=10)
        noon_plus_10 = prev_solar_noon_dt + timedelta(minutes=10)
        
        az_before = get_sun_position(self.hass, noon_minus_10, entry_id, config_data=config_data)['azimuth']
        az_after = get_sun_position(self.hass, noon_plus_10, entry_id, config_data=config_data)['azimuth']
        noon_azimuth = get_sun_position(self.hass, prev_solar_noon_dt, entry_id, config_data=config_data)['azimuth']
        
        derivative = calculate_azimuth_derivative(az_before, az_after)
        direction_at_noon = 1 if derivative > 0 else -1
        
        _LOGGER.debug(f"Direction at solar noon: {direction_at_noon}")
        
        # Scan from previous noon forward, searching only for future checkpoints
        # Use 30-day max search window as safety limit
        search_end = now_utc + timedelta(days=30)
        
        # Keep track of all checkpoints found (for last_known_state)
        all_passed = []
        all_future = []
        current_search_start = prev_solar_noon_dt
        current_search_direction = direction_at_noon
        
        # Keep searching until we have enough future checkpoints
        while len(all_future) < AZIMUTH_REVERSAL_CACHE_LENGTH and current_search_start < search_end:
            # Search for next checkpoint
            checkpoints = await self._scan_for_checkpoints(
                entry_id,
                start_time=current_search_start,
                end_time=search_end,
                start_direction=current_search_direction,
                config_data=config_data,
                target_count=1  # Find one checkpoint at a time
            )
            
            if not checkpoints:
                # No more checkpoints found
                _LOGGER.warning(f"Could not find enough future checkpoints, only found {len(all_future)}")
                break
            
            checkpoint = checkpoints[0]
            
            # Check if this checkpoint is in the future
            if checkpoint['time'] > now_utc:
                all_future.append(checkpoint)
            else:
                all_passed.append(checkpoint)
            
            # Move search forward from this checkpoint
            current_search_start = checkpoint['time']
            current_search_direction = checkpoint['direction']
        
        passed = all_passed
        future = all_future
        
        reversal_count = sum(1 for cp in passed + future if cp['is_reversal'])
        _LOGGER.debug(f"Found {len(passed)} passed and {len(future)} future checkpoints ({reversal_count} reversals)")
        
        # Calculate current direction (only flip for reversals)
        current_direction = direction_at_noon
        for cp in passed:
            if cp['is_reversal']:
                current_direction *= -1
        
        # Set last_known_state
        if passed:
            last_checkpoint = passed[-1]
            last_known_time = last_checkpoint['time']
            last_known_azimuth = last_checkpoint['azimuth']
            last_known_direction = last_checkpoint['direction']
        else:
            last_known_time = prev_solar_noon_dt
            last_known_azimuth = noon_azimuth
            last_known_direction = direction_at_noon
        
        # Store all future checkpoints
        cache = {
            'last_known_state': {
                'time': last_known_time,
                'direction': last_known_direction,
                'azimuth': last_known_azimuth
            },
            'checkpoints': future,
            'location': {
                'latitude': config_data.get('latitude', self.hass.config.latitude),
                'longitude': config_data.get('longitude', self.hass.config.longitude)
            }
        }
        
        _LOGGER.info(
            f"Initialized cache for {entry_id}: "
            f"{len(cache['checkpoints'])} checkpoints, "
            f"last_known_state at {last_known_time.isoformat()}"
        )
        
        await save_reversal_cache(self.hass, entry_id, cache)
        
        return cache
    
    async def _validate_and_cleanup_cache(
        self, 
        entry_id: str, 
        cache: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate and clean up loaded cache.
        
        Args:
            entry_id: Config entry ID
            cache: Loaded cache dictionary
            
        Returns:
            Validated and cleaned cache
        """
        config_data = get_config_entry_data(entry_id)
        now_utc = dt_util.now().astimezone(timezone.utc)
        
        # Check for invalid cache structure - reinitialize if needed
        if 'checkpoints' not in cache or 'last_known_state' not in cache:
            _LOGGER.warning(f"Invalid cache structure for {entry_id}, reinitializing")
            return await self._initialize_from_solar_noon(entry_id)
        
        # Check if location changed
        cached_lat = cache.get('location', {}).get('latitude')
        cached_lon = cache.get('location', {}).get('longitude')
        current_lat = config_data.get('latitude', self.hass.config.latitude)
        current_lon = config_data.get('longitude', self.hass.config.longitude)
        
        if cached_lat != current_lat or cached_lon != current_lon:
            _LOGGER.info(f"Location changed for {entry_id}, reinitializing cache")
            return await self._initialize_from_solar_noon(entry_id)
        
        # Remove past checkpoints and update last_known_state
        passed_checkpoints = [cp for cp in cache['checkpoints'] if cp['time'] <= now_utc]
        
        if passed_checkpoints:
            _LOGGER.debug(f"Removing {len(passed_checkpoints)} passed checkpoints")
            
            # Update last_known_state from last passed checkpoint
            last_checkpoint = passed_checkpoints[-1]
            cache['last_known_state'] = {
                'time': last_checkpoint['time'],
                'direction': last_checkpoint['direction'],
                'azimuth': last_checkpoint['azimuth']
            }
            
            # Remove passed checkpoints
            cache['checkpoints'] = [cp for cp in cache['checkpoints'] if cp['time'] > now_utc]
        
        # Ensure we have checkpoints covering the search window
        cache = await self._refill_checkpoints(entry_id, cache, config_data)
        
        await save_reversal_cache(self.hass, entry_id, cache)
        
        return cache
    
    async def _refill_checkpoints(
        self,
        entry_id: str,
        cache: Dict[str, Any],
        config_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculate additional checkpoints to maintain exactly AZIMUTH_REVERSAL_CACHE_LENGTH checkpoints.
        
        Args:
            entry_id: Config entry ID
            cache: Current cache
            config_data: Config data
            
        Returns:
            Updated cache
        """
        # Only refill if we have fewer than the target number of checkpoints
        if len(cache['checkpoints']) >= AZIMUTH_REVERSAL_CACHE_LENGTH:
            return cache
        
        # Determine starting point for new search
        if cache['checkpoints']:
            search_start = cache['checkpoints'][-1]['time']
            start_direction = cache['checkpoints'][-1]['direction']
        else:
            search_start = cache['last_known_state']['time']
            start_direction = cache['last_known_state']['direction']
        
        # Search forward until we have enough checkpoints
        # Maximum search window to prevent infinite loops (30 days)
        now_utc = dt_util.now().astimezone(timezone.utc)
        search_end = now_utc + timedelta(days=30)
        
        new_checkpoints = await self._scan_for_checkpoints(
            entry_id,
            start_time=search_start,
            end_time=search_end,
            start_direction=start_direction,
            config_data=config_data,
            target_count=AZIMUTH_REVERSAL_CACHE_LENGTH - len(cache['checkpoints'])
        )
        
        # Add new checkpoints to cache (keep all, sorted chronologically)
        cache['checkpoints'].extend(new_checkpoints)
        cache['checkpoints'].sort(key=lambda cp: cp['time'])
        
        return cache
    
    async def _scan_for_checkpoints(
        self,
        entry_id: str,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        start_direction: int,
        config_data: Dict[str, Any],
        target_count: int = None
    ) -> List[Dict[str, Any]]:
        """
        Scan for azimuth checkpoints (reversals + solar noons) until target count reached.
        
        Finds whichever comes first chronologically: reversal or solar noon.
        
        Args:
            entry_id: Config entry ID
            start_time: Start of scan window
            end_time: End of scan window (safety limit)
            start_direction: Known direction at start_time
            config_data: Config data
            target_count: Stop after finding this many checkpoints (None = no limit)
            
        Returns:
            List of checkpoint dictionaries (chronologically sorted)
        """
        checkpoints = []
        current_direction = start_direction
        search_time = start_time
        
        while search_time < end_time:
            # Check if we've reached target count
            if target_count is not None and len(checkpoints) >= target_count:
                break
            
            # Find next solar noon after search_time
            next_solar_noon = await self._calculate_solar_noon(
                search_time + timedelta(minutes=1),
                entry_id,
                config_data
            )
            
            # Find next reversal by scanning forward
            next_reversal_time = None
            next_reversal_azimuth = None
            scan_time = search_time + timedelta(minutes=5)
            
            # Scan up to next solar noon (or end_time if no solar noon)
            scan_limit = min(next_solar_noon if next_solar_noon else end_time, end_time)
            
            while scan_time < scan_limit:
                # Sample azimuth change over 10-minute window
                az_before = get_sun_position(self.hass, scan_time, entry_id, config_data=config_data)['azimuth']
                az_after = get_sun_position(
                    self.hass,
                    scan_time + timedelta(minutes=10),
                    entry_id,
                    config_data=config_data
                )['azimuth']
                
                derivative = calculate_azimuth_derivative(az_before, az_after)
                observed_direction = 1 if derivative > 0 else -1
                
                # Check for direction change (reversal)
                if observed_direction != current_direction:
                    try:
                        # Binary search to find exact reversal
                        reversal_time, reversal_azimuth = await self._binary_search_reversal(
                            scan_time - timedelta(minutes=5),
                            scan_time + timedelta(minutes=10),
                            entry_id,
                            config_data
                        )
                        
                        next_reversal_time = reversal_time
                        next_reversal_azimuth = reversal_azimuth
                        break  # Found reversal, stop scanning
                        
                    except Exception as e:
                        _LOGGER.warning(f"Error finding reversal: {e}")
                
                # Advance scan window
                scan_time += timedelta(minutes=30)
            
            # Determine which comes first: reversal or solar noon
            if next_reversal_time and (not next_solar_noon or next_reversal_time < next_solar_noon):
                # Reversal comes first
                current_direction *= -1  # Flip direction
                
                checkpoints.append({
                    'time': next_reversal_time,
                    'azimuth': next_reversal_azimuth,
                    'direction': current_direction,
                    'is_reversal': True
                })
                
                search_time = next_reversal_time
                
            elif next_solar_noon:
                # Solar noon comes first (or no reversal found)
                solar_noon_azimuth = get_sun_position(
                    self.hass, 
                    next_solar_noon, 
                    entry_id, 
                    config_data=config_data
                )['azimuth']
                
                checkpoints.append({
                    'time': next_solar_noon,
                    'azimuth': solar_noon_azimuth,
                    'direction': current_direction,  # Direction doesn't change
                    'is_reversal': False
                })
                
                search_time = next_solar_noon
                
            else:
                # No reversal or solar noon found - shouldn't happen, but safety break
                _LOGGER.warning(f"No checkpoint found between {search_time} and {end_time}")
                break
        
        # Sort chronologically
        checkpoints.sort(key=lambda cp: cp['time'])
        
        reversal_count = sum(1 for cp in checkpoints if cp['is_reversal'])
        _LOGGER.debug(
            f"Found {len(checkpoints)} checkpoints ({reversal_count} reversals, "
            f"{len(checkpoints) - reversal_count} solar noons) for entry {entry_id}"
        )
        
        return checkpoints
    
    async def _calculate_solar_noon(
        self,
        after_time: datetime.datetime,
        entry_id: str,
        config_data: Dict[str, Any]
    ) -> Optional[datetime.datetime]:
        """
        Calculate next solar noon after given time.
        
        Args:
            after_time: Find solar noon after this time
            entry_id: Config entry ID
            config_data: Config data
            
        Returns:
            Next solar noon datetime (UTC), or None if error
        """
        try:
            observer = ephem.Observer()
            observer.lat = str(config_data.get('latitude', self.hass.config.latitude))
            observer.lon = str(config_data.get('longitude', self.hass.config.longitude))
            observer.elevation = config_data.get('elevation', self.hass.config.elevation)
            observer.date = after_time
            
            sun = ephem.Sun()
            next_transit = observer.next_transit(sun)
            return next_transit.datetime().replace(tzinfo=timezone.utc)
            
        except Exception as e:
            _LOGGER.warning(f"Error calculating solar noon: {e}")
            return None
    
    async def _binary_search_reversal(
        self,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        entry_id: str,
        config_data: Dict[str, Any]
    ) -> Tuple[datetime.datetime, float]:
        """
        Binary search to find exact reversal point.
        
        Args:
            start_time: Start of search window
            end_time: End of search window
            entry_id: Config entry ID
            config_data: Config data
            
        Returns:
            Tuple of (reversal_time, reversal_azimuth)
        """
        left, right = start_time, end_time
        iterations = 0
        
        while iterations < AZIMUTH_REVERSAL_SEARCH_MAX_ITERATIONS:
            # Check azimuth difference across current window
            left_azimuth = get_sun_position(self.hass, left, entry_id, config_data=config_data)['azimuth']
            right_azimuth = get_sun_position(self.hass, right, entry_id, config_data=config_data)['azimuth']
            azimuth_diff = abs(right_azimuth - left_azimuth)
            if azimuth_diff > 180:  # Handle wraparound
                azimuth_diff = 360 - azimuth_diff
            
            # Stop if azimuth difference is small enough
            if azimuth_diff <= AZIMUTH_DEGREE_TOLERANCE:
                break
            
            iterations += 1
            mid = left + (right - left) / 2
            
            # Check if reversal is in left or right half
            left_mid = left + (mid - left) / 2
            right_mid = mid + (right - mid) / 2
            
            # Get azimuth values
            left_az = get_sun_position(self.hass, left, entry_id, config_data=config_data)['azimuth']
            left_mid_az = get_sun_position(self.hass, left_mid, entry_id, config_data=config_data)['azimuth']
            right_mid_az = get_sun_position(self.hass, right_mid, entry_id, config_data=config_data)['azimuth']
            right_az = get_sun_position(self.hass, right, entry_id, config_data=config_data)['azimuth']
            
            # Calculate derivatives
            left_rate = calculate_azimuth_derivative(left_az, left_mid_az)
            right_rate = calculate_azimuth_derivative(right_mid_az, right_az)
            
            # Check which half contains the reversal
            if left_rate * right_rate < 0:
                # Reversal is in the right half
                left = left_mid
            else:
                # Reversal is in the left half
                right = right_mid
        
        # Return the precise time and azimuth
        final_time = left + (right - left) / 2
        final_azimuth = get_sun_position(self.hass, final_time, entry_id, config_data=config_data)['azimuth']
        
        return final_time, final_azimuth
    
    async def _schedule_next_maintenance(self, entry_id: str) -> None:
        """Schedule maintenance for after next checkpoint."""
        # Cancel existing timer
        if entry_id in self._maintenance_timers:
            self._maintenance_timers[entry_id].cancel()
        
        try:
            cache = await load_reversal_cache(self.hass, entry_id)
            
            if cache and cache.get('checkpoints'):
                # Schedule for 100ms after first checkpoint
                next_checkpoint_time = cache['checkpoints'][0]['time']
                maintenance_time = next_checkpoint_time + timedelta(milliseconds=100)
                
                now = dt_util.now().astimezone(timezone.utc)
                delay = (maintenance_time - now).total_seconds()
                
                if delay > 0:
                    self._maintenance_timers[entry_id] = self.hass.loop.call_later(
                        delay,
                        lambda: self.hass.async_create_task(
                            self._perform_maintenance(entry_id)
                        )
                    )
                    _LOGGER.debug(
                        f"Scheduled maintenance for {entry_id} in {delay/3600:.2f} hours "
                        f"at {maintenance_time}"
                    )
                else:
                    # Checkpoint already passed, run maintenance immediately
                    await self._perform_maintenance(entry_id)
            else:
                _LOGGER.debug(f"No checkpoints in cache for {entry_id}, skipping maintenance scheduling")
        
        except Exception as e:
            _LOGGER.error(f"Error scheduling maintenance for {entry_id}: {e}", exc_info=True)
    
    async def _perform_maintenance(self, entry_id: str) -> None:
        """Perform scheduled maintenance."""
        try:
            _LOGGER.debug(f"Performing maintenance for {entry_id}")
            
            cache = await load_reversal_cache(self.hass, entry_id)
            if not cache:
                _LOGGER.warning(f"No cache found during maintenance for {entry_id}")
                return
            
            config_data = get_config_entry_data(entry_id)
            now_utc = dt_util.now().astimezone(timezone.utc)
            
            # Check if any checkpoints have passed
            passed_checkpoints = [cp for cp in cache['checkpoints'] if cp['time'] <= now_utc]
            
            if passed_checkpoints:
                _LOGGER.debug(f"Updating last_known_state after {len(passed_checkpoints)} passed checkpoints")
                
                # Update last_known_state from last passed checkpoint
                last_checkpoint = passed_checkpoints[-1]
                cache['last_known_state'] = {
                    'time': last_checkpoint['time'],
                    'direction': last_checkpoint['direction'],
                    'azimuth': last_checkpoint['azimuth']
                }
                
                # Remove passed checkpoints
                cache['checkpoints'] = [cp for cp in cache['checkpoints'] if cp['time'] > now_utc]
            
            # Refill checkpoints to maintain coverage
            cache = await self._refill_checkpoints(entry_id, cache, config_data)
            
            await save_reversal_cache(self.hass, entry_id, cache)
            
            # Reschedule for next checkpoint
            await self._schedule_next_maintenance(entry_id)
            
        except Exception as e:
            _LOGGER.error(f"Error during maintenance for {entry_id}: {e}", exc_info=True)
    


# Global cache manager instance
_cache_manager_instance: Optional[ReversalCacheManager] = None


def get_reversal_cache_manager(hass: HomeAssistant) -> ReversalCacheManager:
    """Get the global reversal cache manager instance."""
    global _cache_manager_instance
    if _cache_manager_instance is None:
        _cache_manager_instance = ReversalCacheManager(hass)
    return _cache_manager_instance





