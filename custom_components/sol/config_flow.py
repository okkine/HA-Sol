"""Config flow for Sol integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_ELEVATION

from .const import DOMAIN, NAME

_LOGGER = logging.getLogger(__name__)


class SolConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sol."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required("location_mode", default="system"): vol.In(["system", "manual"]),
                        vol.Optional("latitude"): vol.Coerce(float),
                        vol.Optional("longitude"): vol.Coerce(float),
                        vol.Optional("elevation"): vol.Coerce(float),
                        vol.Required("pressure_mode", default="auto"): vol.In(["auto", "manual"]),
                        vol.Optional("pressure"): vol.Coerce(float),
                        vol.Optional("temperature", default=20.0): vol.Coerce(float),
                        vol.Optional("horizon", default=0.0): vol.Coerce(float),
                    }
                ),
                description_placeholders={
                    "note": "Choose whether to use system defaults or manual settings for location and pressure."
                }
            )

        # Validate coordinates if manual mode is selected
        errors = {}
        if user_input.get("location_mode") == "manual":
            lat = user_input.get("latitude")
            lon = user_input.get("longitude")
            if lat is None or lon is None:
                errors["base"] = "coordinates_required"
            elif not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                errors["base"] = "invalid_coordinates"

        # Validate pressure if manual mode is selected
        if user_input.get("pressure_mode") == "manual" and user_input.get("pressure") is None:
            errors["base"] = "pressure_required"

        if errors:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required("location_mode", default=user_input.get("location_mode", "system")): vol.In(["system", "manual"]),
                        vol.Optional("latitude", default=user_input.get("latitude")): vol.Coerce(float),
                        vol.Optional("longitude", default=user_input.get("longitude")): vol.Coerce(float),
                        vol.Optional("elevation", default=user_input.get("elevation")): vol.Coerce(float),
                        vol.Required("pressure_mode", default=user_input.get("pressure_mode", "auto")): vol.In(["auto", "manual"]),
                        vol.Optional("pressure", default=user_input.get("pressure")): vol.Coerce(float),
                        vol.Optional("temperature", default=user_input.get("temperature", 20.0)): vol.Coerce(float),
                        vol.Optional("horizon", default=user_input.get("horizon", 0.0)): vol.Coerce(float),
                    }
                ),
                errors=errors,
                description_placeholders={
                    "note": "Choose whether to use system defaults or manual settings for location and pressure."
                }
            )

        # Get system defaults
        system_lat = self.hass.config.latitude
        system_lon = self.hass.config.longitude
        system_elevation = self.hass.config.elevation

        # Store configuration with proper defaults
        info = {
            "location_mode": user_input.get("location_mode", "system"),
            "latitude": user_input.get("latitude") if user_input.get("location_mode") == "manual" else system_lat,
            "longitude": user_input.get("longitude") if user_input.get("location_mode") == "manual" else system_lon,
            "elevation": user_input.get("elevation") if user_input.get("location_mode") == "manual" else system_elevation,
            "pressure_mode": user_input.get("pressure_mode", "auto"),
            "pressure": user_input.get("pressure") if user_input.get("pressure_mode") == "manual" else None,
            "temperature": user_input.get("temperature", 20.0),
            "horizon": user_input.get("horizon", 0.0),
        }

        return self.async_create_entry(title=NAME, data=info)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth.""" 