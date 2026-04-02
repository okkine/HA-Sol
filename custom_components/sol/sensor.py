"""Sensors for Sol integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .sensor_classes import AzimuthSensor, ElevationSensor, RiseSensor, SetSensor, TransitSensor, AntitransitSensor, SolarDeclinationNormalizedSensor, MoonPhaseSensor, PhaseAngleSensor, ParallacticAngleSensor
from .utils import get_body
from .const import eph
from .config_store import get_config_entry_data

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialize Sol config entry."""
    try:
        bodies = config_entry.data.get("bodies", ["sun"])
        config_data = get_config_entry_data(config_entry.entry_id) or {}

        sensors = []
        for body_key in bodies:
            body = get_body(body_key, eph)
            if body is None:
                _LOGGER.error(f"Invalid body_key '{body_key}' for entry {config_entry.entry_id}, skipping")
                continue

            if config_data.get(f"{body_key}_enable_az_el", True):
                sensors.append(AzimuthSensor(hass, config_entry.entry_id, body, body_key))
                sensors.append(ElevationSensor(hass, config_entry.entry_id, body, body_key))
            if config_data.get(f"{body_key}_enable_rise_set", False):
                sensors.append(RiseSensor(hass, config_entry.entry_id, body, body_key))
                sensors.append(SetSensor(hass, config_entry.entry_id, body, body_key))
            if config_data.get(f"{body_key}_enable_transit", False):
                sensors.append(TransitSensor(hass, config_entry.entry_id, body, body_key))
                sensors.append(AntitransitSensor(hass, config_entry.entry_id, body, body_key))
            if body_key == "moon" and config_data.get("moon_enable_phase", False):
                sensors.append(MoonPhaseSensor(hass, config_entry.entry_id, body, body_key))
            if body_key == "moon" and config_data.get("moon_enable_phase_angle", False):
                sensors.append(PhaseAngleSensor(hass, config_entry.entry_id, body, body_key))
            if body_key == "moon" and config_data.get("moon_enable_parallactic_angle", False):
                sensors.append(ParallacticAngleSensor(hass, config_entry.entry_id, body, body_key))

        if config_data.get("enable_declination_normalized", False):
            try:
                sensors.append(SolarDeclinationNormalizedSensor(
                    hass,
                    config_entry.entry_id,
                    enabled_by_default=True,
                ))
            except Exception as e:
                _LOGGER.error(f"Failed to create SolarDeclinationNormalizedSensor for entry {config_entry.entry_id}: {e}", exc_info=True)

        async_add_entities(sensors)
    except Exception as e:
        _LOGGER.error(f"Failed to create sensors for entry {config_entry.entry_id}: {e}", exc_info=True)
