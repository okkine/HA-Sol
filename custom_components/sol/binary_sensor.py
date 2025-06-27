# binary_sensor.py
import logging
from datetime import datetime, timedelta, timezone
from homeassistant.util import dt as dt_util
from .helper import SunHelper, BaseSolBinarySensor, SolCalculateSolsticeCurve, SOLSTICE_CURVE_STORE
from .const import (
    CONF_BINARY_ELEVATION_SENSOR,
    CONF_RISING_ELEVATION,
    CONF_SETTING_ELEVATION,
    CONF_SEASONALLY_DYNAMIC,
    CONF_SUMMER_RISING_ELEVATION,
    CONF_SUMMER_SETTING_ELEVATION,
    CONF_WINTER_RISING_ELEVATION,
    CONF_WINTER_SETTING_ELEVATION,
    CONF_PRESSURE,
    CONF_TEMPERATURE,
    DEFAULT_PRESSURE,
    DEFAULT_TEMPERATURE,
    DOMAIN,
    NAME
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up Sol binary sensors."""
    conf = discovery_info if discovery_info is not None else config
    latitude = hass.config.latitude
    longitude = hass.config.longitude
    elevation = hass.config.elevation
    time_zone = hass.config.time_zone
    pressure = conf.get(CONF_PRESSURE, DEFAULT_PRESSURE)
    temperature = conf.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)

    sensors = []
    if CONF_BINARY_ELEVATION_SENSOR in conf:
        for sensor_config in conf[CONF_BINARY_ELEVATION_SENSOR]:
            try:
                # Get configuration values
                seasonally_dynamic = sensor_config.get(CONF_SEASONALLY_DYNAMIC, False)
                rising_elev = sensor_config.get(CONF_RISING_ELEVATION)
                setting_elev = sensor_config.get(CONF_SETTING_ELEVATION)
                summer_rising = sensor_config.get(CONF_SUMMER_RISING_ELEVATION)
                summer_setting = sensor_config.get(CONF_SUMMER_SETTING_ELEVATION)
                winter_rising = sensor_config.get(CONF_WINTER_RISING_ELEVATION)
                winter_setting = sensor_config.get(CONF_WINTER_SETTING_ELEVATION)
                
                # Validate configuration
                if seasonally_dynamic:
                    # Validate required seasonal parameters
                    if None in (summer_rising, summer_setting, winter_rising, winter_setting):
                        _LOGGER.error("Missing seasonal elevation parameters for dynamic sensor %s", 
                                     sensor_config["name"])
                        continue
                else:
                    # Validate static elevations
                    if rising_elev is None and setting_elev is None:
                        _LOGGER.error("Missing elevation configuration for binary sensor %s. "
                                     "Please provide at least one of rising_elevation or setting_elevation.", 
                                     sensor_config["name"])
                        continue
                    
                    # Handle single elevation configuration
                    if rising_elev is None:
                        rising_elev = setting_elev
                    if setting_elev is None:
                        setting_elev = rising_elev
                
                sensor = SolBinaryElevationSensor(
                    user_name=sensor_config["name"],
                    latitude=latitude,
                    longitude=longitude,
                    elevation=elevation,
                    pressure=pressure,
                    temperature=temperature,
                    time_zone=time_zone,
                    seasonally_dynamic=seasonally_dynamic,
                    rising_elev=rising_elev,
                    setting_elev=setting_elev,
                    summer_rising=summer_rising,
                    summer_setting=summer_setting,
                    winter_rising=winter_rising,
                    winter_setting=winter_setting
                )
                sensors.append(sensor)
                _LOGGER.debug("Created binary elevation sensor: %s (dynamic: %s)", 
                             sensor_config["name"], seasonally_dynamic)
            except Exception as e:
                _LOGGER.error("Failed to create binary sensor %s: %s", 
                              sensor_config["name"], e)
    
    async_add_entities(sensors, True)
    _LOGGER.info("Setup completed with %d binary elevation sensors", len(sensors))

class SolBinaryElevationSensor(BaseSolBinarySensor):
    """Binary sensor showing if sun is above target elevation with seasonal adjustment."""
    
    _attr_icon = "mdi:weather-sunny"
    
    def __init__(self, user_name, latitude, longitude, elevation, pressure, temperature, time_zone,
                 seasonally_dynamic, rising_elev, setting_elev, summer_rising, summer_setting, 
                 winter_rising, winter_setting):
        # Initialize base entity
        super().__init__(user_name, user_name)
        
        # Store configuration
        self._seasonally_dynamic = seasonally_dynamic
        self._static_rising_elev = rising_elev
        self._static_setting_elev = setting_elev
        self._summer_rising = summer_rising
        self._summer_setting = summer_setting
        self._winter_rising = winter_rising
        self._winter_setting = winter_setting
        
        # Location and atmospheric parameters
        self._latitude = latitude
        self._longitude = longitude
        self._elevation = elevation
        self._pressure = pressure
        self._temperature = temperature
        self._time_zone = time_zone
        
        # Current dynamic values
        self._current_rising_elev = rising_elev
        self._current_setting_elev = setting_elev
        self._solstice_curve = None
        
        # Unified helper class
        self._sun_helper = SunHelper(
            latitude, longitude, elevation, pressure, temperature
        )
        
        # Solstice calculator for dynamic mode
        self._solstice_calculator = None
        if seasonally_dynamic:
            self._solstice_calculator = SolCalculateSolsticeCurve(
                latitude, longitude, elevation, pressure, temperature
            )
        
        # Initial state attributes
        self._attr_extra_state_attributes = {
            "rising": None,
            "setting": None,
            "next_change": None,
            "current_rising_elevation": self._current_rising_elev,
            "current_setting_elevation": self._current_setting_elev,
            "solstice_curve": self._solstice_curve,
            "seasonally_dynamic": seasonally_dynamic,
            "sun_direction": None,
            "next_event_type": None
        }
        
        _LOGGER.debug("Initialized binary elevation sensor: %s (dynamic: %s)", 
                     user_name, seasonally_dynamic)

    async def _async_update_logic(self, now):
        try:
            now = now or dt_util.utcnow()
            
            # Get sun direction using shared method
            sun_direction = self._get_sun_direction_with_fallback(now, self._sun_helper)
            
            # === DYNAMIC ELEVATION CALCULATION ===
            if self._seasonally_dynamic:
                # Get value from global storage
                normalized_curve = SOLSTICE_CURVE_STORE.get('value')
                
                if normalized_curve is None:
                    # Fallback to local calculation if global value not available
                    _LOGGER.warning("Solstice curve value not available; using local calculation")
                    try:
                        # Calculate solstice curve value at current time
                        normalized_curve, _, _ = self._solstice_calculator.get_normalized_curve(now)
                        # Update global store for other sensors
                        SOLSTICE_CURVE_STORE['value'] = normalized_curve
                    except Exception as e:
                        _LOGGER.error("Error calculating seasonal elevations for %s: %s", self.name, e)
                        # Use static values as fallback
                        self._current_rising_elev = self._static_rising_elev
                        self._current_setting_elev = self._static_setting_elev
                        normalized_curve = None
                
                if normalized_curve is not None:
                    # Calculate current elevations using seasonal interpolation
                    self._current_rising_elev = (
                        (self._summer_rising - self._winter_rising) * normalized_curve) + self._winter_rising
                    
                    self._current_setting_elev = (
                        (self._summer_setting - self._winter_setting) * normalized_curve) + self._winter_setting
                    
                    _LOGGER.debug(
                        "Dynamic elevations for %s: solstice=%.8f, rising=%.2f°, setting=%.2f°",
                        self.name, normalized_curve, self._current_rising_elev, self._current_setting_elev
                    )
            else:
                # Use static elevations
                self._current_rising_elev = self._static_rising_elev
                self._current_setting_elev = self._static_setting_elev
            
            # Ensure we have valid elevation values
            if self._current_rising_elev is None or self._current_setting_elev is None:
                _LOGGER.error("Invalid elevation values for %s: rising=%s, setting=%s", 
                             self.name, self._current_rising_elev, self._current_setting_elev)
                return now + timedelta(minutes=5)
            
            # Get current elevation using shared method
            current_elev, azimuth = self._get_current_elevation(now, self._sun_helper)
            if current_elev is None:
                return now + timedelta(minutes=5)
            
            # === SIMPLIFIED STATE DETERMINATION ===
            # State depends on sun direction and appropriate threshold
            if sun_direction == "rising":
                # During rising phase: ON if above rising threshold
                new_state = current_elev >= self._current_rising_elev
                threshold_used = self._current_rising_elev
            else:  # setting
                # During setting phase: ON if above setting threshold
                new_state = current_elev >= self._current_setting_elev
                threshold_used = self._current_setting_elev
            
            # Check if state changed
            if self._attr_is_on != new_state:
                self._attr_is_on = new_state
                _LOGGER.info(
                    "State changed for %s: %s (elev=%.2f°, threshold=%.2f°, direction=%s)",
                    self.name, new_state, current_elev, threshold_used, sun_direction
                )
            
            # === SIMPLIFIED NEXT EVENT CALCULATION ===
            # Get the next event based on current state and direction
            if self._attr_is_on:
                # When ON: next event is when we go below the appropriate threshold
                if sun_direction == "rising":
                    # Currently rising and ON - next event is when we go below setting threshold
                    next_event = self._sun_helper.get_time_at_elevation(
                        start_dt=now,
                        target_elev=self._current_setting_elev,
                        direction='setting',
                        max_days=1
                    )
                    event_type = "setting"
                else:
                    # Currently setting and ON - next event is when we go below rising threshold (next day)
                    next_event = self._sun_helper.get_time_at_elevation(
                        start_dt=now,
                        target_elev=self._current_rising_elev,
                        direction='rising',
                        max_days=1
                    )
                    event_type = "rising"
            else:
                # When OFF: next event is when we go above the appropriate threshold
                if sun_direction == "rising":
                    # Currently rising and OFF - next event is when we go above rising threshold
                    next_event = self._sun_helper.get_time_at_elevation(
                        start_dt=now,
                        target_elev=self._current_rising_elev,
                        direction='rising',
                        max_days=1
                    )
                    event_type = "rising"
                else:
                    # Currently setting and OFF - next event is when we go above setting threshold
                    next_event = self._sun_helper.get_time_at_elevation(
                        start_dt=now,
                        target_elev=self._current_setting_elev,
                        direction='setting',
                        max_days=1
                    )
                    event_type = "setting"
            
            # Fallback to solar event if needed
            if not next_event:
                _LOGGER.debug(
                    "No elevation event found for %s (state=%s, direction=%s). Using solar event fallback.",
                    self.name, self._attr_is_on, sun_direction
                )
                next_event = self._get_solar_event_fallback(now, self._sun_helper)
            
            # === UPDATE STATE ATTRIBUTES ===
            # Base attributes for all sensors
            attributes = {
                "next_change": next_event.isoformat() if next_event else None,
                "current_rising_elevation": self._current_rising_elev,
                "current_setting_elevation": self._current_setting_elev,
                "seasonally_dynamic": self._seasonally_dynamic,
                "sun_direction": sun_direction,
                "next_event_type": event_type
            }
            
            # Add solstice curve only for dynamic sensors
            if self._seasonally_dynamic and normalized_curve is not None:
                attributes["solstice_curve"] = f"{normalized_curve:.8f}"
            
            self._attr_extra_state_attributes = attributes
            
            _LOGGER.debug(
                "%s: state=%s, elev=%.2f°, rising=%.2f°, setting=%.2f°, next_change=%s (%s)",
                self.name, self._attr_is_on, current_elev, 
                self._current_rising_elev, self._current_setting_elev,
                next_event.isoformat() if next_event else "None",
                event_type
            )
            
            # === DIRECT SCHEDULING ===
            # Use the exact event time, just like the elevation sensor
            return next_event
                
        except Exception as e:
            _LOGGER.error("Error updating %s: %s", self.name, e, exc_info=True)
            return now + timedelta(minutes=5)
