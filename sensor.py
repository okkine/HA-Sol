# sensor.py
import logging
import math
import ephem
import voluptuous as vol
from datetime import timedelta, timezone, time
from homeassistant.helpers import config_validation as cv
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import slugify
import homeassistant.util.dt as dt_util
from .helper import SunHelper, BaseSolSensor, SolCalculateSolsticeCurve, SOLSTICE_CURVE_STORE
from .const import CONF_PRESSURE, CONF_TEMPERATURE, DEFAULT_PRESSURE, DEFAULT_TEMPERATURE, DOMAIN
from typing import Literal

_LOGGER = logging.getLogger(__name__)

NAME = "Sol"

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
    
    conf = discovery_info if discovery_info is not None else config
    latitude = hass.config.latitude
    longitude = hass.config.longitude
    elevation = hass.config.elevation
    pressure = conf.get(CONF_PRESSURE, DEFAULT_PRESSURE)
    temperature = conf.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)
    time_zone = hass.config.time_zone
    
    _LOGGER.info("Using atmospheric conditions - Pressure: %s mbar, Temperature: %s °C", 
                 pressure, temperature)
    
    sensors = []
    
    # Create elevation step sensor if configured
    elevation_step = conf.get("elevation_step")
    if elevation_step is not None:
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

    
    async_add_entities(sensors, True)
    _LOGGER.info("Setup completed with %d sensors", len(sensors))

class SolElevationSensor(BaseSolSensor):
    """Sensor that tracks sun elevation in configured step intervals."""
    
    _attr_icon = "mdi:weather-sunny"
    _attr_native_unit_of_measurement = "°"
    
    def __init__(self, step, latitude, longitude, elevation, pressure, temperature):
        # Initialize base entity
        super().__init__("Elevation", "elevation", NAME)
        
        self._step = step
        # Create unified sun helper
        self._sun_helper = SunHelper(
            latitude, longitude, elevation, pressure, temperature
        )
        self._current_direction = None
        self._target_elevation = None
        
    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        return {
            "next_change": self.next_change,
            "direction": self._current_direction,
            "target_elevation": self._target_elevation
        }

    async def _async_update_logic(self, now):
        now = now or dt_util.utcnow()
        _LOGGER.debug("Elevation sensor update triggered at %s", now)
        
        # Get current elevation and azimuth using helper
        current_elev, current_azimuth = self._sun_helper.calculate_position(now)
        
        # Update state values
        self._attr_native_value = round(current_elev, 2)
        self._attr_available = True
        
        # === DIRECTION DETECTION ===
        try:
            direction = self._sun_helper.sun_direction(now)
            if direction not in ["rising", "setting"]:
                raise ValueError(f"Invalid direction: {direction}")
            _LOGGER.debug("Sun direction determined: %s (elevation: %.2f°)", direction, current_elev)
        except Exception as e:
            _LOGGER.error("Error determining sun direction: %s", e)
            # Fallback to elevation trend method
            future = now + timedelta(minutes=15)
            future_elev, _ = self._sun_helper.calculate_position(future)
            current_elev, _ = self._sun_helper.calculate_position(now)
            direction = "rising" if future_elev > current_elev else "setting"
            _LOGGER.debug("Using fallback direction: %s (%.2f° -> %.2f°)", direction, current_elev, future_elev)
        
        # STORE THE DIRECTION FOR STATE ATTRIBUTES
        self._current_direction = direction

        # Calculate next target elevation
        if direction == "rising":
            next_target = round(current_elev / self._step) * self._step + self._step
        else:
            next_target = round(current_elev / self._step) * self._step - self._step
        
        # Store target elevation in attribute
        self._target_elevation = round(next_target, 2)
        
        # Clamp to physical limits
        next_target = max(min(next_target, 90), -90)
        
        _LOGGER.debug(
            "Target calculation: current=%.2f°, step=%.2f°, direction=%s, target=%.2f°", 
            current_elev, self._step, direction, self._target_elevation
        )
        
        # Search for event starting from current time
        # Type assertion is safe here since direction is guaranteed to be 'rising' or 'setting'
        event_time = self._sun_helper.get_time_at_elevation(
            start_dt=now,
            target_elev=next_target,
            direction=direction,  # type: ignore[arg-type]
            max_days=1
        )
        
        # Fallback to next solar event if needed
        if not event_time:
            _LOGGER.debug(
                "No elevation event found for target %.2f° (direction: %s). "
                "Current elevation: %.2f°. Using solar event fallback.",
                next_target, direction, current_elev
            )
            
            # Try to get next solar events (peak elevation and midnight)
            try:
                next_peak = self._sun_helper.get_peak_elevation_time(now)
                next_midnight = self._sun_helper.get_next_solar_midnight(now)
                
                # Choose the earlier event
                if next_peak and next_midnight:
                    event_time = min(next_peak, next_midnight)
                    _LOGGER.debug("Using earlier solar event: %s (peak: %s, midnight: %s)", 
                                 event_time, next_peak, next_midnight)
                elif next_peak:
                    event_time = next_peak
                    _LOGGER.debug("Using next peak elevation: %s", event_time)
                elif next_midnight:
                    event_time = next_midnight
                    _LOGGER.debug("Using next solar midnight: %s", event_time)
                else:
                    # Emergency fallback
                    event_time = now + timedelta(minutes=5)
                    _LOGGER.warning("No solar events found, using emergency fallback: %s", event_time)
                    
            except Exception as e:
                _LOGGER.error("Error getting solar events for fallback: %s", e)
                event_time = now + timedelta(minutes=5)
                _LOGGER.debug("Using emergency fallback update at %s", event_time)
        
        _LOGGER.debug(
            "Current: %.2f° (azimuth: %.2f°), Direction: %s, Target: %.2f°",
            current_elev, current_azimuth, direction, self._target_elevation
        )
        
        # Return next update time
        return event_time
        
        
class SolSolsticeCurveSensor(BaseSolSensor):
    """Sensor that shows normalized solstice transition curve (0-1)."""
    
    _attr_icon = "mdi:chart-bell-curve"
    _attr_native_unit_of_measurement = ""  # Dimensionless value
    
    def __init__(self, latitude, longitude, elevation, pressure, temperature, time_zone):
        # Initialize base entity
        super().__init__("Solstice Curve", "solstice_curve", NAME)
        
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
        now = now or dt_util.utcnow()
        try:
            # Find the next solar noon and midnight after now
            next_noon = self._sun_helper.get_next_solar_noon(now)
            next_midnight = self._sun_helper.get_next_solar_midnight(now)

            # Determine which event comes first for the next update time
            if next_noon and next_midnight:
                next_update_time = min(next_noon, next_midnight)
            elif next_noon:
                next_update_time = next_noon
            elif next_midnight:
                next_update_time = next_midnight
            else:
                next_update_time = now

            # Calculate the next 0° elevation crossing after the update time
            # Use 'setting' for noon, 'rising' for midnight
            if next_update_time == next_noon:
                next_zero_crossing = self._sun_helper.get_time_at_elevation(
                    start_dt=next_update_time,
                    target_elev=0,
                    direction='setting',
                    max_days=1
                )
            else:
                next_zero_crossing = self._sun_helper.get_time_at_elevation(
                    start_dt=next_update_time,
                    target_elev=0,
                    direction='rising',
                    max_days=1
                )

            # Calculate solstice curve at the next 0° elevation crossing
            calculation_time = next_zero_crossing or next_update_time
            normalized, prev_solstice, next_solstice = \
                self._solstice_calculator.get_normalized_curve(calculation_time)

            # Update global storage
            SOLSTICE_CURVE_STORE['value'] = normalized
            SOLSTICE_CURVE_STORE['prev_solstice'] = prev_solstice
            SOLSTICE_CURVE_STORE['next_solstice'] = next_solstice
            SOLSTICE_CURVE_STORE['calculation_time'] = calculation_time

            # Update state and attributes
            self._attr_native_value = round(normalized, 8)
            self._attr_available = True
            self._attr_extra_state_attributes = {
                "previous_solstice": prev_solstice.isoformat(),
                "next_solstice": next_solstice.isoformat(),
                "calculation_time": calculation_time.isoformat(),
                "update_time": next_update_time.isoformat(),
            }

            _LOGGER.debug(
                "Solstice curve updated for next event at %s (curve for %s): %.4f (prev: %s, next: %s)",
                next_update_time, calculation_time, normalized, prev_solstice, next_solstice
            )
        except Exception as e:
            _LOGGER.error("Error updating solstice curve: %s", e, exc_info=True)
            self._attr_available = False
            return now + timedelta(minutes=15)  # Retry in 15 minutes

        # Schedule next update at the next solar event (noon or midnight)
        return next_update_time