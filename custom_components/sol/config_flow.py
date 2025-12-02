"""Config flow for Sol integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.helpers.selector import selector

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, NAME, azimuth_step, elevation_step
from ambiance import Atmosphere

_LOGGER = logging.getLogger(__name__)


class SolConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sol."""

    VERSION = 1

    def __init__(self):
        self._use_system_location = True
        self._custom_location = None
        self._custom_elevation = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            if user_input.get("location_type") == "system":
                existing_entries = self.hass.config_entries.async_entries(DOMAIN)
                if any(entry.data.get("location_name") is None for entry in existing_entries):
                    errors["location_type"] = "system_location_exists"
                else:
                    # Use system location - skip map, proceed to pressure selection
                    self._pending_entry = {
                        "latitude": self.hass.config.latitude,
                        "longitude": self.hass.config.longitude,
                        "elevation": self.hass.config.elevation,
                        "location_name": None
                    }
                    return await self.async_step_pressure()
            else:
                # Custom location selected - proceed to location selection
                return await self.async_step_location()

        # Show the initial form with location type selection
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("location_type", default="system"): vol.In({
                    "system": "Use Home Assistant's location",
                    "custom": "Choose custom location on map"
                }),
            }),
            errors=errors,
        )

    async def async_step_location(self, user_input=None):
        """Handle custom location selection."""
        errors = {}
        
        if user_input is not None:
            try:
                location_data = user_input.get("location")
                elevation = user_input.get("elevation", 1000)
                location_name = user_input.get("location_name")
                if not location_name or not location_name.strip():
                    errors["location_name"] = "required"
                else:
                    existing_entries = self.hass.config_entries.async_entries(DOMAIN)
                    if any(entry.data.get("location_name") == location_name for entry in existing_entries):
                        errors["location_name"] = "location_name_exists"
                    elif location_data and "latitude" in location_data and "longitude" in location_data:
                        # Store pending entry and proceed to pressure selection
                        self._pending_entry = {
                            "latitude": location_data["latitude"],
                            "longitude": location_data["longitude"],
                            "elevation": elevation,
                            "location_name": location_name
                        }
                        return await self.async_step_pressure()
                    else:
                        errors["location"] = "invalid_location"
            except Exception as e:
                errors["base"] = "unknown"

        # Get system location as default
        system_location = {
            "latitude": self.hass.config.latitude,
            "longitude": self.hass.config.longitude
        }
        
        # Show the location selection form with system location as default
        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema({
                vol.Required("location", default=system_location): selector({
                    "location": {}
                }),
                vol.Optional("elevation", default=self.hass.config.elevation): vol.Coerce(int),
                vol.Required("location_name"): str,
            }),
            errors=errors,
        )

    async def async_step_pressure(self, user_input=None):
        """Handle pressure selection (auto/manual)."""
        errors = {}
        pending = getattr(self, "_pending_entry", None)
        if not pending:
            return self.async_abort(reason="no_pending_entry")
        elevation = pending["elevation"]
        if user_input is not None:
            pressure_mode = user_input["pressure_mode"]
            if pressure_mode == "manual":
                return await self.async_step_pressure_manual()
            else:
                # Automatic: calculate using ambiance
                try:
                    pressure_pa = Atmosphere(elevation).pressure[0]
                    pressure_mbar = pressure_pa / 100.0
                    self._pending_entry = {**pending, "pressure_mode": "auto", "pressure_mbar": pressure_mbar}
                    return await self.async_step_update_steps()
                except Exception as e:
                    errors["base"] = "pressure_calc_failed"
        # Show the pressure selection form (no text box)
        return self.async_show_form(
            step_id="pressure",
            data_schema=vol.Schema({
                vol.Required("pressure_mode", default="auto"): vol.In({
                    "auto": "Calculate automatically from elevation",
                    "manual": "Enter manually (mbar)"
                })
            }),
            errors=errors,
        )

    async def async_step_pressure_manual(self, user_input=None):
        """Handle manual pressure entry."""
        errors = {}
        pending = getattr(self, "_pending_entry", None)
        if not pending:
            return self.async_abort(reason="no_pending_entry")
        if user_input is not None:
            try:
                pressure_mbar = float(user_input["pressure_mbar"])
                self._pending_entry = {**pending, "pressure_mode": "manual", "pressure_mbar": pressure_mbar}
                return await self.async_step_update_steps()
            except (ValueError, TypeError):
                errors["pressure_mbar"] = "invalid_pressure"
        return self.async_show_form(
            step_id="pressure_manual",
            data_schema=vol.Schema({
                vol.Required("pressure_mbar"): vol.Coerce(float),
            }),
            errors=errors,
        )

    async def async_step_update_steps(self, user_input=None):
        """Handle azimuth and elevation step configuration."""
        errors = {}
        pending = getattr(self, "_pending_entry", None)
        if not pending:
            return self.async_abort(reason="no_pending_entry")
        if user_input is not None:
            try:
                az_step = float(user_input["azimuth_step"])
                el_step = float(user_input["elevation_step"])
                if az_step < 0.1 or el_step < 0.1:
                    errors["base"] = "step_too_low"
                else:
                    enable_solstice_curve = user_input.get("enable_solstice_curve", False)
                    data = {**pending, "azimuth_step": az_step, "elevation_step": el_step, "enable_solstice_curve": enable_solstice_curve}
                    title = f"{DOMAIN.title()} - Solar Position Sensors" if pending["location_name"] is None else f"{DOMAIN.title()} - {pending['location_name'].title()} - Solar Position Sensors"
                    return self.async_create_entry(title=title, data=data)
            except (ValueError, TypeError):
                errors["base"] = "invalid_step"
        return self.async_show_form(
            step_id="update_steps",
            data_schema=vol.Schema({
                vol.Required("azimuth_step", default=azimuth_step): vol.Coerce(float),
                vol.Required("elevation_step", default=elevation_step): vol.Coerce(float),
                vol.Optional("enable_solstice_curve", default=False): bool,
            }),
            errors=errors,
        )


class SolOptionsFlow(config_entries.OptionsFlow):
    """Handle Sol options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                }
            ),
        )
