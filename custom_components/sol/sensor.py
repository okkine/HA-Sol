# sensor.py
import logging
import math
import voluptuous as vol
from datetime import timedelta, timezone, time
from homeassistant.helpers import config_validation as cv
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import slugify
import homeassistant.util.dt as dt_util
from .helper import SunHelper, BaseSolSensor, SolCalculateSolsticeCurve, SOLSTICE_CURVE_STORE
from .const import CONF_PRESSURE, CONF_TEMPERATURE, DEFAULT_PRESSURE, DEFAULT_TEMPERATURE, DOMAIN, NAME
from typing import Literal

_LOGGER = logging.getLogger(__name__)

# Configuration schema
PLATFORM_SCHEMA = cv.PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_PRESSURE, default=DEFAULT_PRESSURE): vol.All(
        vol.Coerce(float), vol.Range(min=800, max=1200)
    ),
    vol.Optional(CONF_TEMPERATURE, default=DEFAULT_TEMPERATURE): vol.All(
        vol.Coerce(float), vol.Range(min=-50, max=60)
    ),
    vol.Optional("elevation_step"): vol.All(
        vol.Coerce(float), vol.Range(min=0.1, max=90)
    ),
    vol.Optional("solstice_curve", default=False): cv.boolean,
})

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up Sol sensors with consistent naming conventions."""
    _LOGGER.info("Setting up Sol sensor platform")
    
    # Use discovery_info if available, otherwise use config
    conf = discovery_info if discovery_info is not None else config
    
    # Log the configuration we received
    _LOGGER.debug("Received configuration: %s", conf)
    
    # Get location settings from Home Assistant configuration
    latitude = hass.config.latitude
    longitude = hass.config.longitude
    elevation = hass.config.elevation
    
    # Get atmospheric conditions from configuration
    pressure = conf.get(CONF_PRESSURE, DEFAULT_PRESSURE)
    temperature = conf.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)
    time_zone = hass.config.time_zone
    
    _LOGGER.info("Using location - Lat: %s, Lon: %s, Elev: %s", 
                 latitude, longitude, elevation)
    _LOGGER.info("Using atmospheric conditions - Pressure: %s mbar, Temperature: %s °C", 
                 pressure, temperature)
    
    sensors = []
    
    # Create elevation step sensor if configured
    elevation_step = conf.get("elevation_step", 1.0)  # Default to 1.0 if not specified
    try:
        elevation_sensor = SolElevationSensor(
            step=elevation_step,
            latitude=latitude,
            longitude=longitude,
            elevation=elevation,
            pressure=pressure,
            temperature=temperature
        )
        sensors.append(elevation_sensor)
        _LOGGER.info("Created elevation step sensor with step size: %s", elevation_step)
    except Exception as e:
        _LOGGER.error("Failed to create elevation sensor: %s", e)
    
    # Create solstice curve sensor if enabled
    if conf.get("solstice_curve", False):
        try:
            solstice_sensor = SolSolsticeCurveSensor(
                latitude=latitude,
                longitude=longitude,
                elevation=elevation,
                pressure=pressure,
                temperature=temperature,
                time_zone=time_zone
            )
            sensors.append(solstice_sensor)
            _LOGGER.info("Created solstice curve sensor")
        except Exception as e:
            _LOGGER.error("Failed to create solstice curve sensor: %s", e)
    else:
        _LOGGER.info("Solstice curve sensor disabled")

    if not sensors:
        _LOGGER.warning("No sensors were created - check your configuration")
        return
    
    try:
        # Add entities to Home Assistant
        async_add_entities(sensors, True)
        _LOGGER.info("Added %d sensors to Home Assistant", len(sensors))
        
        # Trigger initial updates
        for sensor in sensors:
            try:
                await sensor.async_update()
                _LOGGER.info("Initial update completed for %s", sensor.name)
            except Exception as e:
                _LOGGER.error("Error during initial update of %s: %s", sensor.name, e)
    except Exception as e:
        _LOGGER.error("Error adding sensors to Home Assistant: %s", e)

class SolElevationSensor(BaseSolSensor):
    """Sensor that tracks sun elevation in configured step intervals."""
    
    _attr_icon = "mdi:weather-sunny"
    _attr_native_unit_of_measurement = "°"
    
    def __init__(self, step, latitude, longitude, elevation, pressure, temperature):
        # Initialize base entity
        super().__init__("Elevation", "elevation")
        
        self._step = step
        # Create unified sun helper
        self._sun_helper = SunHelper(
            latitude, longitude, elevation, pressure, temperature
        )
        self._current_direction = None
        self._target_elevation = None
        self._next_change = None
        self._solar_noon_time = None
        self._solar_noon_elevation = None
        self._solar_midnight_time = None
        self._solar_midnight_elevation = None
        
    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        return {
            "next_change": self._next_change,
            "direction": self._current_direction,
            "target_elevation": self._target_elevation,
            "current_elevation_raw": getattr(self, '_current_elevation_raw', None),
            "current_azimuth_raw": getattr(self, '_current_azimuth_raw', None),
            "solar_noon_time": self._solar_noon_time,
            "solar_noon_elevation": self._solar_noon_elevation,
            "solar_midnight_time": self._solar_midnight_time,
            "solar_midnight_elevation": self._solar_midnight_elevation
        }

    async def _async_update_logic(self, now):
        """Update sensor state and schedule next update."""
        _LOGGER.debug("Elevation sensor update triggered at %s", now)
        
        # Get current elevation and azimuth using shared method
        current_elev, current_azimuth = self._get_current_elevation(now, self._sun_helper)
        if current_elev is None:
            self._next_change = now + timedelta(minutes=5)
            return self._next_change
        
        # Store raw values for debugging
        self._current_elevation_raw = current_elev
        self._current_azimuth_raw = current_azimuth
        
        # Update state values
        self._attr_native_value = round(current_elev, 2)
        
        # Get sun direction using shared method
        direction = self._get_sun_direction_with_fallback(now, self._sun_helper)
        
        # Store the direction for state attributes
        self._current_direction = direction

        # Get solar noon and midnight times and elevations
        try:
            solar_noon = self._sun_helper.get_next_solar_noon(now)
            if solar_noon:
                noon_elev, _ = self._sun_helper.calculate_position(solar_noon)
                self._solar_noon_time = solar_noon.isoformat()
                self._solar_noon_elevation = round(noon_elev, 2)
            else:
                self._solar_noon_time = None
                self._solar_noon_elevation = None
        except Exception as e:
            _LOGGER.debug("Error getting solar noon: %s", e)
            self._solar_noon_time = None
            self._solar_noon_elevation = None

        try:
            solar_midnight = self._sun_helper.get_next_solar_midnight(now)
            if solar_midnight:
                midnight_elev, _ = self._sun_helper.calculate_position(solar_midnight)
                self._solar_midnight_time = solar_midnight.isoformat()
                self._solar_midnight_elevation = round(midnight_elev, 2)
            else:
                self._solar_midnight_time = None
                self._solar_midnight_elevation = None
        except Exception as e:
            _LOGGER.debug("Error getting solar midnight: %s", e)
            self._solar_midnight_time = None
            self._solar_midnight_elevation = None

        # Calculate next target elevation
        if direction == "rising":
            next_target = round(current_elev / self._step) * self._step + self._step
        else:
            next_target = round(current_elev / self._step) * self._step - self._step
        
        # Clamp to physical limits
        next_target = max(min(next_target, 90), -90)
        
        _LOGGER.debug(
            "Target calculation: current=%.2f°, step=%.2f°, direction=%s, target=%.2f°", 
            current_elev, self._step, direction, next_target
        )
        
        # Check if we're approaching solar maximum and target might exceed it
        try:
            solar_noon = self._sun_helper.get_next_solar_noon(now)
            if solar_noon:
                # Calculate what the elevation will be at solar noon
                noon_elev, _ = self._sun_helper.calculate_position(solar_noon)
                
                # If our target elevation is higher than the solar noon elevation, 
                # we should schedule for solar noon instead and update target elevation
                if next_target > noon_elev:
                    _LOGGER.debug(
                        "Target elevation %.2f° exceeds solar noon elevation %.2f°. "
                        "Scheduling update for solar noon: %s and updating target elevation",
                        next_target, noon_elev, solar_noon
                    )
                    # Update target elevation to the actual solar noon elevation
                    self._target_elevation = round(noon_elev, 2)
                    self._next_change = solar_noon
                    return self._next_change
        except Exception as e:
            _LOGGER.debug("Error checking solar noon elevation: %s", e)
        
        # Check if we're approaching solar minimum (midnight) and target might be below it
        try:
            next_midnight = self._sun_helper.get_next_solar_midnight(now)
            if next_midnight:
                # Calculate what the elevation will be at midnight
                midnight_elev, _ = self._sun_helper.calculate_position(next_midnight)
                
                # If our target elevation is lower than the midnight elevation, 
                # we should schedule for midnight and update target elevation
                if next_target < midnight_elev:
                    _LOGGER.debug(
                        "Target elevation %.2f° is below midnight elevation %.2f°. "
                        "Scheduling update for solar midnight: %s and updating target elevation",
                        next_target, midnight_elev, next_midnight
                    )
                    # Update target elevation to the actual midnight elevation
                    self._target_elevation = round(midnight_elev, 2)
                    self._next_change = next_midnight
                    return self._next_change
        except Exception as e:
            _LOGGER.debug("Error checking midnight elevation: %s", e)
        
        # Store target elevation in attribute (for normal cases)
        self._target_elevation = round(next_target, 2)
        
        # Search for event starting from current time
        # Type assertion is safe here since direction is guaranteed to be 'rising' or 'setting'
        event_time = self._sun_helper.get_time_at_elevation(
            start_dt=now,
            target_elev=next_target,
            direction=direction,  # type: ignore[arg-type]
            max_days=1
        )
        
        if event_time:
            self._next_change = event_time
            return self._next_change
        else:
            # If we can't find the next elevation change, schedule update in 5 minutes
            self._next_change = now + timedelta(minutes=5)
            return self._next_change
        
        
class SolSolsticeCurveSensor(BaseSolSensor):
    """Sensor that shows normalized solstice transition curve (0-1)."""
    
    _attr_icon = "mdi:chart-bell-curve"
    _attr_native_unit_of_measurement = ""  # Dimensionless value
    
    def __init__(self, latitude, longitude, elevation, pressure, temperature, time_zone):
        # Initialize base entity
        super().__init__("Solstice Curve", "solstice_curve")
        
        self._latitude = latitude
        self._longitude = longitude
        self._elevation = elevation
        self._pressure = pressure
        self._temperature = temperature
        self._time_zone = time_zone
        
        # Unified helper for getting solar event times
        self._sun_helper = SunHelper(
            latitude, longitude, elevation, pressure, temperature
        )
        
        # Solstice calculator
        self._solstice_calculator = SolCalculateSolsticeCurve(
            latitude, longitude, elevation,
            pressure, temperature
        )
        
        # State attributes
        self._attr_extra_state_attributes = {
            "previous_solstice": None,
            "next_solstice": None,
            "calculation_time": None
        }

    async def _async_update_logic(self, now):
        """Update the solstice curve value.
        
        This method ensures we always use today's sunrise (before noon) or sunset (after noon)
        for the calculation time. Updates are scheduled for local noon and midnight.
        """
        try:
            # Ensure we have a timezone-aware datetime
            if now is None:
                now = dt_util.utcnow()
            elif now.tzinfo is None:
                now = dt_util.as_utc(now)
            
            # Convert to local time to determine if it's before or after noon
            local_tz = dt_util.get_time_zone(self._time_zone)
            now_local = dt_util.as_local(now)
            
            # Start from beginning of today in local time
            start_of_today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            start_of_today_utc = start_of_today_local.astimezone(dt_util.UTC)
            
            # Get today's sunrise and sunset times
            todays_sunrise = self._sun_helper.get_time_at_elevation(
                start_dt=start_of_today_utc,
                target_elev=0,
                direction='rising',
                max_days=0
            )
            todays_sunset = self._sun_helper.get_time_at_elevation(
                start_dt=start_of_today_utc,
                target_elev=0,
                direction='setting',
                max_days=0
            )
            
            # Use appropriate time based on whether it's before or after noon
            if now_local.hour < 12:
                calculation_time = todays_sunrise
                event_type_used = "today's sunrise"
            else:
                calculation_time = todays_sunset
                event_type_used = "today's sunset"
            
            # Fallback to current time only if we couldn't get either time
            if not calculation_time:
                _LOGGER.warning("Could not determine today's %s time, using current time", 
                              "sunrise" if now_local.hour < 12 else "sunset")
                calculation_time = now
                event_type_used = "current_time (fallback)"
            
            # Calculate solstice curve at the determined calculation time
            normalized, prev_solstice, next_solstice = \
                self._solstice_calculator.get_normalized_curve(calculation_time)
            
            # Update global storage
            SOLSTICE_CURVE_STORE['value'] = normalized
            SOLSTICE_CURVE_STORE['prev_solstice'] = prev_solstice
            SOLSTICE_CURVE_STORE['next_solstice'] = next_solstice
            SOLSTICE_CURVE_STORE['calculation_time'] = calculation_time
            
            # Update state and attributes
            self._attr_native_value = round(normalized, 8)
            self._attr_extra_state_attributes = {
                "previous_solstice": prev_solstice.isoformat(),
                "next_solstice": next_solstice.isoformat(),
                "calculation_time": calculation_time.isoformat(),
                "event_type_used": event_type_used,
                "todays_sunrise": todays_sunrise.isoformat() if todays_sunrise else None,
                "todays_sunset": todays_sunset.isoformat() if todays_sunset else None,
                "update_time_local": now_local.isoformat(),
                "update_time_utc": dt_util.as_utc(now).isoformat()
            }
            
            _LOGGER.debug(
                "Solstice curve updated using %s at %s: %.4f (prev: %s, next: %s)",
                event_type_used, calculation_time, normalized, prev_solstice, next_solstice
            )
            
        except Exception as e:
            _LOGGER.error("Error updating solstice curve: %s", e, exc_info=True)
            # Ensure we return a timezone-aware datetime even in error case
            return dt_util.utcnow() + timedelta(minutes=15)  # Retry in 15 minutes
        
        # Schedule next update at local noon or midnight
        return self._get_next_local_update_time(now_local)

    def _get_next_local_update_time(self, now_local):
        """Get next update time (local noon or midnight).
        
        Args:
            now_local: Current time in local timezone
        
        Returns:
            Next update time in UTC
        """
        # Calculate next local noon (12:00) and midnight (00:00)
        next_noon = now_local.replace(hour=12, minute=0, second=0, microsecond=0)
        if now_local >= next_noon:
            next_noon += timedelta(days=1)
            
        next_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if now_local >= next_midnight:
            next_midnight += timedelta(days=1)
        
        # Return whichever comes first, converted to UTC
        next_update_local = min(next_noon, next_midnight)
        next_update_utc = next_update_local.astimezone(dt_util.UTC)
        
        _LOGGER.debug(
            "Next update scheduled for %s local time",
            next_update_local.strftime("%Y-%m-%d %H:%M:%S %Z")
        )
        
        return next_update_utc
