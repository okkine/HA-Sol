"""Utility functions for Sol integration."""

from typing import Optional, Tuple
from datetime import datetime, timedelta, timezone

# Import constants needed for body functions
from .const import (
    eph,
    ts,
)

from skyfield import searchlib
from skyfield.api import Topos


def get_formatted_sensor_name(
    sensor_name: str,
    body_key: str,
    location_name: Optional[str] = None,
    prefix: Optional[str] = None,
    suffix: Optional[str] = None,
    event_type: Optional[str] = None
) -> str:
    """Get formatted sensor name with celestial body prefix and optional location/suffix.
    
    Args:
        sensor_name: Base sensor name (e.g., "Azimuth", "Elevation", "Rise", "Set")
        body_key: Body identifier (e.g., "sun", "moon", "mars")
        location_name: Optional location name to prepend (e.g., "Calgary", "Columbia")
        prefix: Optional prefix to add before base_name
        suffix: Optional suffix to add after base_name
        event_type: Optional flag for rise/set events ("rise", "set", or None)
        
    Returns:
        Formatted sensor name (e.g., "Calgary: Sun Azimuth", "Sunrise", "Columbia: Moon Elevation")
    """
    body_name = body_key.capitalize()

    # Determine base_name based on event_type
    if event_type == "rise":
        base_name = f"{body_name}rise" if body_key in ("sun", "moon") else f"{body_name} Rise"
    elif event_type == "set":
        base_name = f"{body_name}set" if body_key in ("sun", "moon") else f"{body_name} Set"
    else:
        base_name = sensor_name

    # Build parts list (only include non-None values)
    parts = []
    if location_name:
        parts.append(location_name + ":")
    if event_type is None:
        parts.append(body_name)
    if prefix:
        parts.append(prefix)

    parts.append(base_name)
    if suffix:
        parts.append(suffix)
    
    # Join and title case
    result = " ".join(parts)
    return result.title()


def get_body(body_key: str, eph_instance=None):
    """Get Skyfield body object from identifier string.
    
    Args:
        body_key: Body identifier string (e.g., "sun", "moon", "jupiter")
        eph_instance: Skyfield ephemeris instance (defaults to module-level eph)
        
    Returns:
        Skyfield body object or None if not found
    """
    if eph_instance is None:
        eph_instance = eph
    
    mapping = {
        "sun": eph_instance['sun'],
        "moon": eph_instance['moon'],
        "mercury": eph_instance['mercury'],
        "venus": eph_instance['venus'],
        "mars": eph_instance['mars'],
        "jupiter": eph_instance[5],    # Jupiter barycenter
        "saturn": eph_instance[6],     # Saturn barycenter
        "uranus": eph_instance[7],     # Uranus barycenter
        "neptune": eph_instance[8],    # Neptune barycenter
        "pluto": eph_instance[9],      # Pluto barycenter
    }
    return mapping.get(body_key)


def is_within_reversal_range(latitude: float, current_declination: float, buffer: float = None) -> bool:
    """
    Determine if observer latitude is within the reversal range for a celestial body.
    
    Reversals occur when the observer's latitude is within the body's declination range
    (plus buffer). This means: abs(latitude) <= abs(current_declination) + buffer
    
    Args:
        latitude: Observer latitude in degrees
        current_declination: Current body declination in degrees
        buffer: Buffer value in degrees (defaults to REVERSAL_SCAN_BUFFER)
        
    Returns:
        True if within reversal range, False otherwise
    """
    from .const import REVERSAL_SCAN_BUFFER
    if buffer is None:
        buffer = REVERSAL_SCAN_BUFFER
    return bool(abs(latitude) <= abs(current_declination) + buffer)


def fit_between(
    current_value: float,
    old_min: float,
    old_max: float,
    new_min: float,
    new_max: float
) -> float:
    """
    Map a value from one range to another using linear interpolation.
    
    Args:
        current_value: The value to map (in the old range)
        old_min: Minimum value of the old range
        old_max: Maximum value of the old range
        new_min: Minimum value of the new range
        new_max: Maximum value of the new range
    
    Returns:
        The mapped value in the new range
    
    Example:
        # Map a value from 0-100 range to -1 to +1 range
        fit_between(50, 0, 100, -1, 1)  # Returns 0.0
        fit_between(0, 0, 100, -1, 1)   # Returns -1.0
        fit_between(100, 0, 100, -1, 1) # Returns 1.0
    """
    if old_max == old_min:
        # Prevent division by zero - return middle of new range
        return (new_min + new_max) / 2.0
    
    # Linear interpolation formula
    normalized = (current_value - old_min) / (old_max - old_min)
    return new_min + normalized * (new_max - new_min)


def get_declination_normalized(
    target_time: datetime,
    entry_id: str,
    config_data: dict,
    cached_solstices: Optional[dict] = None
) -> Tuple[float, datetime, datetime, datetime, datetime]:
    """
    Calculate normalized declination (-1 to +1) where -1=December solstice, +1=June solstice.
    
    Args:
        target_time: Time to calculate normalized declination for
        entry_id: Config entry ID for location data
        config_data: Config data dict containing latitude, longitude, etc.
        cached_solstices: Optional dict with cached solstice dates:
            - june_solstice: datetime
            - december_solstice: datetime
            - next_solstice: datetime
            - previous_solstice: datetime
            If None, solstices will be calculated
    
    Returns:
        Tuple (normalized_value, previous_solstice, next_solstice, june_solstice, december_solstice)
    """
    # Get sun body
    sun = eph['sun']
    earth = eph['earth']
    
    # Create observer location (declination doesn't depend on observer location, but need for API)
    observer_location = Topos(latitude_degrees=0.0, longitude_degrees=0.0)
    earth_observer = earth + observer_location
    
    # Convert target_time to UTC and Skyfield Time
    if target_time.tzinfo is None:
        target_time = target_time.replace(tzinfo=timezone.utc)
    target_time_utc = target_time.astimezone(timezone.utc)
    t_target = ts.from_datetime(target_time_utc)
    
    # Function to calculate sun declination at a given time
    def sun_declination(t):
        """Calculate sun declination in degrees at time t."""
        astrometric = earth_observer.at(t).observe(sun)
        apparent = astrometric.apparent()
        ra, dec, distance = apparent.radec()
        return dec.degrees
    
    # Set step_days for solstice search (optimized: 7 days)
    sun_declination.step_days = 7.0
    
    # Determine solstices
    if cached_solstices and 'june_solstice' in cached_solstices and 'december_solstice' in cached_solstices:
        # Use cached solstices
        june_solstice = cached_solstices['june_solstice']
        december_solstice = cached_solstices['december_solstice']
        next_solstice = cached_solstices.get('next_solstice')
        previous_solstice = cached_solstices.get('previous_solstice')
        
        # Ensure timezone-aware
        if june_solstice.tzinfo is None:
            june_solstice = june_solstice.replace(tzinfo=timezone.utc)
        if december_solstice.tzinfo is None:
            december_solstice = december_solstice.replace(tzinfo=timezone.utc)
    else:
        # Calculate solstices using find_maxima/minima
        # Search window: ±7 months (6 months + 1 month buffer)
        search_start_dt = target_time_utc - timedelta(days=7 * 30)  # ~7 months back
        search_end_dt = target_time_utc + timedelta(days=7 * 30)    # ~7 months forward
        t_start = ts.from_datetime(search_start_dt)
        t_end = ts.from_datetime(search_end_dt)
        
        # Find maxima (June solstice) and minima (December solstice)
        max_times, max_decls = searchlib.find_maxima(t_start, t_end, sun_declination)
        min_times, min_decls = searchlib.find_minima(t_start, t_end, sun_declination)
        
        if len(max_times) == 0 or len(min_times) == 0:
            # Fallback: return 0.0 if we can't find solstices
            return 0.0, target_time_utc, target_time_utc, target_time_utc, target_time_utc
        
        # Find closest June solstice (maxima) and December solstice (minima) to target
        # Convert all to datetime and find closest
        june_solstices = []
        for t in max_times:
            dt = t.utc_datetime()
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            june_solstices.append(dt)
        
        december_solstices = []
        for t in min_times:
            dt = t.utc_datetime()
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            december_solstices.append(dt)
        
        # Find closest June solstice to target
        june_solstice = min(june_solstices, key=lambda x: abs((x - target_time_utc).total_seconds()))
        
        # Find closest December solstice to target
        december_solstice = min(december_solstices, key=lambda x: abs((x - target_time_utc).total_seconds()))
        
        # Determine previous and next solstices relative to target_time
        all_solstices = sorted(june_solstices + december_solstices)
        previous_solstice = None
        next_solstice = None
        
        for solstice in all_solstices:
            if solstice < target_time_utc:
                previous_solstice = solstice
            elif solstice > target_time_utc:
                next_solstice = solstice
                break
        
        # If no previous/next found in range, we'll use the closest ones
        if previous_solstice is None:
            previous_solstice = min(all_solstices, key=lambda x: x)
        if next_solstice is None:
            next_solstice = max(all_solstices, key=lambda x: x)
    
    # Calculate declination at solstices and target time
    t_june = ts.from_datetime(june_solstice)
    t_december = ts.from_datetime(december_solstice)
    
    june_declination = sun_declination(t_june)
    december_declination = sun_declination(t_december)
    current_declination = sun_declination(t_target)
    
    # Ensure we have correct max/min (June should be max, December should be min)
    max_declination = max(june_declination, december_declination)
    min_declination = min(june_declination, december_declination)
    
    if max_declination == min_declination:
        normalized = 0.0
    else:
        # Normalize: -1 at December solstice, +1 at June solstice
        # Map from declination range (min_declination to max_declination) to normalized range (-1 to +1)
        normalized = fit_between(
            current_value=current_declination,
            old_min=min_declination,
            old_max=max_declination,
            new_min=-1.0,
            new_max=1.0
        )
        
        # Clamp between -1 to +1
        normalized = max(-1.0, min(1.0, normalized))
    
    return normalized, previous_solstice, next_solstice, june_solstice, december_solstice

