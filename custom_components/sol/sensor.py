"""Sol sensor platform."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .common import create_sensor_attributes, create_sun_helper


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sol sensor platform."""
    # Create SunHelper instance from config
    sun_helper = create_sun_helper(config_entry.data)
    
    async_add_entities([SolSensor(), SunElevationSensor(sun_helper)], True)


class SolSensor(SensorEntity):
    """Representation of a Sol sensor."""

    def __init__(self) -> None:
        """Initialize the sensor."""
        # Use the common naming convention
        sensor_name, unique_id = create_sensor_attributes("status")
        
        self._attr_name = sensor_name
        self._attr_unique_id = unique_id
        self._attr_native_value = "Unknown"
        self._attr_icon = "mdi:solar-power"

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

    def __init__(self, sun_helper) -> None:
        """Initialize the sensor."""
        # Use the common naming convention
        sensor_name, unique_id = create_sensor_attributes("Elevation")
        
        self._attr_name = sensor_name
        self._attr_unique_id = unique_id
        self._attr_native_value = "Unknown"
        self._attr_icon = "mdi:solar-power"
        self._attr_unit_of_measurement = "°"
        
        # Store the sun helper for calculations
        self.sun_helper = sun_helper

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
            "azimuth_deg": getattr(self, '_azimuth', None),
        }

    async def async_update(self) -> None:
        """Fetch new state data for the sensor."""
        try:
            # Get current local time with timezone
            current_time = dt_util.now()
            azimuth, elevation = self.sun_helper.get_sun_position(current_time)
            
            # Store calculation parameters for attributes
            self._calculation_time = current_time.isoformat()
            self._azimuth = round(azimuth, 2)
            
            # Update sensor with current elevation
            self._attr_native_value = round(elevation, 2)
            
        except Exception as e:
            # Fallback to error state if calculation fails
            self._attr_native_value = "Error"
            self._attr_icon = "mdi:alert" 