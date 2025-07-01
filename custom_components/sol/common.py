"""Common utilities and calculations for the Sol integration."""
import re
from typing import Optional, Tuple
from datetime import datetime, timezone
from slugify import slugify
import ephem
import math
import logging

try:
    import ambiance
    AMBIANCE_AVAILABLE = True
except ImportError:
    AMBIANCE_AVAILABLE = False

from .const import DOMAIN, NAME


def create_sensor_attributes(sensor_name: str, icon: Optional[str] = None) -> tuple[str, str]:
    """
    Create consistent sensor name and unique ID for a sensor.
    
    Args:
        sensor_name: The specific name for this sensor (e.g., "solar elevation", "azimuth angle")
        icon: Optional icon to use (defaults to solar-power if not provided)
    
    Returns:
        Tuple of (formatted_sensor_name, unique_id)
        
    Example:
        sensor_name, unique_id = create_sensor_attributes("solar elevation")
        # Returns: ("Sol - Solar Elevation", "sol_solar_elevation")
    """
    # Format sensor name: "[NAME] - [sensor_name]" with first letter of each word capitalized
    formatted_name = f"{NAME} - {sensor_name.title()}"
    
    # Format unique ID: "[DOMAIN]_[sensor_name]" slugified, lowercase
    slug = slugify(sensor_name, separator='_')
    unique_id = f"{DOMAIN}_{slug}"
    
    return formatted_name, unique_id


def create_input_entity_attributes(sensor_name: str, config_variable: str) -> tuple[str, str]:
    """
    Create consistent input entity name and unique ID for configuration variables.
    
    Args:
        sensor_name: The specific name for this sensor (e.g., "solar elevation", "azimuth angle")
        config_variable: The configuration variable name (e.g., "panel angle", "efficiency")
    
    Returns:
        Tuple of (formatted_input_entity_name, unique_id)
        
    Example:
        input_name, input_id = create_input_entity_attributes("solar elevation", "panel angle")
        # Returns: ("Sol - Solar Elevation - Panel Angle", "sol_solar_elevation_panel_angle")
    """
    # Format input entity name: "[NAME] - [sensor_name] - [config_variable]" with first letter of each word capitalized
    formatted_name = f"{NAME} - {sensor_name.title()} - {config_variable.title()}"
    
    # Format unique ID: "[DOMAIN]_[sensor_name]_[config_variable]" slugified, lowercase
    combined_name = f"{sensor_name} {config_variable}"
    slug = slugify(combined_name, separator='_')
    unique_id = f"{DOMAIN}_{slug}"
    
    return formatted_name, unique_id


def calculate_pressure_from_elevation(elevation_meters: float) -> float:
    """
    Calculate atmospheric pressure from elevation using the ambiance library.
    
    Args:
        elevation_meters: Elevation in meters above sea level
    
    Returns:
        Atmospheric pressure in mBar
        
    Note:
        Uses the International Standard Atmosphere (ISA) model via ambiance library.
        Falls back to simplified calculation if ambiance is not available.
    """
    if AMBIANCE_AVAILABLE:
        try:
            # Use ambiance library for accurate pressure calculation
            # ambiance.atmosphere() returns pressure in Pa, convert to mBar
            pressure_pa = ambiance.atmosphere(elevation_meters).pressure
            pressure_mbar = pressure_pa / 100.0
            return pressure_mbar
        except Exception:
            # Fall back to simplified calculation if ambiance fails
            pass
    
    # Fallback to simplified ISA model
    # Standard atmospheric pressure at sea level (mBar)
    P0 = 1013.25
    
    # Scale height for Earth's atmosphere (meters)
    H = 7400
    
    # Calculate pressure using exponential decay model
    pressure_mbar = P0 * math.exp(-elevation_meters / H)
    
    return pressure_mbar


def create_sun_helper(config_data: dict) -> 'SunHelper':
    """
    Create a SunHelper instance using configuration data.
    
    Args:
        config_data: Configuration data from config entry
    
    Returns:
        SunHelper instance with user-configured parameters
    """
    # Handle pressure calculation
    pressure = None
    if config_data.get("pressure_mode") == "auto":
        elevation = config_data.get("elevation", 0)
        pressure = calculate_pressure_from_elevation(elevation)
    elif config_data.get("pressure_mode") == "manual":
        pressure = config_data.get("pressure")
    
    return SunHelper(
        latitude=config_data.get("latitude"),
        longitude=config_data.get("longitude"),
        elevation=config_data.get("elevation"),
        temperature=config_data.get("temperature"),
        pressure=pressure,
        horizon=config_data.get("horizon")
    )


class SunHelper:
    """Helper class for sun position calculations using ephem."""
    
    def __init__(self, 
                 latitude: Optional[float] = None,
                 longitude: Optional[float] = None,
                 elevation: Optional[float] = None,
                 temperature: Optional[float] = None,
                 pressure: Optional[float] = None,
                 horizon: Optional[float] = None):
        """
        Initialize the SunHelper with location and atmospheric parameters.
        
        Args:
            latitude: Latitude in decimal degrees (uses system default if None)
            longitude: Longitude in decimal degrees (uses system default if None)
            elevation: Elevation in meters (uses system default if None)
            temperature: Temperature in Celsius (uses system default if None)
            pressure: Air pressure in mBar (uses system default if None)
            horizon: Horizon offset in degrees (uses system default if None)
        """
        # Create ephem observer
        self.observer = ephem.Observer()
        
        # Set location (will use system defaults if not provided)
        if latitude is not None:
            self.observer.lat = str(latitude)
        if longitude is not None:
            self.observer.lon = str(longitude)
        if elevation is not None:
            self.observer.elevation = elevation
        
        # Set atmospheric parameters (will use system defaults if not provided)
        if temperature is not None:
            self.observer.temp = temperature
        if pressure is not None:
            self.observer.pressure = pressure
        if horizon is not None:
            self.observer.horizon = str(horizon)
        
        # Set additional parameters for more accurate calculations
        self.observer.epoch = ephem.J2000  # Use J2000 epoch
        self.observer.compute_pressure()  # Enable pressure calculations
        
        # Store parameters for reference
        self.latitude = latitude
        self.longitude = longitude
        self.elevation = elevation
        self.temperature = temperature
        self.pressure = pressure
        self.horizon = horizon
    
    def get_sun_position(self, 
                        local_time: datetime,
                        use_center: bool = True) -> Tuple[float, float, datetime, datetime, datetime, datetime]:
        """
        Get the sun's azimuth and elevation at a specific local time, plus solar events.
        
        Args:
            local_time: Local system time (will be converted to UTC)
            use_center: Whether to use center of sun disk (default: True)
        
        Returns:
            Tuple of (azimuth_degrees, elevation_degrees, solar_noon, solar_midnight, next_sunrise, next_sunset)
            
        Note:
            Azimuth is measured from North (0°) clockwise
            Elevation is measured from horizon (0°) to zenith (90°)
            All times are returned as local datetimes
        """
        # Convert local time to UTC (ephem requires UTC)
        if local_time.tzinfo is None:
            # If no timezone info, assume it's already UTC
            utc_time = local_time
        else:
            # Convert to UTC and remove timezone info for ephem
            utc_time = local_time.astimezone(timezone.utc).replace(tzinfo=None)
        
        # Set the observer's date to the UTC time
        self.observer.date = utc_time
        
        # Calculate sun position
        sun = ephem.Sun()
        sun.compute(self.observer)
        
        # Get azimuth and elevation
        if use_center:
            azimuth_rad = sun.az
            elevation_rad = sun.alt
        else:
            # For edge calculations (if needed in future)
            azimuth_rad = sun.az
            elevation_rad = sun.alt
        
        # Convert from radians to degrees
        azimuth_deg = float(ephem.degrees(azimuth_rad)) * 180.0 / ephem.pi
        elevation_deg = float(ephem.degrees(elevation_rad)) * 180.0 / ephem.pi
        
        # Get solar events
        solar_noon = self.observer.next_transit(sun)
        solar_midnight = self.observer.next_antitransit(sun)
        next_sunrise = self.observer.next_rising(sun)
        next_sunset = self.observer.next_setting(sun)
        
        # Convert ephem dates to local datetime
        solar_noon_local = ephem.to_timezone(solar_noon, timezone.utc).astimezone()
        solar_midnight_local = ephem.to_timezone(solar_midnight, timezone.utc).astimezone()
        next_sunrise_local = ephem.to_timezone(next_sunrise, timezone.utc).astimezone()
        next_sunset_local = ephem.to_timezone(next_sunset, timezone.utc).astimezone()
        
        return azimuth_deg, elevation_deg, solar_noon_local, solar_midnight_local, next_sunrise_local, next_sunset_local

    def _get_sun_direction(self, 
                          current_time: datetime,
                          solar_noon: datetime,
                          solar_midnight: datetime) -> str:
        """
        Determine if the sun is currently rising or setting using the exact formula provided.
        
        Args:
            current_time: Current local time
            solar_noon: Next solar noon time (local datetime)
            solar_midnight: Next solar midnight time (local datetime)
        
        Returns:
            "rising" or "setting"
        """
        from datetime import timedelta
        
        cur_date = current_time.date()
        ONE_DAY = timedelta(days=1)
        
        # Find the highest and lowest points on the elevation curve that encompass
        # current time, where it is ok for the current time to be the same as the
        # first of these two points.
        # Note that the ephem solar_midnight event will always come before the ephem
        # solar_noon event for any given date, even if it actually falls on the previous
        # day.
        hi_dttm = solar_noon
        lo_dttm = solar_midnight
        nxt_noon = solar_noon + ONE_DAY
        
        if current_time < lo_dttm:
            # Get previous solar noon
            prev_noon = solar_noon - ONE_DAY
            tl_dttm = prev_noon
            tr_dttm = lo_dttm
        elif current_time < hi_dttm:
            tl_dttm = lo_dttm
            tr_dttm = hi_dttm
        else:
            # Get next solar midnight
            nxt_midnight = solar_midnight + ONE_DAY
            if current_time < nxt_midnight:
                tl_dttm = hi_dttm
                tr_dttm = nxt_midnight
            else:
                tl_dttm = nxt_midnight
                tr_dttm = nxt_noon
        
        # Get elevations at the two time points
        tl_elev = self._get_elevation_at_time(tl_dttm)
        tr_elev = self._get_elevation_at_time(tr_dttm)
        
        rising = tr_elev > tl_elev
        return "rising" if rising else "setting"
    
    def _get_elevation_at_time(self, local_time: datetime) -> float:
        """
        Get the sun's elevation at a specific local time.
        
        Args:
            local_time: Local system time
        
        Returns:
            Elevation in degrees
        """
        # Convert local time to UTC (ephem requires UTC)
        if local_time.tzinfo is None:
            # If no timezone info, assume it's already UTC
            utc_time = local_time
        else:
            # Convert to UTC and remove timezone info for ephem
            utc_time = local_time.astimezone(timezone.utc).replace(tzinfo=None)
        
        # Set the observer's date to the UTC time
        self.observer.date = utc_time
        
        # Calculate sun position
        sun = ephem.Sun()
        sun.compute(self.observer)
        
        # Get elevation and convert from radians to degrees
        elevation_rad = sun.alt
        elevation_deg = float(ephem.degrees(elevation_rad)) * 180.0 / ephem.pi
        
        return elevation_deg

    def get_time_at_elevation(self,
                             target_elevation: float,
                             local_time: datetime,
                             use_center: bool = True) -> Tuple[datetime, datetime]:
        """
        Get the next rising and setting times when the sun reaches a specific elevation.
        
        Args:
            target_elevation: Target elevation in degrees (negative for below horizon)
            local_time: Local system time to start calculation from
            use_center: Whether to use center of sun disk (default: True)
        
        Returns:
            Tuple of (next_rising_time, next_setting_time) as local datetimes
            
        Note:
            Uses ephem's next_rising and next_setting methods with custom horizon
        """
        # Convert local time to UTC (ephem requires UTC)
        if local_time.tzinfo is None:
            # If no timezone info, assume it's already UTC
            utc_time = local_time
        else:
            # Convert to UTC and remove timezone info for ephem
            utc_time = local_time.astimezone(timezone.utc).replace(tzinfo=None)
        
        # Store original horizon setting
        original_horizon = self.observer.horizon
        
        # Set the target elevation as the horizon
        self.observer.horizon = str(target_elevation)
        self.observer.date = utc_time
        
        # Calculate sun position
        sun = ephem.Sun()
        
        try:
            # Get next rising time
            next_rising = self.observer.next_rising(sun, use_center=use_center)
            
            # Get next setting time
            next_setting = self.observer.next_setting(sun, use_center=use_center)
            
            # Convert ephem dates back to Python datetime
            # ephem dates are in UTC
            utc_rising = ephem.to_timezone(next_rising, timezone.utc)
            utc_setting = ephem.to_timezone(next_setting, timezone.utc)
            
            # Convert back to local time
            local_rising = utc_rising.astimezone()
            local_setting = utc_setting.astimezone()
            
            return local_rising, local_setting
            
        finally:
            # Restore original horizon setting
            self.observer.horizon = original_horizon

    def get_max_min_elevations(self, 
                              start_time: datetime,
                              days_ahead: int = 1) -> Tuple[datetime, float, datetime, float]:
        """
        Get the exact times and elevations of maximum and minimum sun elevation.
        
        Args:
            start_time: Local system time to start calculation from
            days_ahead: Number of days to search ahead (default: 1)
        
        Returns:
            Tuple of (max_time, max_elevation, min_time, min_elevation) as local datetimes
            
        Note:
            Uses iterative search with 1-hour increments to find true extrema.
            More accurate than solar noon/midnight for true maximum/minimum elevations.
        """
        from datetime import timedelta
        
        logger = logging.getLogger(__name__)
        
        # Convert to UTC for calculations
        if start_time.tzinfo is None:
            utc_start = start_time
        else:
            utc_start = start_time.astimezone(timezone.utc)
        
        # Calculate search end time
        search_end = utc_start + timedelta(days=days_ahead)
        
        # Initialize tracking variables
        max_elevation = -90.0
        min_elevation = 90.0
        max_time = None
        min_time = None
        
        # Search with 1-hour increments
        current_time = utc_start
        search_increment = timedelta(hours=1)
        
        while current_time <= search_end:
            try:
                # Get elevation at current time
                elevation = self._get_elevation_at_time(current_time)
                
                # Track maximum
                if elevation > max_elevation:
                    max_elevation = elevation
                    max_time = current_time
                
                # Track minimum
                if elevation < min_elevation:
                    min_elevation = elevation
                    min_time = current_time
                    
            except Exception as e:
                logger.debug("Error calculating elevation at %s: %s", current_time, e)
            
            # Move to next hour
            current_time += search_increment
        
        # Convert back to local time
        if max_time is not None:
            max_time_local = max_time.astimezone()
        else:
            max_time_local = start_time
            
        if min_time is not None:
            min_time_local = min_time.astimezone()
        else:
            min_time_local = start_time
        
        logger.debug(
            "Found max elevation %.2f° at %s, min elevation %.2f° at %s",
            max_elevation, max_time_local, min_elevation, min_time_local
        )
        
        return max_time_local, max_elevation, min_time_local, min_elevation

    def find_extremum_precise(self, 
                             start_time: datetime,
                             is_maximum: bool,
                             search_window_hours: int = 2) -> Tuple[datetime, float]:
        """
        Find exact time and elevation of maximum or minimum within a search window.
        
        Args:
            start_time: Local system time to start search from
            is_maximum: True to find maximum, False to find minimum
            search_window_hours: Hours to search before and after start_time
        
        Returns:
            Tuple of (exact_time, exact_elevation) as local datetime
            
        Note:
            Uses binary search with 1-second precision within the search window.
        """
        from datetime import timedelta
        
        logger = logging.getLogger(__name__)
        
        # Convert to UTC
        if start_time.tzinfo is None:
            utc_start = start_time
        else:
            utc_start = start_time.astimezone(timezone.utc)
        
        # Define search window
        window_start = utc_start - timedelta(hours=search_window_hours)
        window_end = utc_start + timedelta(hours=search_window_hours)
        
        # Binary search with 1-second precision
        left = window_start
        right = window_end
        best_time = utc_start
        best_elevation = self._get_elevation_at_time(utc_start)
        
        # Search until we have 1-second precision
        while (right - left).total_seconds() > 1:
            # Calculate midpoints for testing
            mid1 = left + (right - left) / 3
            mid2 = right - (right - left) / 3
            
            # Get elevations at test points
            try:
                elev1 = self._get_elevation_at_time(mid1)
                elev2 = self._get_elevation_at_time(mid2)
            except Exception as e:
                logger.debug("Error in binary search at %s/%s: %s", mid1, mid2, e)
                break
            
            # Update best result
            if is_maximum:
                if elev1 > best_elevation:
                    best_elevation = elev1
                    best_time = mid1
                if elev2 > best_elevation:
                    best_elevation = elev2
                    best_time = mid2
                
                # Narrow search window
                if elev1 > elev2:
                    right = mid2
                else:
                    left = mid1
            else:  # Finding minimum
                if elev1 < best_elevation:
                    best_elevation = elev1
                    best_time = mid1
                if elev2 < best_elevation:
                    best_elevation = elev2
                    best_time = mid2
                
                # Narrow search window
                if elev1 < elev2:
                    right = mid2
                else:
                    left = mid1
        
        # Convert back to local time
        best_time_local = best_time.astimezone()
        
        logger.debug(
            "Found %s elevation %.2f° at %s",
            "maximum" if is_maximum else "minimum", best_elevation, best_time_local
        )
        
        return best_time_local, best_elevation


# Example usage:
# sensor_name, unique_id = create_sensor_attributes("solar elevation")
# # Returns: ("Sol - Solar Elevation", "sol_solar_elevation")
#
# input_name, input_id = create_input_entity_attributes("solar elevation", "panel angle")
# # Returns: ("Sol - Solar Elevation - Panel Angle", "sol_solar_elevation_panel_angle")
#
# input_name, input_id = create_input_entity_attributes("power output", "efficiency")
# # Returns: ("Sol - Power Output - Efficiency", "sol_power_output_efficiency")

# Add your shared calculations and utilities here 