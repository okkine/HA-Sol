"""Sol sensor platform."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .common import create_sensor_attributes, create_sun_helper

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sol sensor platform."""
    # Create SunHelper instance from config
    sun_helper = create_sun_helper(config_entry.data)
    
    # Get elevation step from config
    elevation_step = config_entry.data.get("elevation_step", 1.0)
    
    async_add_entities([SolSensor(), SunElevationSensor(sun_helper, elevation_step)], True)


class SolSensor(SensorEntity):
    """Representation of a Sol sensor."""

    def __init__(self) -> None:
        """Initialize the sensor."""
        # Use the common naming convention
        sensor_name, unique_id = create_sensor_attributes("status")
        
        self._attr_name = sensor_name
        self._attr_unique_id = unique_id
        self._attr_native_value = "Unknown"
        self._attr_icon = "mdi:weather-sunny"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._attr_name

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self._attr_native_value

    async def async_update(self) -> None:
        """Fetch new state data for the sensor."""
        # Here you would typically fetch data from your Sol device/service
        # For now, we'll just set a timestamp
        self._attr_native_value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SunElevationSensor(SensorEntity):
    """Representation of a Sun Elevation sensor."""

    def __init__(self, sun_helper, elevation_step) -> None:
        """Initialize the sensor."""
        # Use the common naming convention
        sensor_name, unique_id = create_sensor_attributes("Elevation")
        
        self._attr_name = sensor_name
        self._attr_unique_id = unique_id
        self._attr_native_value = "Unknown"
        self._attr_icon = "mdi:weather-sunny"
        self._attr_native_unit_of_measurement = "°"
        
        # Store the sun helper for calculations
        self.sun_helper = sun_helper
        self.elevation_step = elevation_step

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._attr_name

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        return {
            "latitude": str(self.sun_helper.latitude) if self.sun_helper.latitude is not None else None,
            "longitude": str(self.sun_helper.longitude) if self.sun_helper.longitude is not None else None,
            "elevation_m": round(self.sun_helper.elevation, 1) if self.sun_helper.elevation is not None else None,
            "pressure_mbar": round(self.sun_helper.pressure, 1) if self.sun_helper.pressure is not None else None,
            "temperature_c": round(self.sun_helper.temperature, 1) if self.sun_helper.temperature is not None else None,
            "horizon_deg": round(self.sun_helper.horizon, 1) if self.sun_helper.horizon is not None else None,
            "calculation_time": getattr(self, '_calculation_time', None),
            "azimuth_deg": getattr(self, '_cur_azimuth', None),
            "cur_elevation": getattr(self, '_cur_elevation', None),
            "next_target_elevation": getattr(self, '_next_target_elevation', None),
            "next_update_time": getattr(self, '_next_update_time', None),
            "sun_direction": getattr(self, '_sun_direction', None),
            "solar_noon": getattr(self, '_solar_noon', None),
            "solar_midnight": getattr(self, '_solar_midnight', None),
            "next_sunrise": getattr(self, '_next_sunrise', None),
            "next_sunset": getattr(self, '_next_sunset', None),
            "elevation_step": self.elevation_step,
        }

    async def async_update(self) -> None:
        """Fetch new state data for the sensor."""
        try:
            # Get current local time with timezone
            current_time = dt_util.now()
            cur_azimuth, cur_elevation, solar_noon, solar_midnight, next_sunrise, next_sunset = self.sun_helper.get_sun_position(current_time)
            
            # Determine sun direction
            sun_direction = self.sun_helper._get_sun_direction(current_time, solar_noon, solar_midnight)
            
            # Calculate next target elevation based on direction and step
            if sun_direction == "rising":
                next_target = (round(cur_elevation / self.elevation_step) * self.elevation_step) + self.elevation_step
            else:
                next_target = round(cur_elevation / self.elevation_step) * self.elevation_step - self.elevation_step
            
            # Get the time when sun reaches the next target elevation
            next_rising_time, next_setting_time = self.sun_helper.get_time_at_elevation(next_target, current_time)
            next_update_time = next_rising_time if sun_direction == "rising" else next_setting_time
            
            # Store calculation parameters for attributes
            self._calculation_time = current_time.isoformat()
            self._cur_azimuth = round(cur_azimuth, 2)
            self._sun_direction = sun_direction
            self._cur_elevation = round(cur_elevation, 2)
            self._next_target_elevation = round(next_target, 2)
            self._next_update_time = next_update_time.isoformat()
            self._solar_noon = solar_noon.isoformat()
            self._solar_midnight = solar_midnight.isoformat()
            self._next_sunrise = next_sunrise.isoformat()
            self._next_sunset = next_sunset.isoformat()
            
            # Update sensor with current elevation
            self._attr_native_value = round(cur_elevation, 2)
            
        except Exception as e:
            # Fallback to error state if calculation fails
            _LOGGER.error("Error updating Sol elevation sensor: %s", e)
            self._attr_native_value = None
            self._attr_icon = "mdi:alert" 