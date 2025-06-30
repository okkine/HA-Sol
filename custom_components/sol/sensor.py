"""Sol sensor platform."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

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

    async def async_update(self) -> None:
        """Fetch new state data for the sensor."""
        try:
            # Get current sun position
            current_time = datetime.now()
            azimuth, elevation = self.sun_helper.get_sun_position(current_time)
            
            # Update sensor with current elevation
            self._attr_native_value = round(elevation, 2)
            
        except Exception as e:
            # Fallback to error state if calculation fails
            self._attr_native_value = "Error"
            self._attr_icon = "mdi:alert" 