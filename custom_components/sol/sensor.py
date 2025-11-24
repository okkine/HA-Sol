"""Sensors for Sol integration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DEBUG_ELEVATION_SENSOR, DEBUG_AZIMUTH_SENSOR, DEBUG_ATTRIBUTES
from .base_sensors import BaseElevationSensor, BaseAzimuthSensor, BasePositionSensor
from .utils import get_sun_position, format_sensor_naming
from .config_store import get_config_entry_data
from .utils import get_next_step, get_time_at_elevation, get_time_at_azimuth
from .cache import get_cached_solstice_curve, get_cached_solar_event

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialize Sol config entry."""
    
    
    # Get configuration data
    config_data = get_config_entry_data(config_entry.entry_id)
    
    
    enable_solstice_curve = config_data.get("enable_solstice_curve", False) if config_data else False
    
    
    # Create sensors list with exception handling
    sensors = []
    
    try:
        
        elevation_sensor = ElevationSensor(hass, config_entry.entry_id)
        sensors.append(elevation_sensor)
        
    except Exception as e:
        _LOGGER.error(f"Failed to create ElevationSensor for entry {config_entry.entry_id}: {e}")
    
    try:
        
        azimuth_sensor = AzimuthSensor(hass, config_entry.entry_id)
        sensors.append(azimuth_sensor)
        
    except Exception as e:
        _LOGGER.error(f"Failed to create AzimuthSensor for entry {config_entry.entry_id}: {e}")
    
    try:
        
        solstice_sensor = SolsticeCurveSensor(hass, config_entry.entry_id, enabled_by_default=enable_solstice_curve)
        sensors.append(solstice_sensor)
        
    except Exception as e:
        _LOGGER.error(f"Failed to create SolsticeCurveSensor for entry {config_entry.entry_id}: {e}")
    
    
    
    if sensors:
        try:
            
            async_add_entities(sensors)
            
        except Exception as e:
            _LOGGER.error(f"Failed to add sensors to Home Assistant for entry {config_entry.entry_id}: {e}")
    else:
        _LOGGER.error(f"No sensors were created for entry {config_entry.entry_id}")


class ElevationSensor(BaseElevationSensor):
    """Sensor for sun elevation."""
    def __init__(self, hass: HomeAssistant, entry_id: str):
        try:
            
            # Format the name and get unique ID
            name = "Solar Elevation"
            formatted_name, unique_id = format_sensor_naming(name, entry_id)
      
            # Pass the formatted name and unique_id
            super().__init__(hass=hass, sensor_name=formatted_name, unique_id=unique_id)
            
            # Store entry_id for sun position calculations
            self._entry_id = entry_id
            
            # Add device info to link this sensor to the device
            self._attr_device_info = {
                "identifiers": {(DOMAIN, entry_id)},
            }
            
            
            
        except Exception as e:
            _LOGGER.error(f"Failed to create ElevationSensor for entry {entry_id}: {e}", exc_info=True)
            raise

    async def _async_update_logic(self, now=None) -> datetime:
        """Update the sensor value and return next update time."""
        try:
            # Use current time if not provided
            if now is None:
                now = datetime.now()
            
            # Get elevation step from config entry
            config_data = get_config_entry_data(self._entry_id)
            elevation_step = config_data.get('elevation_step', 0.5) if config_data else 0.5
            
            # Get current sun position data
            sun_data = get_sun_position(self.hass, now, self._entry_id, config_data=config_data)
            
            # Update the sensor value
            self._attr_native_value = round(sun_data['elevation'], 2)
            
            # Calculate the next elevation step
            current_elevation = sun_data['elevation']
            next_elevation = get_next_step(
                'elevation',
                elevation_step,
                sun_data,
                now,
                self._entry_id,
                self.hass,
                DEBUG_ELEVATION_SENSOR
            )
            # Only pass a float to get_time_at_elevation; allow override time for event cases
            next_time_override = None
            if isinstance(next_elevation, dict):
                elevation_target = float(next_elevation.get('elevation', 0.0) or 0.0)
                next_time_override = next_elevation.get('next_time')
            elif next_elevation is None:
                elevation_target = 0.0
            else:
                elevation_target = float(next_elevation)

            if next_time_override:
                # Use cached UTC event time directly to avoid tangent search
                next_update_time = next_time_override + timedelta(milliseconds=100)
            else:
                # Get the time when sun will be at the next elevation step
                next_rising, next_setting, next_event = get_time_at_elevation(
                    self.hass, elevation_target, now, self._entry_id, next_transit_fallback=True, config_data=config_data
                )
                
                # Use next_event as the next update time (handles all edge cases)
                if next_event:
                    next_update_time = next_event
                else:
                    # Fallback to 60 seconds if no event found
                    next_update_time = datetime.now(timezone.utc) + timedelta(seconds=60)
            
            # Use the target elevation as next_target
            next_target = next_elevation
            
            # Get solar events cache information
            try:
                cached_next_event_time, cached_next_event_type, cached_next_event_elevation, used_cache = get_cached_solar_event(
                    self.hass, self._entry_id, now
                )
                
                # Get cache information for debugging
                from .cache import get_solar_events_cache_instance
                solar_events_cache_instance = get_solar_events_cache_instance(self.hass)
                entry_cache = solar_events_cache_instance._get_entry_cache(self._entry_id)
                
                solar_events_cache = {
                    'next_event_time': cached_next_event_time.isoformat() if cached_next_event_time else None,
                    'next_event_type': cached_next_event_type,
                    'next_event_elevation': cached_next_event_elevation,
                    'cache_available': cached_next_event_time is not None,
                    'debug_current_time': now.isoformat() if now else None,
                    'debug_timezone': str(now.tzinfo) if now and now.tzinfo else None,
                    'debug_sensor_entry_id': self._entry_id,
                    'debug_used_cache': used_cache
                }
            except Exception as e:
                # Get cache information for debugging even in error case
                from .cache import get_solar_events_cache_instance
                solar_events_cache_instance = get_solar_events_cache_instance(self.hass)
                entry_cache = solar_events_cache_instance._get_entry_cache(self._entry_id)
                
                solar_events_cache = {
                    'next_event_time': None,
                    'next_event_type': None,
                    'next_event_elevation': None,
                    'cache_available': False,
                    'cache_error': str(e),
                    'debug_current_time': now.isoformat() if now else None,
                    'debug_timezone': str(now.tzinfo) if now and now.tzinfo else None,
                    'debug_sensor_entry_id': self._entry_id,
                    'debug_used_cache': False
                }
            
            # Store attributes (always show next_update and next_target)
            attributes = {
                'next_update': next_update_time,
                'next_target': next_target['elevation'],
            }
            
            if DEBUG_ATTRIBUTES:
                attributes['next_event'] = next_target['event']
                attributes['declination'] = sun_data['declination']
                attributes['size'] = sun_data['size']
                attributes['latitude'] = sun_data['latitude']
                attributes['longitude'] = sun_data['longitude']
                attributes['elevation_m'] = sun_data['elevation_m']
                attributes['pressure_mbar'] = sun_data['pressure_mbar']
                attributes['sun_data'] = sun_data
                attributes['solar_events_cache'] = solar_events_cache
            
            self._attr_extra_state_attributes = attributes
            
            return next_update_time
            
        except Exception as e:
            # On error, retry in 60 seconds
            return datetime.now(timezone.utc) + timedelta(seconds=60)


class AzimuthSensor(BaseAzimuthSensor):
    """Sensor for sun azimuth."""
    def __init__(self, hass: HomeAssistant, entry_id: str):
        try:
            
            # Format the name
            name = "Solar Azimuth"
            formatted_name, unique_id = format_sensor_naming(name, entry_id)

            # Pass the formatted name and unique_id
            super().__init__(hass=hass, sensor_name=formatted_name, unique_id=unique_id)
            
            # Store entry_id for sun position calculations
            self._entry_id = entry_id
            
            # Add device info to link this sensor to the device
            self._attr_device_info = {
                "identifiers": {(DOMAIN, entry_id)},
            }
            
            
            
        except Exception as e:
            _LOGGER.error(f"Failed to create AzimuthSensor for entry {entry_id}: {e}", exc_info=True)
            raise

    async def _async_update_logic(self, now=None) -> datetime:
        """Update the sensor value and return next update time."""
        
        try:
            
            # Use current time if not provided
            if now is None:
                import zoneinfo
                tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
                now = datetime.now(tz)
            
            
            # Get azimuth step from config entry
            config_data = get_config_entry_data(self._entry_id)
            azimuth_step = config_data.get('azimuth_step', 1.0) if config_data else 1.0
            
            # Get current sun position data
            sun_data = get_sun_position(self.hass, now, self._entry_id, config_data=config_data)
            
            # Update the sensor value
            self._attr_native_value = round(sun_data['azimuth'], 2) % 360
            
            # Get reversal cache from new system
            reversal_cache = None
            try:
                from .reversal_cache import get_reversal_cache_manager
                cache_manager = get_reversal_cache_manager(self.hass)
                reversal_cache = await cache_manager.get_reversals(self._entry_id, now)
            except Exception as e:
                _LOGGER.warning(f"Error loading reversal cache: {e}")
            
            # Calculate the next azimuth step using enhanced logic with reversal cache
            next_step_info = get_next_step(
                'azimuth',
                azimuth_step,
                sun_data,
                now,
                self._entry_id,
                self.hass,
                DEBUG_AZIMUTH_SENSOR,
                config_data=config_data,
                reversal_cache=reversal_cache
            )
            if isinstance(next_step_info, dict):
                next_azimuth_target = next_step_info["azimuth"]
                is_reversal = next_step_info.get("reversal", False)
                next_reversal_time_if_target = next_step_info.get("reversal_time")
            else:
                next_azimuth_target = next_step_info
                is_reversal = False
                next_reversal_time_if_target = None

            # Always get the next reversal time from cache (for monitoring)
            reversal_time = None
            if reversal_cache:
                now_utc = now.astimezone(timezone.utc)
                # Handle both old and new cache formats during migration
                if 'checkpoints' in reversal_cache:
                    # New checkpoint format
                    future_reversals = [
                        cp for cp in reversal_cache['checkpoints'] 
                        if cp['time'] > now_utc and cp.get('is_reversal', False)
                    ]
                    if future_reversals:
                        reversal_time = future_reversals[0]['time']
                elif 'reversals' in reversal_cache:
                    # Old format (during migration)
                    future_reversals = [r for r in reversal_cache['reversals'] if r['time'] > now_utc]
                    if future_reversals:
                        reversal_time = future_reversals[0]['time']

            if is_reversal and next_reversal_time_if_target is not None:
                next_update_time = next_reversal_time_if_target
                search_metrics = {"info": "Next target is a reversal; using cached reversal time."}
            else:
                # Only pass a float to get_time_at_azimuth
                if isinstance(next_azimuth_target, dict):
                    az_target = float(next_azimuth_target.get('azimuth', 0.0) or 0.0)
                elif next_azimuth_target is None:
                    az_target = 0.0
                else:
                    az_target = float(next_azimuth_target)
                next_update_time, search_metrics = get_time_at_azimuth(
                    self.hass,
                    az_target,
                    now,
                    self._entry_id,
                    None,  # start_dt
                    0.167,  # search_window_hours (10 minutes)
                    config_data=config_data,
                    reversal_cache=reversal_cache
                )
            
            # Handle calculation failure
            if next_update_time is None:
                # Let it fail naturally so we can debug the issue
                next_azimuth_target = None
            
        except Exception as e:
            # Let the exception propagate so we can debug it
            raise
        
        # Format checkpoint cache data for attributes
        _LOGGER.debug(f"Reversal cache for {self._entry_id}: type={type(reversal_cache)}, is_none={reversal_cache is None}")
        if reversal_cache:
            _LOGGER.debug(f"Cache keys for {self._entry_id}: {list(reversal_cache.keys())}")
            # Handle both old and new cache formats during migration
            if 'checkpoints' in reversal_cache:
                # New checkpoint format
                last_state = reversal_cache.get('last_known_state', {})
                checkpoint_data = {
                    'last_known_state': {
                        'time': last_state.get('time').isoformat() if last_state.get('time') else 'unknown',
                        'direction': last_state.get('direction', 0),
                        'azimuth': last_state.get('azimuth', 0)
                    },
                    'checkpoints': [
                        {
                            'time': cp['time'].isoformat(),
                            'azimuth': cp['azimuth'],
                            'direction': cp['direction'],
                            'is_reversal': cp['is_reversal']
                        }
                        for cp in reversal_cache.get('checkpoints', [])
                    ],
                    'cache_source': 'checkpoint_system'
                }
            elif 'reversals' in reversal_cache:
                # Old format (during migration)
                last_state = reversal_cache.get('last_known_state', {})
                checkpoint_data = {
                    'last_known_state': {
                        'time': last_state.get('time').isoformat() if last_state.get('time') else 'unknown',
                        'direction': last_state.get('direction', 0)
                    },
                    'reversals': [
                        {
                            'time': r['time'].isoformat(),
                            'azimuth': r['azimuth']
                        }
                        for r in reversal_cache.get('reversals', [])
                    ],
                    'cache_source': 'old_format_migrating'
                }
            else:
                _LOGGER.warning(f"Cache has unexpected keys for {self._entry_id}: {list(reversal_cache.keys())}")
                checkpoint_data = {
                    'cache_source': 'invalid_format',
                    'error': f'Cache missing expected keys. Has: {list(reversal_cache.keys())}'
                }
        else:
            _LOGGER.debug(f"No reversal cache loaded for {self._entry_id}")
            checkpoint_data = {
                'cache_source': 'not_available',
                'error': 'Checkpoint cache not loaded'
            }
        
        # Store attributes (always show next_update and next_target)
        try:
            attributes = {
                'next_update': next_update_time,
                'next_target': next_azimuth_target,
            }
            
            if DEBUG_ATTRIBUTES:
                attributes['elevation'] = sun_data['elevation']
                attributes['declination'] = sun_data['declination']
                attributes['size'] = sun_data['size']
                attributes['latitude'] = sun_data['latitude']
                attributes['longitude'] = sun_data['longitude']
                attributes['elevation_m'] = sun_data['elevation_m']
                attributes['pressure_mbar'] = sun_data['pressure_mbar']
                attributes['reversal'] = is_reversal
                attributes['reversal_time'] = reversal_time
                attributes['search_performance'] = search_metrics
                attributes['checkpoint_cache'] = checkpoint_data
            
            self._attr_extra_state_attributes = attributes
        except Exception as e:
            # Minimal attributes if even this fails
            self._attr_extra_state_attributes = {
                'next_update': next_update_time,
                'error': f'Attribute error: {e}'
            }
        
        # Ensure we always return a datetime, not None
        if next_update_time is None:
            return datetime.now(timezone.utc) + timedelta(seconds=60)
        
        return next_update_time


class SolsticeCurveSensor(BasePositionSensor):
    """Sensor for solstice curve (0-1 seasonal position)."""
    
    def __init__(self, hass: HomeAssistant, entry_id: str, enabled_by_default: bool = False):
        # Format the name using our naming convention
        name = "Solstice Curve"
        formatted_name, unique_id = format_sensor_naming(name, entry_id)

        # Initialize base sensor
        super().__init__(hass=hass, sensor_name=formatted_name, unique_id=unique_id)
        
        # Store entry_id for cache operations
        self._entry_id = entry_id
        
        # Set entity registry enabled state
        self._attr_entity_registry_enabled_default = enabled_by_default
        
        # Add device info to link this sensor to the device
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
        }
        
        # Set sensor properties
        self._attr_device_class = None
        self._attr_state_class = "measurement"
        
        # Initialize with cached data immediately
        self._initialized = False
        
        # Event listener will be registered in async_added_to_hass
        self._unsubscribe_cache_events = None

    async def async_added_to_hass(self) -> None:
        """Set up when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Register event listener now that we're fully integrated
        self._unsubscribe_cache_events = self.hass.bus.async_listen(
            f"{DOMAIN}_solstice_curve_cache_updated",
            self._handle_cache_update
        )

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return "∞"  # Infinity symbol

    @property
    def icon(self):
        """Return the icon."""
        return "mdi:infinity"  # Infinity icon

    async def _handle_cache_update(self, event):
        """Handle cache update events."""
        if event.data.get("entry_id") == self._entry_id:
            
            # Update sensor immediately with cached data (no rounding)
            self._attr_native_value = event.data["normalized"]
            
            # Get current sun position for declination
            config_data = get_config_entry_data(self._entry_id)
            sun_data = get_sun_position(self.hass, datetime.now(), self._entry_id, config_data=config_data)
            
            attributes = {
                'declination': sun_data['declination'],
            }
            
            if DEBUG_ATTRIBUTES:
                attributes['previous_solstice'] = event.data["previous_solstice"]
                attributes['next_solstice'] = event.data["next_solstice"]
                attributes['target_time'] = event.data["target_time"]
                attributes['cache_source'] = 'event_triggered'
                attributes['last_update'] = datetime.now().isoformat()
            
            self._attr_extra_state_attributes = attributes
            
            # Mark as initialized
            self._initialized = True
            
            # Schedule state update
            self.async_schedule_update_ha_state()
            
            

    async def _async_update_logic(self, now=None) -> datetime | None:
        """Initial update and fallback logic."""
        # Use current time if not provided
        if now is None:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(self.hass.config.time_zone)
            now = datetime.now(tz)
        
        # If we're already initialized via events, don't schedule any updates
        if self._initialized:
            return None  # Don't schedule any updates
        
        try:
            
            # Get cached solstice curve data
            normalized, previous_solstice, next_solstice, target_time = get_cached_solstice_curve(
                self.hass, self._entry_id, now
            )
            
            # Update the sensor value (no rounding)
            self._attr_native_value = normalized
            
            # Get current sun position for declination
            config_data = get_config_entry_data(self._entry_id)
            sun_data = get_sun_position(self.hass, now, self._entry_id, config_data=config_data)
            
            # Set extra state attributes
            attributes = {
                'declination': sun_data['declination'],
            }
            
            if DEBUG_ATTRIBUTES:
                attributes['previous_solstice'] = previous_solstice
                attributes['next_solstice'] = next_solstice
                attributes['target_time'] = target_time
                attributes['cache_source'] = 'initial_cache_lookup'
                attributes['last_update'] = now.isoformat()
            
            self._attr_extra_state_attributes = attributes
            
            # Mark as initialized
            self._initialized = True
            
            
            
        except Exception as e:
            # Fallback to manual calculation if cache fails
            from .utils import get_solstice_curve
            # Get config data for the fallback calculation
            config_data = get_config_entry_data(self._entry_id)
            normalized, previous_solstice, next_solstice = get_solstice_curve(
                self.hass, now, self._entry_id, config_data=config_data
            )
            self._attr_native_value = normalized  # No rounding
            
            # Get current sun position for declination
            sun_data = get_sun_position(self.hass, now, self._entry_id, config_data=config_data)
            
            attributes = {
                'declination': sun_data['declination'],
            }
            
            if DEBUG_ATTRIBUTES:
                attributes['previous_solstice'] = previous_solstice
                attributes['next_solstice'] = next_solstice
                attributes['target_time'] = now  # Fallback uses current time
                attributes['cache_source'] = 'direct_calculation_fallback'
                attributes['error'] = str(e)
                attributes['last_update'] = now.isoformat()
            
            self._attr_extra_state_attributes = attributes
            
            # Mark as initialized even with fallback
            self._initialized = True
            
            
        
        # Return None to prevent any scheduled updates - we're event-driven
        return None
    
    async def async_will_remove_from_hass(self):
        """Clean up event listeners when sensor is removed."""
        if hasattr(self, '_unsubscribe_cache_events') and self._unsubscribe_cache_events:
            self._unsubscribe_cache_events()
        await super().async_will_remove_from_hass()
    

