"""Sol sensor platform."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import slugify
from homeassistant.helpers.entity import Entity

from .common import create_sensor_attributes, create_sun_helper

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sol sensor platform."""
    _LOGGER.info("Setting up Sol sensors with config: %s", config_entry.data)
    
    # Create SunHelper instance from config
    sun_helper = create_sun_helper(config_entry.data)
    
    # Get elevation step from config
    elevation_step = config_entry.data.get("elevation_step", 1.0)
    
    # Create sensor list - always include all sensors
    sensors = [
        SolSensor(), 
        SunElevationSensor(sun_helper, elevation_step),
        SunMaximumElevationSensor(sun_helper),
        SunMinimumElevationSensor(sun_helper)
    ]
    
    _LOGGER.info("Total sensors to add: %d", len(sensors))
    async_add_entities(sensors, True)


async def async_unload_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> bool:
    """Unload a config entry."""
    return True


async def async_reload_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_update_options(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Update options."""
    await hass.config_entries.async_reload(config_entry.entry_id)


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


class BaseSolEntity(Entity):
    """Base class for Sol entities handling common scheduling and update logic."""
    
    def __init__(self, base_name, unique_suffix, name="Sol"):
        """
        Initialize base entity with consistent naming conventions.
        
        Args:
            base_name: The descriptive part of the name
            unique_suffix: The unique identifier suffix
            name: The prefix for entity names
        """
        formatted_name = f"{name} - {base_name}"
        self._attr_name = ' '.join(word.capitalize() for word in formatted_name.split())
        self._attr_unique_id = f"sol_{slugify(unique_suffix)}"
        self._unsub_update = None
        self._attr_available = False  # Start as unavailable until first update
        self._next_update = None

    @property
    def next_change(self):
        """Return the next scheduled update time."""
        return self._next_update

    @property
    def should_poll(self):
        """Disable polling for this entity."""
        return False

    async def async_will_remove_from_hass(self):
        """Cancel next update when entity is removed."""
        self.cancel_scheduled_update()

    def cancel_scheduled_update(self):
        """Cancel any scheduled updates."""
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None

    async def async_added_to_hass(self):
        """Call when entity is added to hass."""
        await super().async_added_to_hass()
        # Schedule immediate update when added to Home Assistant
        self.hass.async_create_task(self.async_update())

    async def async_update(self, now=None):
        """Common update logic and scheduling."""
        try:
            next_update_time = await self._async_update_logic(now)
            self._next_update = next_update_time
            
            if next_update_time:
                if next_update_time <= dt_util.utcnow():
                    next_update_time = dt_util.utcnow() + timedelta(seconds=5)
                    _LOGGER.warning("Rescheduling %s to %s", self.name, next_update_time)
                
                self.cancel_scheduled_update()
                self._unsub_update = async_track_point_in_time(
                    self.hass, self.async_update, next_update_time
                )
                _LOGGER.debug("Scheduled next update for %s at %s", self.name, next_update_time)
            
            # Set entity as available after successful update
            self._attr_available = True
            
            if self.entity_id:
                self.async_write_ha_state()
                
        except Exception as e:
            _LOGGER.error("Error updating %s: %s", self.name, e, exc_info=True)
            self._attr_available = False
            next_update_time = dt_util.utcnow() + timedelta(minutes=5)
            self.cancel_scheduled_update()
            self._unsub_update = async_track_point_in_time(
                self.hass, self.async_update, next_update_time
            )
            if self.entity_id:
                self.async_write_ha_state()

    async def _async_update_logic(self, now):
        """Sensor-specific update logic to be implemented by subclasses.
        
        Should return the next update time or None if no scheduling needed.
        """
        raise NotImplementedError("Subclasses must implement this method")


class BaseSolSensor(BaseSolEntity, SensorEntity):
    """Base class for Sol sensor entities."""
    pass


class SunElevationSensor(BaseSolSensor):
    """Representation of a Sun Elevation sensor."""

    def __init__(self, sun_helper, elevation_step) -> None:
        """Initialize the sensor."""
        # Initialize base entity
        super().__init__("Elevation", "elevation", "Sol")
        
        self._attr_icon = "mdi:weather-sunny"
        self._attr_native_unit_of_measurement = "°"
        
        # Store the sun helper for calculations
        self.sun_helper = sun_helper
        self.elevation_step = elevation_step
        
        # State tracking
        self._current_direction = None
        self._target_elevation = None

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
            "next_change": self.next_change,
            "direction": self._current_direction,
            "target_elevation": self._target_elevation,
            "elevation_step": self.elevation_step,
        }

    async def _async_update_logic(self, now):
        """Sensor-specific update logic."""
        now = now or dt_util.utcnow()
        _LOGGER.debug("Elevation sensor update triggered at %s", now)
        
        try:
            # Get current elevation and azimuth using helper
            cur_azimuth, cur_elevation, solar_noon, solar_midnight, next_sunrise, next_sunset = self.sun_helper.get_sun_position(now)
            
            # Update state values
            self._attr_native_value = round(cur_elevation, 2)
            self._attr_available = True
            
            # Determine sun direction
            try:
                sun_direction = self.sun_helper._get_sun_direction(now, solar_noon, solar_midnight)
                if sun_direction not in ["rising", "setting"]:
                    raise ValueError(f"Invalid direction: {sun_direction}")
            except Exception as e:
                _LOGGER.error("Error determining sun direction: %s", e)
                # Fallback to elevation trend method
                future = now + timedelta(minutes=15)
                future_azimuth, future_elevation, _, _, _, _ = self.sun_helper.get_sun_position(future)
                sun_direction = "rising" if future_elevation > cur_elevation else "setting"

            self._current_direction = sun_direction

            # Calculate next target elevation based on direction and step
            if sun_direction == "rising":
                next_target = (round(cur_elevation / self.elevation_step) * self.elevation_step) + self.elevation_step
            else:
                next_target = (round(cur_elevation / self.elevation_step) * self.elevation_step) - self.elevation_step
            
            # Store target elevation in attribute
            self._target_elevation = round(next_target, 2)
            
            # Clamp to physical limits
            next_target = max(min(next_target, 90), -90)
            
            # Get the time when sun reaches the next target elevation
            next_rising_time, next_setting_time = self.sun_helper.get_time_at_elevation(next_target, now, use_center=True)
            event_time = next_rising_time if sun_direction == "rising" else next_setting_time
            
            # Fallback to next solar event if needed
            if not event_time:
                # Use sun position calculator parameters for observer
                import ephem
                from datetime import timezone
                now_utc = now.astimezone(timezone.utc).replace(tzinfo=None)
                observer = ephem.Observer()
                observer.lat = str(self.sun_helper.latitude)
                observer.lon = str(self.sun_helper.longitude)
                observer.elevation = self.sun_helper.elevation
                observer.pressure = self.sun_helper.pressure
                observer.temp = self.sun_helper.temperature
                observer.date = ephem.Date(now_utc)
                sun = ephem.Sun()
                try:
                    event_time = observer.next_transit(sun).datetime().replace(tzinfo=timezone.utc)
                    _LOGGER.debug("Using fallback solar event at %s", event_time)
                except Exception:
                    event_time = now + timedelta(minutes=5)
                    _LOGGER.debug("Using emergency fallback update at %s", event_time)
            
            _LOGGER.debug(
                "Current: %.2f° (azimuth: %.2f°), Direction: %s, Target: %.2f°",
                cur_elevation, cur_azimuth, sun_direction, self._target_elevation
            )
            
            # Return next update time
            return event_time
            
        except Exception as e:
            _LOGGER.error("Error updating Sol elevation sensor: %s", e, exc_info=True)
            self._attr_native_value = None
            self._attr_icon = "mdi:alert"
            return now + timedelta(minutes=5)  # Retry in 5 minutes


class SunMaximumElevationSensor(BaseSolSensor):
    """Representation of a Sun Maximum Elevation sensor."""

    entity_registry_enabled_default = False

    def __init__(self, sun_helper) -> None:
        """Initialize the sensor."""
        # Initialize base entity
        super().__init__("Maximum Elevation", "max_elevation", "Sol")
        
        self._attr_icon = "mdi:weather-sunny"
        self._attr_native_unit_of_measurement = "°"
        
        # Store the sun helper for calculations
        self.sun_helper = sun_helper
        
        # State tracking
        self._max_time = None
        self._max_elevation = None

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
            "next_change": self.next_change,
            "max_time": self._max_time,
            "max_elevation": self._max_elevation,
        }

    async def _async_update_logic(self, now):
        """Sensor-specific update logic."""
        now = now or dt_util.utcnow()
        _LOGGER.debug("Maximum elevation sensor update triggered at %s", now)
        
        try:
            # Get maximum and minimum elevations for the next day
            max_time, max_elevation, min_time, min_elevation = self.sun_helper.get_max_min_elevations(now, days_ahead=1)
            
            # Update state values
            self._attr_native_value = round(max_elevation, 2)
            self._attr_available = True
            
            # Store attributes
            self._max_time = max_time.isoformat()
            self._max_elevation = round(max_elevation, 2)
            
            _LOGGER.debug(
                "Maximum elevation: %.2f° at %s",
                max_elevation, max_time
            )
            
            # Schedule next update at noon tomorrow (local time)
            tomorrow = now.replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
            return tomorrow
            
        except Exception as e:
            _LOGGER.error("Error updating maximum elevation sensor: %s", e, exc_info=True)
            self._attr_native_value = None
            self._attr_icon = "mdi:alert"
            return now + timedelta(minutes=5)  # Retry in 5 minutes


class SunMinimumElevationSensor(BaseSolSensor):
    """Representation of a Sun Minimum Elevation sensor."""

    entity_registry_enabled_default = False

    def __init__(self, sun_helper) -> None:
        """Initialize the sensor."""
        # Initialize base entity
        super().__init__("Minimum Elevation", "min_elevation", "Sol")
        
        self._attr_icon = "mdi:weather-night"
        self._attr_native_unit_of_measurement = "°"
        
        # Store the sun helper for calculations
        self.sun_helper = sun_helper
        
        # State tracking
        self._min_time = None
        self._min_elevation = None

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
            "next_change": self.next_change,
            "min_time": self._min_time,
            "min_elevation": self._min_elevation,
        }

    async def _async_update_logic(self, now):
        """Sensor-specific update logic."""
        now = now or dt_util.utcnow()
        _LOGGER.debug("Minimum elevation sensor update triggered at %s", now)
        
        try:
            # Get maximum and minimum elevations for the next day
            max_time, max_elevation, min_time, min_elevation = self.sun_helper.get_max_min_elevations(now, days_ahead=1)
            
            # Update state values
            self._attr_native_value = round(min_elevation, 2)
            self._attr_available = True
            
            # Store attributes
            self._min_time = min_time.isoformat()
            self._min_elevation = round(min_elevation, 2)
            
            _LOGGER.debug(
                "Minimum elevation: %.2f° at %s",
                min_elevation, min_time
            )
            
            # Schedule next update at midnight tomorrow (local time)
            tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            return tomorrow
            
        except Exception as e:
            _LOGGER.error("Error updating minimum elevation sensor: %s", e, exc_info=True)
            self._attr_native_value = None
            self._attr_icon = "mdi:alert"
            return now + timedelta(minutes=5)  # Retry in 5 minutes 