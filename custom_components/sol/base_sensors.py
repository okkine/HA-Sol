"""Base classes for Sol position sensors."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class BasePositionSensor(SensorEntity):
    """Base class for all position/angle sensors."""
    
    def __init__(self, hass: HomeAssistant, sensor_name: str, unique_id: str) -> None:
        """Initialize position sensor with a sensor name and unique ID."""
        try:
            
            super().__init__()
            
            self.hass = hass

            # Use provided name directly (already formatted by concrete classes)
            self._attr_name = sensor_name
            
            self._attr_has_entity_name = True
            
            # Use the provided unique_id
            self._attr_unique_id = unique_id
            
            self._attr_native_value = None
            
            self._last_updated = None
            self._unsub_update = None  # Callback to unsubscribe from scheduled updates
            self._attr_available = False  # Start as unavailable until first update
            self._next_update = None  # Next scheduled update time
            
            # Get location from Home Assistant configuration
            self._latitude = hass.config.latitude
            self._longitude = hass.config.longitude
            self._elevation = hass.config.elevation
            
            
            
            
        except Exception as e:
            _LOGGER.error(f"Failed to initialize BasePositionSensor for sensor {sensor_name}: {e}", exc_info=True)
            raise
    
    async def async_added_to_hass(self) -> None:
        """Set up when entity is added to hass."""
        
        
        await super().async_added_to_hass()
        
        
        
        # Do an initial update
        
        await self.async_update()
        
    
    async def async_will_remove_from_hass(self) -> None:
        """Clean up scheduled updates when entity is removed."""
        self.cancel_scheduled_update()
        await super().async_will_remove_from_hass()
    
    @property
    def should_poll(self) -> bool:
        """Return False as entity should not be polled by Home Assistant."""
        return False
    
    @property
    def next_change(self):
        """Return the next scheduled update time."""
        return self._next_update
    
    def cancel_scheduled_update(self):
        """Cancel any pending scheduled updates."""
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None
    
    async def async_update(self, now=None):
        """Update the sensor state and schedule next update."""
        
        
        try:
            # Call the sensor-specific update logic
            
            next_update_time = await self._async_update_logic(now)
            
            
            # Store the next update time
            self._next_update = next_update_time
            
            if next_update_time:
                # Prevent scheduling updates in the past
                if next_update_time <= dt_util.utcnow():
                    next_update_time = dt_util.utcnow() + datetime.timedelta(seconds=5)
                
                self.cancel_scheduled_update()
                self._unsub_update = async_track_point_in_time(
                    self.hass, self.async_update, next_update_time
                )
            
            # Set entity as available after successful update
            self._attr_available = True
            
            
            if self.entity_id:
                self.async_write_ha_state()
                
                
        except Exception as e:
            # Error recovery: mark unavailable and retry in 5 minutes
            _LOGGER.error(f"Error updating {self.name} (ID: {self.unique_id}): {e}")
            self._attr_available = False
            next_update_time = dt_util.utcnow() + datetime.timedelta(minutes=5)
            self.cancel_scheduled_update()
            self._unsub_update = async_track_point_in_time(
                self.hass, self.async_update, next_update_time
            )
            if self.entity_id:
                self.async_write_ha_state()
    
    async def _async_update_logic(self, now):
        """
        Abstract method for sensor-specific update logic.
        Must be implemented by subclasses to provide their unique update behavior.
        
        Args:
            now: Current time (or None to use current time)
            
        Returns:
            datetime: When the next update should occur
        """
        raise NotImplementedError("Subclasses must implement this method")
    
    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return "°"

    @property
    def icon(self):
        """Return the icon."""
        return "mdi:weather-sunny"
    
    @property
    def device_class(self):
        """Return the device class."""
        return None  # Generic sensor


class BaseElevationSensor(BasePositionSensor):
    """Base class for sun elevation sensors."""
    pass


class BaseAzimuthSensor(BasePositionSensor):
    """Base class for sun azimuth sensors."""
    pass 