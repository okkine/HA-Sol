"""Config flow for Sol integration."""

from __future__ import annotations

import logging
import math
from typing import Any

import voluptuous as vol
from homeassistant.helpers.selector import selector
from homeassistant.helpers import config_validation as cv

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, NAME, AZIMUTH_STEP_VALUE_DEFAULT, ELEVATION_STEP_VALUE_DEFAULT
from ambiance import Atmosphere

_LOGGER = logging.getLogger(__name__)

# Bodies offered in the config flow body selection step (not exposed to users elsewhere)
AVAILABLE_BODIES = ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto"]


class SkyfieldTestConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sol."""

    VERSION = 1

    def __init__(self):
        self._pending_entry = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            if user_input.get("location_type") == "system":
                existing_entries = self.hass.config_entries.async_entries(DOMAIN)
                if any(entry.data.get("location_name") is None for entry in existing_entries):
                    errors["location_type"] = "system_location_exists"
                else:
                    # Use system location - store pending entry and proceed to temperature/pressure step
                    # Clamp latitude to avoid exact pole issues (90°/-90°)
                    system_latitude = self.hass.config.latitude
                    if system_latitude > 89.9999:
                        system_latitude = 89.9999
                    elif system_latitude < -89.9999:
                        system_latitude = -89.9999
                    
                    self._pending_entry = {
                        "latitude": system_latitude,
                        "longitude": self.hass.config.longitude,
                        "elevation": self.hass.config.elevation,
                        "location_name": None
                    }
                    return await self.async_step_temperature_pressure()
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
                elevation = user_input.get("elevation", self.hass.config.elevation)
                location_name = user_input.get("location_name")
                
                if not location_name or not location_name.strip():
                    errors["location_name"] = "required"
                else:
                    existing_entries = self.hass.config_entries.async_entries(DOMAIN)
                    if any(entry.data.get("location_name") == location_name for entry in existing_entries):
                        errors["location_name"] = "location_name_exists"
                    elif location_data and "latitude" in location_data and "longitude" in location_data:
                        # Clamp latitude to avoid exact pole issues (90°/-90°)
                        latitude = location_data["latitude"]
                        if latitude > 89.9999:
                            latitude = 89.9999
                        elif latitude < -89.9999:
                            latitude = -89.9999
                        
                        # Store pending entry and proceed to temperature/pressure step
                        self._pending_entry = {
                            "latitude": latitude,
                            "longitude": location_data["longitude"],
                            "elevation": elevation,
                            "location_name": location_name
                        }
                        return await self.async_step_temperature_pressure()
                    else:
                        errors["location"] = "invalid_location"
            except Exception as e:
                _LOGGER.error(f"Error in location step: {e}")
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
    
    async def async_step_temperature_pressure(self, user_input=None):
        """Handle temperature/pressure mode selection."""
        errors = {}
        pending = getattr(self, "_pending_entry", None)
        if not pending:
            return self.async_abort(reason="no_pending_entry")
        
        if user_input is not None:
            temperature_mode = user_input["temperature_mode"]
            pressure_mode = user_input["pressure_mode"]
            azimuth_step_value = user_input.get("azimuth_step_value", AZIMUTH_STEP_VALUE_DEFAULT)
            elevation_step_value = user_input.get("elevation_step_value", ELEVATION_STEP_VALUE_DEFAULT)
            
            # Validate step values:
            # - positive integers are allowed (1, 2, 3, ...)
            # - fractional values in (0, 1) must evenly divide 1 (0.5, 0.25, 0.2, 0.1, ...)
            az_int_like = math.isclose(azimuth_step_value, round(azimuth_step_value), abs_tol=1e-9)
            az_fraction_ok = (
                0 < azimuth_step_value < 1
                and math.isclose((1.0 / azimuth_step_value), round(1.0 / azimuth_step_value), abs_tol=1e-9)
            )
            if azimuth_step_value <= 0 or (not az_int_like and not az_fraction_ok):
                errors["azimuth_step_value"] = "min_step_value"
            el_int_like = math.isclose(elevation_step_value, round(elevation_step_value), abs_tol=1e-9)
            el_fraction_ok = (
                0 < elevation_step_value < 1
                and math.isclose((1.0 / elevation_step_value), round(1.0 / elevation_step_value), abs_tol=1e-9)
            )
            if elevation_step_value <= 0 or (not el_int_like and not el_fraction_ok):
                errors["elevation_step_value"] = "min_step_value"
            
            if not errors:
                # Calculate pressure if auto mode
                pressure_mbar = None
                if pressure_mode == "auto":
                    try:
                        elevation = pending["elevation"]
                        pressure_pa = Atmosphere(elevation).pressure[0]
                        pressure_mbar = pressure_pa / 100.0
                    except Exception as e:
                        _LOGGER.error(f"Error calculating pressure from elevation: {e}")
                        errors["base"] = "pressure_calc_failed"
                
                if not errors:
                    # Store all selections
                    entry_data = {
                        **pending,
                        "temperature_mode": temperature_mode,
                        "pressure_mode": pressure_mode,
                        "azimuth_step_value": azimuth_step_value,
                        "elevation_step_value": elevation_step_value
                    }
                    if pressure_mbar is not None:
                        entry_data["pressure_mbar"] = pressure_mbar
                    self._pending_entry = entry_data
                
                # Route to appropriate next step
                if temperature_mode == "manual":
                    return await self.async_step_temperature_manual()
                elif temperature_mode == "sensor":
                    return await self.async_step_temperature_sensor()
                elif pressure_mode == "manual":
                    return await self.async_step_pressure_manual()
                else:
                    # Both are auto/estimator - proceed to body selection
                    return await self.async_step_bodies()
        
        # Show the temperature/pressure mode selection form
        return self.async_show_form(
            step_id="temperature_pressure",
            data_schema=vol.Schema({
                vol.Required("azimuth_step_value", default=AZIMUTH_STEP_VALUE_DEFAULT): vol.Coerce(float),
                vol.Required("elevation_step_value", default=ELEVATION_STEP_VALUE_DEFAULT): vol.Coerce(float),
                vol.Required("temperature_mode", default="estimator"): vol.In({
                    "manual": "Enter temperature manually (°C)",
                    "estimator": "Calculate automatically (from location/date)",
                    "sensor": "Use temperature sensor"
                }),
                vol.Required("pressure_mode", default="auto"): vol.In({
                    "auto": "Calculate from elevation",
                    "manual": "Enter pressure manually (mbar)"
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
                if pressure_mbar < 0:
                    errors["pressure_mbar"] = "invalid_pressure"
                else:
                    self._pending_entry = {**pending, "pressure_mode": "manual", "pressure_mbar": pressure_mbar}
                    # Check if temperature also needs input (check if value is already set)
                    if "temperature_C" not in self._pending_entry and "temperature_entity_id" not in self._pending_entry:
                        # Temperature value not set yet - check mode to route
                        if self._pending_entry.get("temperature_mode") == "manual":
                            return await self.async_step_temperature_manual()
                        elif self._pending_entry.get("temperature_mode") == "sensor":
                            return await self.async_step_temperature_sensor()
                    # Temperature already set or not needed - proceed to body selection
                    return await self.async_step_bodies()
            except (ValueError, TypeError):
                errors["pressure_mbar"] = "invalid_pressure"
        
        return self.async_show_form(
            step_id="pressure_manual",
            data_schema=vol.Schema({
                vol.Required("pressure_mbar"): vol.Coerce(float),
            }),
            errors=errors,
        )
    
    async def async_step_temperature_sensor(self, user_input=None):
        """Handle temperature sensor selection."""
        errors = {}
        pending = getattr(self, "_pending_entry", None)
        if not pending:
            return self.async_abort(reason="no_pending_entry")
        
        if user_input is not None:
            temperature_entity_id = user_input.get("temperature_entity_id")
            if not temperature_entity_id:
                errors["temperature_entity_id"] = "required"
            else:
                # Verify entity exists and is a sensor
                state = self.hass.states.get(temperature_entity_id)
                if state is None:
                    errors["temperature_entity_id"] = "entity_not_found"
                else:
                    self._pending_entry = {**pending, "temperature_mode": "sensor", "temperature_entity_id": temperature_entity_id}
                    # Check if pressure also needs input (check if value is already set)
                    if "pressure_mbar" not in self._pending_entry and self._pending_entry.get("pressure_mode") == "manual":
                        return await self.async_step_pressure_manual()
                    # Pressure already set or not needed - proceed to body selection
                    return await self.async_step_bodies()
        
        return self.async_show_form(
            step_id="temperature_sensor",
            data_schema=vol.Schema({
                vol.Required("temperature_entity_id"): selector({
                    "entity": {
                        "domain": ["sensor", "weather"]
                    }
                }),
            }),
            errors=errors,
        )
    
    async def async_step_temperature_manual(self, user_input=None):
        """Handle manual temperature entry."""
        errors = {}
        pending = getattr(self, "_pending_entry", None)
        if not pending:
            return self.async_abort(reason="no_pending_entry")
        
        if user_input is not None:
            try:
                temperature_C = float(user_input["temperature_C"])
                if temperature_C < -100 or temperature_C > 100:
                    errors["temperature_C"] = "invalid_temperature"
                else:
                    self._pending_entry = {**pending, "temperature_mode": "manual", "temperature_C": temperature_C}
                    # Check if pressure also needs input (check if value is already set)
                    if "pressure_mbar" not in self._pending_entry and self._pending_entry.get("pressure_mode") == "manual":
                        return await self.async_step_pressure_manual()
                    # Pressure already set or not needed - proceed to body selection
                    return await self.async_step_bodies()
            except (ValueError, TypeError):
                errors["temperature_C"] = "invalid_temperature"
        
        return self.async_show_form(
            step_id="temperature_manual",
            data_schema=vol.Schema({
                vol.Required("temperature_C"): vol.Coerce(float),
            }),
            errors=errors,
        )
    
    async def async_step_bodies(self, user_input=None):
        """Handle body selection."""
        errors = {}
        pending = getattr(self, "_pending_entry", None)
        if not pending:
            return self.async_abort(reason="no_pending_entry")
        
        if user_input is not None:
            # Extract selected bodies from input (they come as individual boolean fields with capitalized labels)
            # Map capitalized labels back to lowercase body keys
            body_mapping = {
                "Sun": "sun", "Moon": "moon", "Mercury": "mercury", "Venus": "venus",
                "Mars": "mars", "Jupiter": "jupiter", "Saturn": "saturn",
                "Uranus": "uranus", "Neptune": "neptune", "Pluto": "pluto"
            }
            selected_bodies = []
            for body_label, body_key in body_mapping.items():
                if user_input.get(body_label, False):
                    selected_bodies.append(body_key)
            
            if not selected_bodies:
                errors["base"] = "at_least_one_body_required"
            else:
                enable_declination_normalized = user_input.get("enable_declination_normalized", False)
                self._pending_entry = {**pending, "bodies": selected_bodies, "enable_declination_normalized": enable_declination_normalized}
                return await self.async_step_body_sensors()
        
        # Build schema with individual checkbox fields in the correct order
        # Sun default checked, Moon unchecked, then planets
        body_order = [
            ("sun", "Sun", True),
            ("moon", "Moon", False),
            ("mercury", "Mercury", False),
            ("venus", "Venus", False),
            ("mars", "Mars", False),
            ("jupiter", "Jupiter", False),
            ("saturn", "Saturn", False),
            ("uranus", "Uranus", False),
            ("neptune", "Neptune", False),
            ("pluto", "Pluto", False),
        ]
        
        schema_dict = {}
        for body_key, body_label, default_checked in body_order:
            # Use body_label (capitalized) as the field key for display
            schema_dict[vol.Optional(body_label, default=default_checked)] = bool
        
        # Add enable_declination_normalized option
        schema_dict[vol.Optional("enable_declination_normalized", default=False)] = bool
        
        return self.async_show_form(
            step_id="bodies",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )
    
    async def async_step_body_sensors(self, user_input=None):
        """Handle per-body sensor selection."""
        pending = getattr(self, "_pending_entry", None)
        if not pending:
            return self.async_abort(reason="no_pending_entry")

        selected_bodies = pending.get("bodies", ["sun"])

        if user_input is not None:
            for body_key in selected_bodies:
                pending[f"{body_key}_enable_az_el"] = user_input.get(f"{body_key}_enable_az_el", True)
                pending[f"{body_key}_enable_rise_set"] = user_input.get(f"{body_key}_enable_rise_set", False)
                pending[f"{body_key}_enable_transit"] = user_input.get(f"{body_key}_enable_transit", False)
                if body_key == "moon":
                    pending["moon_enable_phase"] = user_input.get("moon_enable_phase", False)
                    pending["moon_enable_phase_angle"] = user_input.get("moon_enable_phase_angle", False)
                    pending["moon_enable_parallactic_angle"] = user_input.get("moon_enable_parallactic_angle", False)
                    pending["moon_naming_convention"] = user_input.get("moon_naming_convention", "none")
            self._pending_entry = pending
            return await self._create_entry()

        schema_dict = {}
        for body_key in selected_bodies:
            schema_dict[vol.Optional(f"{body_key}_enable_az_el", default=True)] = bool
            schema_dict[vol.Optional(f"{body_key}_enable_rise_set", default=False)] = bool
            schema_dict[vol.Optional(f"{body_key}_enable_transit", default=False)] = bool
            if body_key == "moon":
                schema_dict[vol.Optional("moon_enable_phase", default=False)] = bool
                schema_dict[vol.Optional("moon_enable_phase_angle", default=False)] = bool
                schema_dict[vol.Optional("moon_enable_parallactic_angle", default=False)] = bool
                schema_dict[vol.Optional("moon_naming_convention", default="none")] = vol.In({
                    "none": "None",
                    "north_american": "North American",
                    "pagan": "Pagan",
                })

        return self.async_show_form(
            step_id="body_sensors",
            data_schema=vol.Schema(schema_dict),
        )

    async def _create_entry(self):
        """Create config entry from pending entry data."""
        pending = getattr(self, "_pending_entry", None)
        if not pending:
            return self.async_abort(reason="no_pending_entry")
        
        # Ensure bodies is set (default to sun if missing for backward compatibility)
        if "bodies" not in pending:
            pending["bodies"] = ["sun"]
        
        location_name = pending.get("location_name")
        if location_name is None:
            title = f"{NAME} - System Location"
        else:
            title = f"{NAME} - {location_name.title()}"
        
        return self.async_create_entry(title=title, data=pending)
    
    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "SkyfieldTestOptionsFlow":
        """Create the options flow."""
        return SkyfieldTestOptionsFlow()


class SkyfieldTestOptionsFlow(config_entries.OptionsFlow):
    """Handle Sol options."""
    
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors = {}
        
        if user_input is not None:
            temperature_mode = user_input.get("temperature_mode", "estimator")
            pressure_mode = user_input.get("pressure_mode", "auto")
            azimuth_step_value = user_input.get("azimuth_step_value")
            elevation_step_value = user_input.get("elevation_step_value")
            
            # Validate step values:
            # - positive integers are allowed (1, 2, 3, ...)
            # - fractional values in (0, 1) must evenly divide 1 (0.5, 0.25, 0.2, 0.1, ...)
            if azimuth_step_value is not None:
                az_int_like = math.isclose(azimuth_step_value, round(azimuth_step_value), abs_tol=1e-9)
                az_fraction_ok = (
                    0 < azimuth_step_value < 1
                    and math.isclose((1.0 / azimuth_step_value), round(1.0 / azimuth_step_value), abs_tol=1e-9)
                )
                if azimuth_step_value <= 0 or (not az_int_like and not az_fraction_ok):
                    errors["azimuth_step_value"] = "min_step_value"
            if elevation_step_value is not None:
                el_int_like = math.isclose(elevation_step_value, round(elevation_step_value), abs_tol=1e-9)
                el_fraction_ok = (
                    0 < elevation_step_value < 1
                    and math.isclose((1.0 / elevation_step_value), round(1.0 / elevation_step_value), abs_tol=1e-9)
                )
                if elevation_step_value <= 0 or (not el_int_like and not el_fraction_ok):
                    errors["elevation_step_value"] = "min_step_value"
            
            # Validate based on mode
            if temperature_mode == "manual":
                temperature_C = user_input.get("temperature_C")
                if temperature_C is None:
                    errors["temperature_C"] = "required"
                elif temperature_C < -100 or temperature_C > 100:
                    errors["temperature_C"] = "invalid_temperature"
            elif temperature_mode == "sensor":
                temperature_entity_id = user_input.get("temperature_entity_id")
                if not temperature_entity_id:
                    errors["temperature_entity_id"] = "required"
                else:
                    # Verify entity exists
                    state = self.hass.states.get(temperature_entity_id)
                    if state is None:
                        errors["temperature_entity_id"] = "entity_not_found"
            
            # Calculate or validate pressure based on mode
            if pressure_mode == "manual":
                pressure_mbar = user_input.get("pressure_mbar")
                if pressure_mbar is None:
                    errors["pressure_mbar"] = "required"
                elif pressure_mbar < 0:
                    errors["pressure_mbar"] = "invalid_pressure"
            else:  # auto mode
                try:
                    elevation = self.config_entry.data.get("elevation", self.hass.config.elevation)
                    pressure_pa = Atmosphere(elevation).pressure[0]
                    pressure_mbar = pressure_pa / 100.0
                except Exception as e:
                    _LOGGER.error(f"Error calculating pressure from elevation: {e}")
                    errors["base"] = "pressure_calc_failed"
                    pressure_mbar = None
            
            # Extract selected bodies from checkbox inputs
            # Map capitalized labels back to lowercase body keys
            body_mapping = {
                "Sun": "sun", "Moon": "moon", "Mercury": "mercury", "Venus": "venus",
                "Mars": "mars", "Jupiter": "jupiter", "Saturn": "saturn",
                "Uranus": "uranus", "Neptune": "neptune", "Pluto": "pluto"
            }
            selected_bodies = []
            for body_label, body_key in body_mapping.items():
                if user_input.get(body_label, False):
                    selected_bodies.append(body_key)
            
            if not selected_bodies:
                errors["base"] = "at_least_one_body_required"
            
            if not errors:
                # Build the cleaned-up data dict that will eventually be saved
                current_data = dict(self.config_entry.data)
                current_data.update(user_input)

                # Clamp latitude to avoid exact pole issues (90°/-90°)
                latitude = current_data.get("latitude")
                if latitude is not None:
                    if latitude > 89.9999:
                        current_data["latitude"] = 89.9999
                    elif latitude < -89.9999:
                        current_data["latitude"] = -89.9999

                # Store selected bodies
                current_data["bodies"] = selected_bodies

                # Store enable_declination_normalized option
                enable_declination_normalized = user_input.get("enable_declination_normalized", False)
                current_data["enable_declination_normalized"] = enable_declination_normalized

                # Store calculated pressure if auto mode
                if pressure_mode == "auto" and pressure_mbar is not None:
                    current_data["pressure_mbar"] = pressure_mbar

                # Remove fields that don't apply to the selected modes
                if temperature_mode != "manual":
                    current_data.pop("temperature_C", None)
                if temperature_mode != "sensor":
                    current_data.pop("temperature_entity_id", None)

                # Remove body checkbox fields (they're not needed in stored data)
                body_labels = ["Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"]
                body_keys = ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto"]
                for label in body_labels:
                    current_data.pop(label, None)
                for key in body_keys:
                    current_data.pop(key, None)

                # Stash and proceed to per-body sensor selection
                self._pending_options = current_data
                return await self.async_step_body_sensors()
        
        # Get current values
        current_latitude = self.config_entry.data.get("latitude", self.hass.config.latitude)
        current_longitude = self.config_entry.data.get("longitude", self.hass.config.longitude)
        current_azimuth_step = self.config_entry.data.get("azimuth_step_value", AZIMUTH_STEP_VALUE_DEFAULT)
        current_elevation_step = self.config_entry.data.get("elevation_step_value", ELEVATION_STEP_VALUE_DEFAULT)
        current_temp_mode = self.config_entry.data.get("temperature_mode", "estimator")
        current_pressure_mode = self.config_entry.data.get("pressure_mode", "auto")
        current_temperature_C = self.config_entry.data.get("temperature_C")
        current_pressure = self.config_entry.data.get("pressure_mbar")
        current_entity = self.config_entry.data.get("temperature_entity_id")
        current_bodies = self.config_entry.data.get("bodies", ["sun"])
        current_enable_declination_normalized = self.config_entry.data.get("enable_declination_normalized", False)
        
        # Build schema - show all fields, but make them conditional based on mode
        # User can change mode and fill in the appropriate field
        schema_dict = {
            vol.Required("latitude", default=current_latitude): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
            vol.Required("longitude", default=current_longitude): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
            vol.Required("azimuth_step_value", default=current_azimuth_step): vol.Coerce(float),
            vol.Required("elevation_step_value", default=current_elevation_step): vol.Coerce(float),
            vol.Required("temperature_mode", default=current_temp_mode): vol.In({
                "manual": "Enter temperature manually (°C)",
                "estimator": "Calculate automatically (from location/date)",
                "sensor": "Use temperature sensor"
            }),
            vol.Required("pressure_mode", default=current_pressure_mode): vol.In({
                "auto": "Calculate from elevation",
                "manual": "Enter pressure manually (mbar)"
            })
        }
        
        # Add body selection checkboxes
        body_order = [
            ("sun", "Sun"),
            ("moon", "Moon"),
            ("mercury", "Mercury"),
            ("venus", "Venus"),
            ("mars", "Mars"),
            ("jupiter", "Jupiter"),
            ("saturn", "Saturn"),
            ("uranus", "Uranus"),
            ("neptune", "Neptune"),
            ("pluto", "Pluto"),
        ]
        for body_key, body_label in body_order:
            # Use body_label (capitalized) as the field key, but store body_key (lowercase) in data
            schema_dict[vol.Optional(body_label, default=body_key in current_bodies)] = bool
        
        # Add enable_declination_normalized option
        schema_dict[vol.Optional("enable_declination_normalized", default=current_enable_declination_normalized)] = bool
        
        # Determine which mode we're using (from user_input if available, otherwise current)
        effective_temp_mode = current_temp_mode
        effective_pressure_mode = current_pressure_mode
        if user_input is not None:
            effective_temp_mode = user_input.get("temperature_mode", current_temp_mode)
            effective_pressure_mode = user_input.get("pressure_mode", current_pressure_mode)
        
        # Only show temperature field when mode requires it
        if effective_temp_mode == "manual":
            schema_dict[vol.Optional("temperature_C", default=current_temperature_C)] = vol.Coerce(float)
        
        # Only show pressure field when mode requires it
        if effective_pressure_mode == "manual":
            schema_dict[vol.Optional("pressure_mbar", default=current_pressure)] = vol.Coerce(float)
        
        # Only show entity field when mode requires it
        if effective_temp_mode == "sensor":
            schema_dict[vol.Optional("temperature_entity_id", default=current_entity)] = selector({
                "entity": {
                    "domain": ["sensor", "weather"]
                }
            })
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def async_step_body_sensors(self, user_input=None):
        """Handle per-body sensor selection for the options flow."""
        pending = getattr(self, "_pending_options", None)
        if pending is None:
            return self.async_abort(reason="no_pending_entry")

        selected_bodies = pending.get("bodies", ["sun"])

        if user_input is not None:
            # Merge sensor flags into pending data
            for body_key in selected_bodies:
                pending[f"{body_key}_enable_az_el"] = user_input.get(f"{body_key}_enable_az_el", True)
                pending[f"{body_key}_enable_rise_set"] = user_input.get(f"{body_key}_enable_rise_set", False)
                pending[f"{body_key}_enable_transit"] = user_input.get(f"{body_key}_enable_transit", False)
                if body_key == "moon":
                    pending["moon_enable_phase"] = user_input.get("moon_enable_phase", False)
                    pending["moon_enable_phase_angle"] = user_input.get("moon_enable_phase_angle", False)
                    pending["moon_enable_parallactic_angle"] = user_input.get("moon_enable_parallactic_angle", False)
                    pending["moon_naming_convention"] = user_input.get("moon_naming_convention", "none")

            # Save and reload
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=pending,
            )

            from .config_store import store_config_entry_data
            store_config_entry_data(self.config_entry.entry_id, pending)

            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.config_entry.entry_id)
            )

            return self.async_create_entry(title="", data={})

        # Build form — defaults from currently saved config
        saved = dict(self.config_entry.data)
        schema_dict = {}
        for body_key in selected_bodies:
            schema_dict[vol.Optional(f"{body_key}_enable_az_el", default=saved.get(f"{body_key}_enable_az_el", True))] = bool
            schema_dict[vol.Optional(f"{body_key}_enable_rise_set", default=saved.get(f"{body_key}_enable_rise_set", False))] = bool
            schema_dict[vol.Optional(f"{body_key}_enable_transit", default=saved.get(f"{body_key}_enable_transit", False))] = bool
            if body_key == "moon":
                schema_dict[vol.Optional("moon_enable_phase", default=saved.get("moon_enable_phase", False))] = bool
                schema_dict[vol.Optional("moon_enable_phase_angle", default=saved.get("moon_enable_phase_angle", False))] = bool
                schema_dict[vol.Optional("moon_enable_parallactic_angle", default=saved.get("moon_enable_parallactic_angle", False))] = bool
                schema_dict[vol.Optional("moon_naming_convention", default=saved.get("moon_naming_convention", "none"))] = vol.In({
                    "none": "None",
                    "north_american": "North American",
                    "pagan": "Pagan",
                })

        return self.async_show_form(
            step_id="body_sensors",
            data_schema=vol.Schema(schema_dict),
        )
