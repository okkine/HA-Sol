"""Sol sensor platform."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from ..const import DOMAIN, CONF_NAME
from ..helpers import create_sensor_attributes


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sol sensor platform."""
    name = config_entry.data[CONF_NAME]
    
    async_add_entities([SolSensor(name)], True)


class SolSensor(SensorEntity):
    """Representation of a Sol sensor."""

    def __init__(self, name: str) -> None:
        """Initialize the sensor."""
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