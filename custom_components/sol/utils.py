"""Utility functions for the Sol integration."""

from __future__ import annotations

import datetime
import logging
import math
from datetime import timezone

import ephem
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .const import DOMAIN, DEBUG_ELEVATION_SENSOR, ELEVATION_TOLERANCE, AZIMUTH_DEGREE_TOLERANCE, AZIMUTH_REVERSAL_SEARCH_MAX_ITERATIONS, AZIMUTH_TERNARY_SEARCH_MAX_ITERATIONS, azimuth_step as DEFAULT_AZIMUTH_STEP
from .config_store import get_config_entry_data

_LOGGER = logging.getLogger(__name__)


def calculate_azimuth_derivative(az1: float, az2: float) -> float:
    """
    Calculate azimuth change handling wrap-around.
    
    Args:
        az1: First azimuth value
        az2: Second azimuth value
        
    Returns:
        Azimuth difference with wraparound handled
    """
    diff = az2 - az1
    
    # Handle wrap-around cases
    if diff > 180:
        diff -= 360  # e.g., 10° - 350° = 20° (not -340°)
    elif diff < -180:
        diff += 360  # e.g., 350° - 10° = -20° (not 340°)
    
    return diff


def format_sensor_naming(sensor_name: str, entry_id: str) -> tuple[str, str]:
    """
    Create consistent sensor name and unique ID for solar sensors.
    
    Args:
        sensor_name: The specific name for this sensor (e.g., "solar elevation", "azimuth angle")
        entry_id: The config entry ID to generate a unique identifier
    
    Returns:
        Tuple of (formatted_sensor_name, unique_id)
        
    Example:
        sensor_name, unique_id = format_sensor_naming("solar elevation", "entry_123")
        # Returns: ("Solar Elevation", "sol_solar_elevation_entry_123") or ("Tokyo - Solar Elevation", "sol_tokyo_solar_elevation_entry_123")
    """
    
    
    # Get config data to check for location name
    config_data = get_config_entry_data(entry_id)
    
    
    # Just use the sensor name directly
    name = sensor_name
    location_name = config_data.get("location_name") if config_data else None
    
    
    if location_name is not None:
        formatted_name = f"{location_name.title()} - {sensor_name.title()}"
        # Create unique ID with location prefix
        unique_id = f"{slugify(DOMAIN)}_{slugify(location_name)}_{slugify(sensor_name, separator='_')}_{entry_id}"
        
    else:
        formatted_name = f"{name.title()}"
        # Create unique ID without location prefix for unnamed sensors
        unique_id = f"{slugify(DOMAIN)}_{slugify(sensor_name, separator='_')}_{entry_id}"
        

    return formatted_name, unique_id


def format_input_entity_naming(sensor_name: str, config_variable: str) -> tuple[str, str]:
    """
    Create consistent input entity name and unique ID for configuration variables.
    
    Args:
        sensor_name: The specific name for this sensor (e.g., "solar elevation", "azimuth angle")
        config_variable: The configuration variable name (e.g., "panel angle", "efficiency")
    
    Returns:
        Tuple of (formatted_input_entity_name, unique_id)
        
    Example:
        input_name, input_id = format_input_entity_naming("solar elevation", "panel angle")
        # Returns: ("Sol - Solar Elevation - Panel Angle", "sol_solar_elevation_panel_angle")
    """
    # Format input entity name: "[DOMAIN.title()] - [sensor_name.title()] - [config_variable.title()]"
    formatted_name = f"{DOMAIN.title()} - {sensor_name.title()} - {config_variable.title()}"
    formatted_unique_id = f"{slugify(DOMAIN)}_{slugify(sensor_name, separator='_')}_{slugify(config_variable, separator='_')}"
    
    return formatted_name, formatted_unique_id


def get_sun_position(
    hass: HomeAssistant,
    dt: datetime.datetime,
    entry_id: str,
    use_center: bool = True,
    config_data: dict = None
) -> dict:
    """
    Calculate current sun position using ephem.
    
    Args:
        hass: Home Assistant instance to get location settings
        dt: Local datetime to calculate position for
        use_center: Whether to use the center of the sun (True) or the edge (False)
    
    Returns:
        Dictionary with sun position data:
        - azimuth: Sun azimuth in degrees (0=North, 90=East, 180=South, 270=West)
        - elevation: Sun elevation in degrees
        - declination: Sun declination in degrees
        - size: Angular size of the sun's disk (in degrees)
        - latitude: Latitude of the location
        - longitude: Longitude of the location
        - elevation_m: Elevation in meters
        - pressure_mbar: Pressure in millibars
    """
    # Get location from config entry data, fallback to Home Assistant configuration
    if config_data is None:
        config_data = get_config_entry_data(entry_id)
    latitude = config_data.get("latitude", hass.config.latitude) if config_data else hass.config.latitude
    longitude = config_data.get("longitude", hass.config.longitude) if config_data else hass.config.longitude
    elevation = config_data.get("elevation", hass.config.elevation) if config_data else hass.config.elevation
    pressure_mbar = config_data.get("pressure_mbar", 1013.25) if config_data else 1013.25
    
    # Convert local datetime to UTC for ephem
    dt_utc = dt.astimezone(timezone.utc)
    
    # Create observer
    observer = ephem.Observer()
    observer.lat = str(latitude)
    observer.lon = str(longitude)
    observer.elevation = elevation
    observer.pressure = pressure_mbar
    observer.date = dt_utc
    
    # Set horizon based on use_center parameter
    if not use_center:
        # Set horizon to 0 for edge of sun calculations
        observer.horizon = '0'
    
    # Create sun object
    sun = ephem.Sun()
    
    # Calculate current position
    sun.compute(observer)
    
    # Convert angles from radians to degrees
    azimuth_deg = math.degrees(sun.az) % 360
    elevation_deg = math.degrees(sun.alt)

    declination_deg = math.degrees(sun.dec)
    


    size_deg = sun.size/3600
    
    # Calculate solar noon (transit) for today
    try:
        solar_noon = observer.next_transit(sun)
        solar_noon_dt = solar_noon.datetime().replace(tzinfo=timezone.utc)
    except Exception:
        solar_noon_dt = None

    # Calculate solar midnight (antitransit) for today
    try:
        solar_midnight = observer.next_antitransit(sun)
        solar_midnight_dt = solar_midnight.datetime().replace(tzinfo=timezone.utc)
    except Exception:
        solar_midnight_dt = None
    
    return {
        "azimuth": azimuth_deg,
        "elevation": elevation_deg,
        "declination": declination_deg,
        "size": size_deg,
        "latitude": latitude,
        "longitude": longitude,
        "elevation_m": elevation,
        "pressure_mbar": pressure_mbar,
        "solar_noon": solar_noon_dt,
        "solar_midnight": solar_midnight_dt
    }

def get_next_step(
    target_type: str,
    step_value: float,
    sun_data: dict,
    dt: datetime.datetime,
    entry_id: str,
    hass: HomeAssistant,
    debug_flag: bool = False,
    config_data: dict = None,
    reversal_cache: dict = None
) -> dict | float | None:
    """
    Calculate the next step value for elevation and azimuth updates.
    For azimuth, returns a dict with azimuth, reversal flag, and reversal time.
    For elevation, returns the next step as a float.

    Args:
        target_type: 'elevation' or 'azimuth'
        step_value: The step increment value.
        sun_data: Dictionary containing sun position data with solar noon and midnight
        dt: Local datetime to calculate from
        entry_id: Config entry ID for location data
        hass: Home Assistant instance
        debug_flag: Whether to enable debug logging for this call

    Returns:
        For azimuth: dict with keys 'azimuth', 'reversal', and optionally 'reversal_time'.
        For elevation: float value for the next step.
        None if target_type is invalid or current_position is missing.
    """
    dt_utc = dt.astimezone(timezone.utc)


    if target_type == 'elevation':
        current_position = sun_data.get('elevation')
        if current_position is None:
            return None
            
        # Use solar events cache for more reliable direction determination
        try:
            from .cache import get_cached_solar_event
            cached_next_event_time, cached_next_event_type, cached_next_event_elevation, used_cache = get_cached_solar_event(
                hass, entry_id, dt
            )
            
            # Check if we have valid cached data and it's not in the past
            # Convert to timestamps for precise comparison to avoid floating-point precision issues
            if (cached_next_event_time and 
                cached_next_event_type and 
                cached_next_event_elevation is not None):
                
                cached_timestamp = cached_next_event_time.timestamp()
                current_timestamp = dt_utc.timestamp()
                
                if cached_timestamp >= current_timestamp:
                    # Use cached solar event for direction determination
                    time_difference = cached_timestamp - current_timestamp
                    
                    if cached_next_event_type == 'noon':
                        try:
                            next_step_value = round((round(current_position / step_value) * step_value) + step_value, 2)
                            if next_step_value > cached_next_event_elevation:
                                # Overshoot → pass event elevation and its time to avoid tangent search
                                return {
                                    'elevation': cached_next_event_elevation,
                                    'next_time': cached_next_event_time,
                                    'event': 'noon'
                                }
                            else:
                                # Heading toward solar noon - elevation increasing
                                if time_difference >= ELEVATION_TOLERANCE:
                                    next_rising_dt, next_setting_dt, next_event_dt = get_time_at_elevation(
                                        hass=hass,
                                        target_elevation=next_step_value,
                                        dt=dt_utc - datetime.timedelta(minutes=60),
                                        entry_id=entry_id,
                                        next_transit_fallback=True,
                                        config_data=config_data,
                                    )
                                    event_label = 'rising' if next_event_dt == next_rising_dt else ('setting' if next_event_dt == next_setting_dt else None)
                                    return {
                                        'elevation': float(next_step_value),
                                        'next_time': next_rising_dt,
                                        'event': 'rising'
                                    }
                                else:
                                    # At or very close to solar noon - use event elevation and time
                                    return {
                                        'elevation': cached_next_event_elevation,
                                        'next_time': cached_next_event_time,
                                        'event': 'noon'
                                    }
                        except Exception as e:
                            _LOGGER.error(f"Error in solar noon calculation: current_position={current_position}, step_value={step_value}, cached_next_event_elevation={cached_next_event_elevation}, time_difference={time_difference}, error={e}")
                            # Fallback to cached event elevation
                            return {
                                'elevation': 'Unknown',
                                'next_time': dt_utc + datetime.timedelta(minutes=1),
                                'event': 'Fallback Method'
                            }

                    elif cached_next_event_type == 'midnight':
                        try:
                            # Heading toward solar midnight - elevation decreasing
                            next_step_value = round((round(current_position / step_value) * step_value) - step_value, 2)
                            if next_step_value < cached_next_event_elevation:
                                # Overshoot → pass event elevation and its time to avoid tangent search
                                return {
                                    'elevation': cached_next_event_elevation,
                                    'next_time': cached_next_event_time,
                                    'event': 'midnight'
                                }
                            else:
                                if time_difference >= ELEVATION_TOLERANCE:
                                    next_rising_dt, next_setting_dt, next_event_dt = get_time_at_elevation(
                                        hass=hass,
                                        target_elevation=next_step_value,
                                        dt=dt_utc - datetime.timedelta(minutes=60),
                                        entry_id=entry_id,
                                        next_transit_fallback=True,
                                        config_data=config_data,
                                    )
                                    event_label = 'rising' if next_event_dt == next_rising_dt else ('setting' if next_event_dt == next_setting_dt else None)
                                    return {
                                        'elevation': float(next_step_value),
                                        'next_time': next_setting_dt,
                                        'event': 'setting'
                                    }
                                else:
                                    # At or very close to solar midnight - use event elevation and time
                                    return {
                                        'elevation': cached_next_event_elevation,
                                        'next_time': cached_next_event_time,
                                        'event': 'midnight'
                                    }
                        except Exception as e:
                            _LOGGER.error(f"Error in solar midnight calculation: current_position={current_position}, step_value={step_value}, cached_next_event_elevation={cached_next_event_elevation}, time_difference={time_difference}, error={e}")
                            # Fallback to cached event elevation
                            return {
                                'elevation': 'Unknown',
                                'next_time': dt_utc + datetime.timedelta(minutes=1),
                                'event': 'Fallback Method'
                            }
                    else:
                        # Fallback to current position
                        return {
                            'elevation': 'Unknown',
                            'next_time': dt_utc + datetime.timedelta(minutes=1),
                            'event': 'Fallback Method'
                        }
            else:
                # Cache invalid, stale, or not available - trigger fallback
                raise Exception("Cache invalid or stale")
                    
        except Exception as e:
            # Single fallback logic for all cases:
            # - Cached event is in the past
            # - Cache not available
            # - Any other cache failure
            _LOGGER.debug(f"Using fallback logic for elevation step calculation: {e}")
            return {
                'elevation': 'Unknown',
                'next_time': dt_utc + datetime.timedelta(minutes=1),
                'event': 'Fallback Method'
            }


    elif target_type == 'azimuth':
        current_position = sun_data.get('azimuth')
        if current_position is None:
            return None
        
        # Get checkpoint cache
        if reversal_cache:
            # Use checkpoint cache system
            from .reversal_cache import get_reversal_cache_manager
            cache_manager = get_reversal_cache_manager(hass)
            current_direction = cache_manager.get_current_direction(reversal_cache, dt_utc)
            
            # Handle both old and new cache formats during migration
            if 'checkpoints' in reversal_cache:
                # New checkpoint format - get ALL future checkpoints (not just reversals)
                all_checkpoints = [
                    cp for cp in reversal_cache.get('checkpoints', []) 
                    if cp['time'] > dt_utc
                ]
                # Keep separate list of just reversals for compatibility
                reversals = [cp for cp in all_checkpoints if cp.get('is_reversal', False)]
            elif 'reversals' in reversal_cache:
                # Old format (during migration)
                reversals = [r for r in reversal_cache.get('reversals', []) if r['time'] > dt_utc]
                all_checkpoints = reversals  # Old format only had reversals
            else:
                reversals = []
                all_checkpoints = []
        else:
            # Fallback: use simple latitude-based direction (no reversals)
            if config_data is None:
                config_data = get_config_entry_data(entry_id)
            latitude = config_data.get("latitude", hass.config.latitude) if config_data else hass.config.latitude
            declination = sun_data.get('declination')
            if declination is None:
                return None
            current_direction = 1 if latitude > declination else -1
            reversals = []
            all_checkpoints = []
        
        # Calculate next azimuth step with signed arithmetic
        signed_step = step_value * current_direction
        target_azimuth = (round(current_position / step_value) * step_value + signed_step) % 360
        
        # Check if any checkpoint occurs before the calculated target
        # Checkpoints take priority over step targets to maintain accurate state
        for checkpoint in all_checkpoints:
            checkpoint_time = checkpoint['time']
            checkpoint_azimuth = checkpoint['azimuth']
            is_reversal = checkpoint.get('is_reversal', True)  # Old format assumes all are reversals
            
            # Check if we're very close to a checkpoint (within 2 degrees)
            azimuth_distance = abs(current_position - checkpoint_azimuth)
            if azimuth_distance > 180:  # Handle wraparound
                azimuth_distance = 360 - azimuth_distance
            
            is_close_to_checkpoint = azimuth_distance <= 2.0  # Within 2 degrees
            
            # Check if checkpoint blocks the step target
            blocks = False
            if is_close_to_checkpoint or checkpoint_time > dt_utc:
                if current_direction == 1:  # Moving positive
                    # Moving positive: check if checkpoint is between current and target
                    if current_position <= target_azimuth:
                        # No wraparound
                        blocks = current_position < checkpoint_azimuth < target_azimuth
                    else:
                        # Wraparound case (e.g., 350° -> 10°)
                        blocks = (checkpoint_azimuth > current_position) or (checkpoint_azimuth < target_azimuth)
                else:  # Moving negative (current_direction == -1)
                    # Moving negative: check if checkpoint is between target and current
                    if target_azimuth <= current_position:
                        # No wraparound
                        blocks = target_azimuth < checkpoint_azimuth < current_position
                    else:
                        # Wraparound case (e.g., 10° -> 350°)
                        blocks = (checkpoint_azimuth < current_position) or (checkpoint_azimuth > target_azimuth)
            
            if blocks:
                # This checkpoint blocks our path - use it as the target
                return {
                    "azimuth": checkpoint_azimuth,
                    "reversal": is_reversal,
                    "reversal_time": checkpoint_time if is_reversal else None
                }
            
            # Only check the first future checkpoint
            break
        
        return {
            "azimuth": target_azimuth,
            "reversal": False
        }

    else:
        return None

def get_time_at_elevation(
    hass: HomeAssistant,
    target_elevation: float,
    dt: datetime.datetime,
    entry_id: str,
    use_center: bool = True,
    next_transit_fallback: bool = False,
    config_data: dict = None
) -> tuple[datetime.datetime | None, datetime.datetime | None, datetime.datetime | None]:
    """
    Calculate when the sun will be at a specific elevation.
    
    Args:
        hass: Home Assistant instance to get location settings
        target_elevation: Target elevation in degrees
        dt: Local datetime to calculate from
        entry_id: Config entry ID for location data
        use_center: Whether to use center of sun (True) or edge of sun (False)
        next_transit_fallback: If True, use transit/antitransit fallback; if False, search 365 days
    
    Returns:
        Tuple of (next_rising, next_setting, next_event)
    """
    # Get location from config entry data
    if config_data is None:
        config_data = get_config_entry_data(entry_id)
    latitude = config_data.get("latitude", hass.config.latitude) if config_data else hass.config.latitude
    longitude = config_data.get("longitude", hass.config.longitude) if config_data else hass.config.longitude
    elevation = config_data.get("elevation", hass.config.elevation) if config_data else hass.config.elevation
    pressure_mbar = config_data.get("pressure_mbar", 1013.25) if config_data else 1013.25
    
    # Convert local datetime to UTC for ephem
    dt_utc = dt.astimezone(timezone.utc)
    
    # Create observer with all settings
    observer = ephem.Observer()
    observer.lat = str(latitude)
    observer.lon = str(longitude)
    observer.elevation = elevation
    observer.pressure = pressure_mbar
    observer.date = dt_utc
    observer.horizon = str(target_elevation)  # Set horizon to target elevation
    
    # Create sun object
    sun = ephem.Sun()
    
    try:
        # Try to get rising/setting at target elevation
        next_rising = observer.next_rising(sun, use_center=use_center)
        next_setting = observer.next_setting(sun, use_center=use_center)
        
        # Convert to datetime objects
        next_rising_dt = next_rising.datetime().replace(tzinfo=timezone.utc) if next_rising else None
        next_setting_dt = next_setting.datetime().replace(tzinfo=timezone.utc) if next_setting else None
        
        # Calculate which is next (rising or setting)
        events = [next_rising_dt, next_setting_dt]
        valid_events = [event for event in events if event is not None]
        next_event_dt = min(valid_events) if valid_events else None
        
        return next_rising_dt, next_setting_dt, next_event_dt
        
    except Exception:
        # Target elevation not reachable
        if next_transit_fallback:
            # Get current sun elevation and set as horizon
            current_sun_data = get_sun_position(hass, dt, entry_id, config_data=config_data)
            current_elevation = current_sun_data['elevation']
            observer.horizon = str(current_elevation)
            
            try:
                next_transit = observer.next_transit(sun)
                next_antitransit = observer.next_antitransit(sun)
                
                # Convert to datetime objects
                next_transit_dt = next_transit.datetime().replace(tzinfo=timezone.utc) if next_transit else None
                next_antitransit_dt = next_antitransit.datetime().replace(tzinfo=timezone.utc) if next_antitransit else None
                
                # Find the sooner of transit or antitransit
                events = [next_transit_dt, next_antitransit_dt]
                valid_events = [event for event in events if event is not None]
                next_transit_event = min(valid_events) if valid_events else None
                
                return None, None, next_transit_event
                
            except Exception:
                return None, None, None
        
        else:  # next_transit_fallback == False
            try:
                # Iterate through next 365 days
                search_date = dt_utc
                
                for day in range(365):
                    search_date += datetime.timedelta(days=1)
                    observer.date = search_date
                    
                    try:
                        test_rising = observer.next_rising(sun, use_center=use_center)
                        test_setting = observer.next_setting(sun, use_center=use_center)
                        
                        if test_rising or test_setting:
                            # Found valid rising/setting times
                            next_rising_dt = test_rising.datetime().replace(tzinfo=timezone.utc) if test_rising else None
                            next_setting_dt = test_setting.datetime().replace(tzinfo=timezone.utc) if test_setting else None
                            
                            # Calculate the sooner of rising or setting
                            events = [next_rising_dt, next_setting_dt]
                            valid_events = [event for event in events if event is not None]
                            next_event_dt = min(valid_events) if valid_events else None
                            
                            return next_rising_dt, next_setting_dt, next_event_dt
                    except Exception:
                        continue
                
                # Nothing found in 365 days
                return None, None, None
                
            except Exception:
                return None, None, None


def get_time_at_azimuth(
    hass: HomeAssistant,
    target_azimuth: float,
    current_dt: datetime.datetime,
    entry_id: str,
    start_dt: datetime.datetime | None = None,
    search_window_hours: float = 6.0,
    config_data: dict = None,
    reversal_cache: dict = None
) -> tuple[datetime.datetime | None, dict]:
    """
    Find the time when the sun will be at a specific azimuth using ternary search.
    
    Args:
        hass: Home Assistant instance
        target_azimuth: Target azimuth in degrees (0-360)
        current_dt: Current datetime (search will never go before this)
        entry_id: Config entry ID for location data
        start_dt: Starting datetime for search (defaults to nearest solar noon/midnight)
        search_window_hours: Initial search window in hours (defaults to 6.0)
        config_data: Config data dictionary
        reversal_cache: Optional reversal cache data (from new cache system)
    
    Returns:
        Tuple of (datetime when sun will be at target azimuth or None, performance metrics dict)
    """
    # Start timing and iteration tracking
    import time
    start_time = time.time()
    total_iterations = 0
    
    # IMMEDIATELY convert ALL datetime objects to UTC first
    current_dt_utc = current_dt.astimezone(timezone.utc)
    start_dt_utc = start_dt.astimezone(timezone.utc) if start_dt is not None else None
    
    # Get cached checkpoint data to determine maximum search window
    try:
        if reversal_cache:
            # Handle both old and new cache formats during migration
            if 'checkpoints' in reversal_cache:
                # New checkpoint format - get ALL future checkpoints (not just reversals)
                all_checkpoints = [
                    cp for cp in reversal_cache.get('checkpoints', []) 
                    if cp['time'] > current_dt_utc
                ]
                # Keep reversals list for compatibility
                reversals = [cp for cp in all_checkpoints if cp.get('is_reversal', False)]
            elif 'reversals' in reversal_cache:
                # Old format (during migration)
                reversals = [r for r in reversal_cache.get('reversals', []) if r['time'] > current_dt_utc]
                all_checkpoints = reversals  # Old format only had reversals
            else:
                reversals = []
                all_checkpoints = []
            
            # Find next future checkpoint (any type) to cap search window
            next_checkpoint_time = None
            if all_checkpoints:
                next_checkpoint_time = all_checkpoints[0]['time']
            
            # Calculate max search window (cap at next checkpoint or 24 hours)
            if next_checkpoint_time:
                seconds_until_checkpoint = (next_checkpoint_time - current_dt_utc).total_seconds()
                max_search_seconds = min(24 * 3600, seconds_until_checkpoint)
            else:
                max_search_seconds = 24 * 3600  # No checkpoints, use 24h limit
        else:
            # No cache available - use 24h search window
            max_search_seconds = 24 * 3600
            
    except Exception as e:
        max_search_seconds = 24 * 3600
    
    # Normalize target azimuth to 0-360 range
    target_azimuth = target_azimuth % 360
    
    def normalize_azimuth(azimuth: float) -> float:
        """Convert ephem's 360° to 0° and normalize to 0-360 range."""
        if abs(azimuth - 360.0) < 0.001:
            azimuth = 0.0
        return azimuth % 360
    
    # If no start_dt provided, start search from current time
    if start_dt_utc is None:
        start_dt_utc = current_dt_utc

    # Ensure start_dt is not before current_dt (both in UTC)
    if start_dt_utc < current_dt_utc:
        start_dt_utc = current_dt_utc

    def ternary_search(start_time: datetime.datetime, initial_window_seconds: float) -> tuple[datetime.datetime | None, int]:
        """Perform ternary search for target azimuth."""
        
        # Initialize iteration counter
        iterations = 0
        
        # Initialize window - forward-looking from start_time
        # This ensures we search from current time forward, but can look back if needed during narrowing
        left = start_time
        right = start_time + datetime.timedelta(seconds=initial_window_seconds)
        
        # No need to shift - window is already positioned correctly starting from current time
        
        # Initialize best result tracking
        best_time = None
        best_azimuth_diff = float('inf')
        
        # Direction-agnostic window boundary check
        # Instead of assuming azimuth direction, sample multiple points in the window
        # to see if the target azimuth is reachable
        def is_target_in_window(left_time: datetime.datetime, right_time: datetime.datetime) -> bool:
            """Check if target azimuth is reachable within the time window."""
            
            # Sample more points for better wraparound detection
            sample_times = []
            window_duration = (right_time - left_time).total_seconds()
            num_samples = 10  # Increase from 5 to 10 for better detection
            
            for i in range(num_samples):
                sample_time = left_time + datetime.timedelta(seconds=window_duration * i / (num_samples - 1))
                sample_times.append(sample_time)
            
            # Get azimuth values at sample points
            sample_azimuths = []
            for sample_time in sample_times:
                sample_data = get_sun_position(hass, sample_time, entry_id, config_data=config_data)
                sample_azimuth = normalize_azimuth(sample_data.get('azimuth', 0))
                sample_azimuths.append(sample_azimuth)
            
            # Helper function to check if target is between two azimuths
            def is_target_between_azimuths(az1: float, az2: float, target: float) -> bool:
                """Check if target azimuth is between two azimuth values, handling wraparound."""
                
                # Calculate the azimuth difference
                diff = az2 - az1
                
                # Handle wraparound
                if diff > 180:
                    diff -= 360  # e.g., 350° → 10° becomes -340° → 20°
                elif diff < -180:
                    diff += 360  # e.g., 10° → 350° becomes 340° → -20°
                
                if diff >= 0:  # Moving in positive direction
                    if az1 <= az2:  # No wraparound
                        return az1 <= target <= az2
                    else:  # Wraparound case (e.g., 350° → 10°)
                        return target >= az1 or target <= az2
                else:  # Moving in negative direction
                    if az1 >= az2:  # No wraparound
                        return az2 <= target <= az1
                    else:  # Wraparound case (e.g., 10° → 350°)
                        return target <= az1 or target >= az2
            
            # Check if target is reachable by examining azimuth continuity
            for i in range(len(sample_azimuths) - 1):
                az1 = sample_azimuths[i]
                az2 = sample_azimuths[i + 1]
                
                # Check if target is between consecutive samples
                if is_target_between_azimuths(az1, az2, target_azimuth):
                    return True
            
            return False
        
        # Check if target is within window
        if not is_target_in_window(left, right):
            # Try expanding window if target not found
            expanded_window_seconds = initial_window_seconds * 2
            if expanded_window_seconds <= max_search_seconds:  # Use max_search_seconds
                result, sub_iterations = ternary_search(start_time, expanded_window_seconds)
                return result, iterations + sub_iterations
            return None, iterations

        # Perform ternary search with direction-agnostic narrowing
        while iterations < AZIMUTH_TERNARY_SEARCH_MAX_ITERATIONS:
            iterations += 1
            
            # Calculate test points at 1/3 and 2/3 of window
            mid1 = left + (right - left) / 3
            mid2 = right - (right - left) / 3
            
            # Get azimuths at test points
            mid1_data = get_sun_position(hass, mid1, entry_id, config_data=config_data)
            mid2_data = get_sun_position(hass, mid2, entry_id, config_data=config_data)
            mid1_azimuth = normalize_azimuth(mid1_data.get('azimuth', 0))
            mid2_azimuth = normalize_azimuth(mid2_data.get('azimuth', 0))
            
            # Calculate azimuth differences (handle wrap-around)
            diff1 = abs(mid1_azimuth - target_azimuth)
            if diff1 > 180:
                diff1 = 360 - diff1
                
            diff2 = abs(mid2_azimuth - target_azimuth)
            if diff2 > 180:
                diff2 = 360 - diff2
            
            # Track best result
            if diff1 < best_azimuth_diff:
                best_azimuth_diff = diff1
                best_time = mid1
                
            if diff2 < best_azimuth_diff:
                best_azimuth_diff = diff2
                best_time = mid2
            
            # Check if we've achieved degree-based tolerance
            if abs(best_azimuth_diff) <= AZIMUTH_DEGREE_TOLERANCE:
                return best_time, iterations
            
            # Also check azimuth-based tolerance as a fallback to prevent infinite loops
            left_azimuth = normalize_azimuth(get_sun_position(hass, left, entry_id, config_data=config_data).get('azimuth', 0))
            right_azimuth = normalize_azimuth(get_sun_position(hass, right, entry_id, config_data=config_data).get('azimuth', 0))
            azimuth_window_diff = abs(right_azimuth - left_azimuth)
            if azimuth_window_diff > 180:  # Handle wraparound
                azimuth_window_diff = 360 - azimuth_window_diff
            if azimuth_window_diff <= AZIMUTH_DEGREE_TOLERANCE:
                return (best_time if best_time is not None else left + (right - left) / 2), iterations
            
            # Direction-agnostic window narrowing
            # Narrow the window toward whichever test point is closer to target
            if diff1 < diff2:
                # mid1 is closer to target, search around mid1
                # Check which half contains the target
                if is_target_in_window(left, mid1):
                    right = mid1
                elif is_target_in_window(mid1, right):
                    left = mid1
                else:
                    # Target might be very close to mid1, narrow around it
                    quarter = (right - left) / 4
                    left = mid1 - quarter
                    right = mid1 + quarter
            else:
                # mid2 is closer to target, search around mid2
                # Check which half contains the target
                if is_target_in_window(left, mid2):
                    right = mid2
                elif is_target_in_window(mid2, right):
                    left = mid2
                else:
                    # Target might be very close to mid2, narrow around it
                    quarter = (right - left) / 4
                    left = mid2 - quarter
                    right = mid2 + quarter
            
            # Ensure we don't go before current_dt (both in UTC)
            if left < current_dt_utc:
                left = current_dt_utc
        
        # Check if we hit the iteration limit
        if iterations >= AZIMUTH_TERNARY_SEARCH_MAX_ITERATIONS:
            return (best_time if best_time is not None else left + (right - left) / 2), iterations
        
        # Use best time found or middle of final window
        return (best_time if best_time is not None else left + (right - left) / 2), iterations

    # Convert search window to seconds
    initial_window_seconds = search_window_hours * 3600
    
    # Perform the ternary search
    result, iterations = ternary_search(start_dt_utc, initial_window_seconds)
    
    # Calculate performance metrics
    end_time = time.time()
    execution_time = end_time - start_time
    
    metrics = {
        'execution_time_ms': round(execution_time * 1000, 2),
        'iterations': iterations,
        'max_iterations': AZIMUTH_TERNARY_SEARCH_MAX_ITERATIONS,
        'hit_iteration_limit': iterations >= AZIMUTH_TERNARY_SEARCH_MAX_ITERATIONS,
        'target_azimuth': target_azimuth,
        'search_window_hours': search_window_hours
    }
    
    return result, metrics


def get_solstice_curve(
    hass: HomeAssistant,
    date_time: datetime.datetime | None = None,
    entry_id: str | None = None,
    config_data: dict = None
) -> tuple[float, datetime.datetime, datetime.datetime]:
    """
    Calculate normalized solstice curve (-1 to +1) where -1=winter solstice, +1=summer solstice.
    
    The curve represents the seasonal position:
    - +1.0 at summer solstice (maximum solar declination)
    - -1.0 at winter solstice (minimum solar declination)
    - 0.0 at equinox (midpoint between solstices)
    - Values increase as we approach summer solstice
    - Values decrease as we approach winter solstice
    
    Note: Accounts for hemisphere - southern hemisphere has inverted seasons.
    
    Args:
        hass: Home Assistant instance (used for timezone if date_time is None)
        date_time: Time to calculate for (default: current time)
        entry_id: Config entry ID (optional, for future location-specific calculations)
        
    Returns:
        Tuple (normalized_value, previous_solstice, next_solstice)
    """
    # Use current time if not specified
    if date_time is None:
        import zoneinfo
        local_tz = zoneinfo.ZoneInfo(hass.config.time_zone)
        date_time = datetime.datetime.now(local_tz)
    
    # Convert to UTC for ephem calculations
    date_time = date_time.astimezone(timezone.utc)
    
    # Get latitude to determine hemisphere
    if config_data is None:
        config_data = hass.data.get(DOMAIN, {}).get(entry_id or 'default', {})
    latitude = config_data.get("latitude", hass.config.latitude) if config_data else hass.config.latitude
    is_southern_hemisphere = latitude < 0
    
    # Find solstices
    next_summer = ephem.next_summer_solstice(date_time)
    next_winter = ephem.next_winter_solstice(date_time)
    prev_summer = ephem.previous_summer_solstice(date_time)
    prev_winter = ephem.previous_winter_solstice(date_time)
    
    # Convert to datetime objects
    solstices = {
        "next_summer": ephem.Date(next_summer).datetime().replace(tzinfo=timezone.utc),
        "next_winter": ephem.Date(next_winter).datetime().replace(tzinfo=timezone.utc),
        "prev_summer": ephem.Date(prev_summer).datetime().replace(tzinfo=timezone.utc),
        "prev_winter": ephem.Date(prev_winter).datetime().replace(tzinfo=timezone.utc),
    }
    
    # Determine adjacent solstices for reference
    if solstices["next_summer"] < solstices["next_winter"]:
        previous_solstice = solstices["prev_winter"]
        next_solstice = solstices["next_summer"]
    else:
        previous_solstice = solstices["prev_summer"]
        next_solstice = solstices["next_winter"]
    
    # Get declination at both solstices to establish the range
    closest_summer = solstices["prev_summer"] if solstices["prev_summer"] > solstices["next_summer"] else solstices["next_summer"]
    closest_winter = solstices["prev_winter"] if solstices["prev_winter"] > solstices["next_winter"] else solstices["next_winter"]
    
    # Get current sun data for latitude determination
    current_sun_data = get_sun_position(hass, date_time, entry_id or 'default', config_data=config_data)
    
    # Get sun data at closest solstices for normalization
    summer_sun_data = get_sun_position(hass, closest_summer, entry_id or 'default', config_data=config_data)
    winter_sun_data = get_sun_position(hass, closest_winter, entry_id or 'default', config_data=config_data)
    
    # Calculate solar declination at given time
    current_declination = current_sun_data['declination']
    
    summer_declination = summer_sun_data['declination']
    winter_declination = winter_sun_data['declination']
    
    # Ensure we have the correct max/min values
    max_declination = max(summer_declination, winter_declination)  # Should be summer
    min_declination = min(summer_declination, winter_declination)  # Should be winter
    
    if max_declination == min_declination:  # Prevent division by zero
        normalized = 0.0
    else:
        # Calculate normalized value: 0 at winter solstice, 1 at summer solstice (0-1 range)
        normalized_0to1 = (current_declination - min_declination) / (max_declination - min_declination)
        
        # Account for hemisphere: In southern hemisphere, seasons are inverted
        # When it's summer in the north (positive declination), it's winter in the south
        if is_southern_hemisphere:
            normalized_0to1 = 1.0 - normalized_0to1
        
        # Clamp between 0-1 first
        normalized_0to1 = max(0.0, min(1.0, normalized_0to1))
        
        # Convert from 0-1 range to -1 to +1 range
        # -1 at winter solstice, 0 at equinox, +1 at summer solstice
        normalized = 2.0 * normalized_0to1 - 1.0
        
        # Clamp between -1 to +1 (shouldn't be necessary, but safety check)
        normalized = max(-1.0, min(1.0, normalized))
    
    return normalized, previous_solstice, next_solstice


