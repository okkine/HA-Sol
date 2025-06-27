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
    DOMAIN
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
        super().__init__(user_name, user_name, "Sol")
        
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

    def _get_next_local_update_time(self, now_utc):
        """Get next local noon and midnight for scheduling updates."""
        # Convert to local timezone
        local_tz = dt_util.get_time_zone(self._time_zone)
        now_local = now_utc.astimezone(local_tz)
        
        # Calculate next local noon (12:00) and midnight (00:00)
        next_noon_local = now_local.replace(hour=12, minute=0, second=0, microsecond=0)
        if now_local >= next_noon_local:
            next_noon_local += timedelta(days=1)
            
        next_midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if now_local >= next_midnight_local:
            next_midnight_local += timedelta(days=1)
        
        # Convert back to UTC
        next_noon_utc = next_noon_local.astimezone(timezone.utc)
        next_midnight_utc = next_midnight_local.astimezone(timezone.utc)
        
        return next_noon_utc, next_midnight_utc

    def _get_solar_event_time(self, now, event_type):
        """Get accurate solar event time for calculations."""
        start_of_today = dt_util.start_of_local_day().astimezone(timezone.utc)
        
        if event_type == "noon":
            return self._sun_helper.get_time_at_elevation(
                start_dt=start_of_today,
                target_elev=0,
                direction='setting',
                max_days=1
            )
        else:  # midnight
            return self._sun_helper.get_time_at_elevation(
                start_dt=start_of_today,
                target_elev=0,
                direction='rising',
                max_days=1
            )

    async def _async_update_logic(self, now):
        try:
            now = now or dt_util.utcnow()
            start_of_today = dt_util.start_of_local_day().astimezone(timezone.utc)
            
            # === SUN DIRECTION DETECTION ===
            try:
                sun_direction = self._sun_helper.sun_direction(now)
            except Exception as e:
                _LOGGER.warning("Error getting sun direction: %s. Using elevation trend", e)
                # Fallback to elevation trend method
                future = now + timedelta(minutes=5)
                future_elev = self._sun_helper.calculate_position(future)[0]
                current_elev = self._sun_helper.calculate_position(now)[0]
                sun_direction = "rising" if future_elev > current_elev else "setting"
            
            # For dynamic sensors, get seasonal elevations from global store
            solstice_curve_str = None  # Initialize as None
            if self._seasonally_dynamic:
                # Get value from global storage
                normalized_curve = SOLSTICE_CURVE_STORE.get('value')
                
                if normalized_curve is None:
                    # Fallback to local calculation if global value not available
                    _LOGGER.warning("Solstice curve value not available; using local calculation")
                    try:
                        # Calculate solar event times for accurate calculation
                        solar_noon = self._get_solar_event_time(now, "noon")
                        solar_midnight = self._get_solar_event_time(now, "midnight")
                        
                        # Use the most recent solar event for calculation
                        calculation_time = solar_noon if solar_noon and solar_noon <= now else solar_midnight
                        if not calculation_time:
                            calculation_time = now
                        
                        # Calculate solstice curve value
                        normalized_curve, _, _ = self._solstice_calculator.get_normalized_curve(calculation_time)
                        
                        # Update global store for other sensors
                        SOLSTICE_CURVE_STORE['value'] = normalized_curve
                    except Exception as e:
                        _LOGGER.error("Error calculating seasonal elevations for %s: %s", self.name, e)
                        # Use static values as fallback
                        self._current_rising_elev = self._static_rising_elev
                        self._current_setting_elev = self._static_setting_elev
                        normalized_curve = None
                
                if normalized_curve is not None:
                    # Convert to string with full precision to prevent rounding
                    solstice_curve_str = f"{normalized_curve:.8f}"
                    
                    # Calculate current elevations using seasonal interpolation
                    self._current_rising_elev = (
                        (self._summer_rising - self._winter_rising) * normalized_curve) + self._winter_rising
                    
                    self._current_setting_elev = (
                        (self._summer_setting - self._winter_setting) * normalized_curve) + self._winter_setting
                    
                    # Log with full precision
                    _LOGGER.debug(
                        "Dynamic elevations for %s: solstice=%s, rising=%s°, setting=%s°",
                        self.name, solstice_curve_str, self._current_rising_elev, self._current_setting_elev
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
            
            # Calculate current elevation
            try:
                current_elev, azimuth = self._sun_helper.calculate_position(now)
            except Exception as e:
                _LOGGER.error("Error getting sun position for %s: %s", self.name, e)
                return now + timedelta(minutes=5)
            
            # === CORRECTED STATE DETERMINATION ===
            # State depends on sun direction and appropriate threshold
            if sun_direction == "rising":
                # During rising phase: ON if above rising threshold
                new_state = current_elev >= self._current_rising_elev
                threshold_used = self._current_rising_elev
                _LOGGER.debug(
                    "State calculation for %s (rising phase): elev=%.2f°, rising_threshold=%.2f°, state=%s",
                    self.name, current_elev, threshold_used, new_state
                )
            else:  # setting
                # During setting phase: ON if above setting threshold
                new_state = current_elev >= self._current_setting_elev
                threshold_used = self._current_setting_elev
                _LOGGER.debug(
                    "State calculation for %s (setting phase): elev=%.2f°, setting_threshold=%.2f°, state=%s",
                    self.name, current_elev, threshold_used, new_state
                )
            
            # Check if state changed
            if self._attr_is_on != new_state:
                self._attr_is_on = new_state
                _LOGGER.info(
                    "State changed for %s: %s (elev=%.2f°, threshold=%.2f°, direction=%s)",
                    self.name, new_state, current_elev, threshold_used, sun_direction
                )
            # === END STATE DETERMINATION ===
            
            # === IMPROVED NEXT EVENT CALCULATION ===
            # Get today's events first (for attributes)
            today_rising = self._sun_helper.get_time_at_elevation(
                start_dt=now,
                target_elev=self._current_rising_elev,
                direction='rising',  # type: ignore[arg-type]
                max_days=1  # Just today
            )
            
            today_setting = self._sun_helper.get_time_at_elevation(
                start_dt=now,
                target_elev=self._current_setting_elev,
                direction='setting',  # type: ignore[arg-type]
                max_days=1  # Just today
            )
            
            # Get next events (for scheduling)
            next_rising = self._sun_helper.get_time_at_elevation(
                start_dt=now,
                target_elev=self._current_rising_elev,
                direction='rising',  # type: ignore[arg-type]
                max_days=365  # Full year search for seasonal thresholds
            )
            
            next_setting = self._sun_helper.get_time_at_elevation(
                start_dt=now,
                target_elev=self._current_setting_elev,
                direction='setting',  # type: ignore[arg-type]
                max_days=365  # Full year search for seasonal thresholds
            )
            
            # Use today's events for attributes if they exist, otherwise use next events
            rising_for_attr = today_rising if today_rising else next_rising
            setting_for_attr = today_setting if today_setting else next_setting
            
            _LOGGER.debug(
                "Events for %s: today_rising=%s, today_setting=%s, next_rising=%s, next_setting=%s",
                self.name, today_rising, today_setting, next_rising, next_setting
            )
            
            # === IMPROVED NEXT EVENT SELECTION ===
            next_event = None
            event_type = None
            
            if self._attr_is_on:
                # When ON: next event is the earlier of rising or setting
                # This handles cases where thresholds are different
                if next_rising and next_setting:
                    if next_rising < next_setting:
                        next_event = next_rising
                        event_type = "rising"
                    else:
                        next_event = next_setting
                        event_type = "setting"
                elif next_rising:
                    next_event = next_rising
                    event_type = "rising"
                elif next_setting:
                    next_event = next_setting
                    event_type = "setting"
                else:
                    # Emergency fallback
                    next_event = now + timedelta(hours=1)
                    event_type = "fallback"
            else:
                # When OFF: next event is the earlier of rising or setting
                # This ensures we catch the next opportunity to turn ON
                if next_rising and next_setting:
                    if next_rising < next_setting:
                        next_event = next_rising
                        event_type = "rising"
                    else:
                        next_event = next_setting
                        event_type = "setting"
                elif next_rising:
                    next_event = next_rising
                    event_type = "rising"
                elif next_setting:
                    next_event = next_setting
                    event_type = "setting"
                else:
                    # Emergency fallback
                    next_event = now + timedelta(hours=1)
                    event_type = "fallback"
            
            # === UPDATE STATE ATTRIBUTES ===
            self._attr_extra_state_attributes = {
                "rising": rising_for_attr.isoformat(timespec='microseconds') if rising_for_attr else None,
                "setting": setting_for_attr.isoformat(timespec='microseconds') if setting_for_attr else None,
                "next_change": next_event.isoformat(timespec='microseconds') if next_event else None,
                "current_rising_elevation": self._current_rising_elev,
                "current_setting_elevation": self._current_setting_elev,
                "solstice_curve": solstice_curve_str,
                "seasonally_dynamic": self._seasonally_dynamic,
                "sun_direction": sun_direction,
                "next_event_type": event_type
            }
            
            _LOGGER.debug(
                "%s: state=%s, elev=%.2f°, rising=%.2f°, setting=%.2f°, next_change=%s (%s)",
                self.name, self._attr_is_on, current_elev, 
                self._current_rising_elev, self._current_setting_elev,
                next_event.isoformat() if next_event else "None",
                event_type
            )
            
            # === IMPROVED SCHEDULING ===
            if next_event:
                # Calculate time difference to next event
                time_diff = (next_event - now).total_seconds()
                
                # More responsive scheduling based on proximity to event
                if time_diff <= 300:  # 5 minutes or less
                    # Very close to event - check every 30 seconds
                    next_update = now + timedelta(seconds=30)
                    _LOGGER.debug("Very close to event (%.1f min), checking every 30s", time_diff/60)
                elif time_diff <= 1800:  # 30 minutes or less
                    # Close to event - check every 2 minutes
                    next_update = now + timedelta(minutes=2)
                    _LOGGER.debug("Close to event (%.1f min), checking every 2min", time_diff/60)
                elif time_diff <= 3600:  # 1 hour or less
                    # Near event - check every 5 minutes
                    next_update = now + timedelta(minutes=5)
                    _LOGGER.debug("Near event (%.1f min), checking every 5min", time_diff/60)
                elif time_diff <= 86400:  # 24 hours or less
                    # Within a day - check every 15 minutes
                    next_update = now + timedelta(minutes=15)
                    _LOGGER.debug("Within a day (%.1f hours), checking every 15min", time_diff/3600)
                else:
                    # More than a day away - check daily at local midnight
                    local_tz = dt_util.get_time_zone(self._time_zone)
                    midnight_local = now.astimezone(local_tz).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ) + timedelta(days=1)
                    next_update = midnight_local.astimezone(timezone.utc)
                    _LOGGER.debug("More than a day away (%.1f days), checking daily at midnight", time_diff/86400)
                
                # Ensure we don't schedule in the past
                if next_update <= now:
                    next_update = now + timedelta(seconds=30)
                    _LOGGER.warning("Scheduled time was in the past, using 30s fallback")
            else:
                # Fallback to 15-minute checks if no event found
                next_update = now + timedelta(minutes=15)
                _LOGGER.warning("No next event found, using 15min fallback")
            
            _LOGGER.debug("Scheduling next update for %s at %s (in %.1f minutes)", 
                         self.name, next_update, (next_update - now).total_seconds() / 60)
            return next_update
                
        except Exception as e:
            _LOGGER.error("Error updating %s: %s. Location: (lat:%.4f, lon:%.4f, elev:%.1f). "
                         "Atmosphere: (press:%.1f, temp:%.1f)",
                         self.name, e, self._latitude, self._longitude, self._elevation,
                         self._pressure, self._temperature, exc_info=True)
            # Return a retry time (5 minutes)
            return dt_util.utcnow() + timedelta(minutes=5)
