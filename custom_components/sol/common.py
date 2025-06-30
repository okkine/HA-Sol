"""Common utilities and calculations for the Sol integration."""
import re
from typing import Optional, Tuple
from datetime import datetime
from slugify import slugify
import ephem
import pyatmos

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
    Calculate atmospheric pressure from elevation using pyatmos.
    
    Args:
        elevation_meters: Elevation in meters above sea level
    
    Returns:
        Atmospheric pressure in mBar
    """
    try:
        # Use pyatmos to calculate pressure at given elevation
        # Assuming standard atmospheric conditions (15°C, 0% humidity)
        pressure_pa = pyatmos.atmospheric_pressure(elevation_meters)
        
        # Convert from Pa to mBar (1 mBar = 100 Pa)
        pressure_mbar = pressure_pa / 100.0
        
        return pressure_mbar
    except Exception as e:
        # Fallback to standard atmospheric pressure if calculation fails
        # This is a simplified calculation: P = P0 * exp(-elevation/7400)
        # where P0 = 1013.25 mBar (standard sea level pressure)
        pressure_mbar = 1013.25 * (2.71828 ** (-elevation_meters / 7400))
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
        
        # Store parameters for reference
        self.latitude = latitude
        self.longitude = longitude
        self.elevation = elevation
        self.temperature = temperature
        self.pressure = pressure
        self.horizon = horizon
    
    def get_sun_position(self, 
                        local_time: datetime,
                        use_center: bool = True) -> Tuple[float, float]:
        """
        Get the sun's azimuth and elevation at a specific local time.
        
        Args:
            local_time: Local system time (will be converted to UTC)
            use_center: Whether to use center of sun disk (default: True)
        
        Returns:
            Tuple of (azimuth_degrees, elevation_degrees)
            
        Note:
            Azimuth is measured from North (0°) clockwise
            Elevation is measured from horizon (0°) to zenith (90°)
        """
        # Convert local time to UTC (ephem requires UTC)
        utc_time = local_time.astimezone().replace(tzinfo=None)
        
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
        
        return azimuth_deg, elevation_deg


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