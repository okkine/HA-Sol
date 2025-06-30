# binary_sensor.py
import logging
from datetime import datetime, timedelta, timezone, time
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
        
        # Initialize state as False - will be set to correct value on first update
        self._attr_is_on = False
        
        _LOGGER.debug("Initialized binary elevation sensor: %s (dynamic: %s)", 
                     user_name, seasonally_dynamic)

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        
        # Initialize with today's events immediately
        now = dt_util.utcnow()
        local_tz = dt_util.get_time_zone(self._time_zone)
        now_local = now.astimezone(local_tz)
        today_local_date = now_local.date()
        
        # Create today at midnight in local time for rising/setting attributes
        today_midnight_local = datetime.combine(today_local_date, time.min).replace(tzinfo=local_tz)
        
        # Get today's events for initial attributes
        today_rise = self._sun_helper.get_time_at_elevation(
            start_dt=today_midnight_local,  # Use today at midnight to get today's events
            target_elev=self._current_rising_elev,
            direction='rising',
            max_days=0,
            caller=self.name
        )
        
        today_set = self._sun_helper.get_time_at_elevation(
            start_dt=today_midnight_local,  # Use today at midnight to get today's events
            target_elev=self._current_setting_elev,
            direction='setting',
            max_days=0,
            caller=self.name
        )
        
        # Set initial attributes
        self._attr_extra_state_attributes.update({
            "rising": today_rise.isoformat() if today_rise else "unknown",
            "setting": today_set.isoformat() if today_set else "unknown",
            "next_change": "unknown",
            "current_rising_elevation": self._current_rising_elev,
            "current_setting_elevation": self._current_setting_elev,
            "seasonally_dynamic": self._seasonally_dynamic,
            "sun_direction": "unknown",
            "next_event_type": "unknown"
        })
        
        _LOGGER.info("%s: Initialized with today's events - rising: %s, setting: %s", 
                    self.name, 
                    today_rise.isoformat() if today_rise else "unknown",
                    today_set.isoformat() if today_set else "unknown")

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
                        if self._solstice_calculator is not None:
                            normalized_curve, _, _ = self._solstice_calculator.get_normalized_curve(now)
                            # Update global store for other sensors
                            SOLSTICE_CURVE_STORE['value'] = normalized_curve
                        else:
                            _LOGGER.error("Solstice calculator not available for %s", self.name)
                            normalized_curve = None
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
            
            # === STATE DETERMINATION ===
            # State depends on sun direction and appropriate threshold:
            # - During RISING phase: ON if above RISING threshold (higher threshold)
            # - During SETTING phase: ON if above SETTING threshold (lower threshold)
            # This creates hysteresis to prevent rapid state changes around transitions
            if sun_direction == "rising":
                # During rising phase: ON if above rising threshold
                new_state = current_elev >= self._current_rising_elev
                threshold_used = self._current_rising_elev
            else:  # setting
                # During setting phase: ON if above setting threshold
                new_state = current_elev >= self._current_setting_elev
                threshold_used = self._current_setting_elev
            
            # Debug logging for state determination
            _LOGGER.debug(
                "%s state determination: direction=%s, elev=%.2f°, rising_threshold=%.2f°, "
                "setting_threshold=%.2f°, threshold_used=%.2f°, new_state=%s, current_state=%s",
                self.name, sun_direction, current_elev, self._current_rising_elev,
                self._current_setting_elev, threshold_used, new_state, self._attr_is_on
            )
            
            # ALWAYS update the state to reflect current calculated state
            # This ensures the binary sensor state is always correct
            state_changed = self._attr_is_on != new_state
            self._attr_is_on = new_state
            
            if state_changed:
                _LOGGER.info(
                    "State changed for %s: %s (elev=%.2f°, threshold=%.2f°, direction=%s)",
                    self.name, new_state, current_elev, threshold_used, sun_direction
                )
            else:
                _LOGGER.debug(
                    "State unchanged for %s: %s (elev=%.2f°, threshold=%.2f°, direction=%s)",
                    self.name, new_state, current_elev, threshold_used, sun_direction
                )
            
            # === CALCULATE TODAY'S RISE AND SET TIMES ===
            # Get today's rise and set times for the current thresholds
            # Use today at midnight in local time to ensure we get today's events
            local_tz = dt_util.get_time_zone(self._time_zone)
            now_local = now.astimezone(local_tz)
            today_local_date = now_local.date()  # Just the date part
            
            # Create today at midnight in local time for rising/setting attributes
            today_midnight_local = datetime.combine(today_local_date, time.min).replace(tzinfo=local_tz)
            
            _LOGGER.debug(
                "%s: Date conversion - now=%s, now_local=%s, today_local_date=%s, today_midnight_local=%s",
                self.name, now, now_local, today_local_date, today_midnight_local
            )
            
            # Always try to get today's events first (even if they've passed)
            # Use today at midnight to ensure we get today's events, not tomorrow's
            today_rise = self._sun_helper.get_time_at_elevation(
                start_dt=today_midnight_local,  # Use today at midnight to get today's events
                target_elev=self._current_rising_elev,
                direction='rising',
                max_days=0,  # Only look for today's event
                caller=self.name
            )
            
            today_set = self._sun_helper.get_time_at_elevation(
                start_dt=today_midnight_local,  # Use today at midnight to get today's events
                target_elev=self._current_setting_elev,
                direction='setting',
                max_days=0,  # Only look for today's event
                caller=self.name
            )
            
            # If no events today, search 365 days ahead for next events to display
            if not today_rise:
                # Start search from tomorrow midnight to get next day's events
                tomorrow_midnight_local = today_midnight_local + timedelta(days=1)
                future_rise = self._sun_helper.get_time_at_elevation(
                    start_dt=tomorrow_midnight_local,  # Start from tomorrow midnight
                    target_elev=self._current_rising_elev,
                    direction='rising',
                    max_days=365,  # Look up to 365 days ahead
                    caller=self.name
                )
                # Only use future event if it's within today's local date
                if future_rise:
                    future_rise_local = future_rise.astimezone(local_tz)
                    if future_rise_local.date() == today_local_date:
                        today_rise = future_rise
                        _LOGGER.debug("%s: Using future rising event %s for today", self.name, today_rise)
            
            if not today_set:
                # Start search from tomorrow midnight to get next day's events
                tomorrow_midnight_local = today_midnight_local + timedelta(days=1)
                future_set = self._sun_helper.get_time_at_elevation(
                    start_dt=tomorrow_midnight_local,  # Start from tomorrow midnight
                    target_elev=self._current_setting_elev,
                    direction='setting',
                    max_days=365,  # Look up to 365 days ahead
                    caller=self.name
                )
                # Only use future event if it's within today's local date
                if future_set:
                    future_set_local = future_set.astimezone(local_tz)
                    if future_set_local.date() == today_local_date:
                        today_set = future_set
                        _LOGGER.debug("%s: Using future setting event %s for today", self.name, today_set)
            
            # === CALCULATE NEXT CHANGE TIME ===
            next_change = None
            next_event_type = None
            
            # First, check if today's events are still upcoming
            if today_rise and now < today_rise:
                next_change = today_rise
                next_event_type = "rising"
            elif today_set and now < today_set:
                next_change = today_set
                next_event_type = "setting"
            else:
                # Today's events have passed or don't exist - look for next events
                # Start search from current time (not today_start) to find next events
                next_rise = self._sun_helper.get_time_at_elevation(
                    start_dt=now,
                    target_elev=self._current_rising_elev,
                    direction='rising',
                    max_days=365,  # Look up to 365 days ahead
                    caller=self.name
                )
                
                next_set = self._sun_helper.get_time_at_elevation(
                    start_dt=now,
                    target_elev=self._current_setting_elev,
                    direction='setting',
                    max_days=365,  # Look up to 365 days ahead
                    caller=self.name
                )
                
                # Determine which next event comes first
                if next_rise and next_set:
                    if next_rise < next_set:
                        next_change = next_rise
                        next_event_type = "rising"
                    else:
                        next_change = next_set
                        next_event_type = "setting"
                elif next_rise:
                    next_change = next_rise
                    next_event_type = "rising"
                elif next_set:
                    next_change = next_set
                    next_event_type = "setting"
            
            # If no events found within 365 days, mark as unknown
            if not next_change:
                _LOGGER.warning(
                    "No elevation events found for %s within 365 days. "
                    "Sun may never reach target elevations at this location.",
                    self.name
                )
                next_change = None
                next_event_type = "unknown"
            
            # === DETERMINE NEXT UPDATE TIME ===
            next_update = None
            
            if next_change:
                # Update at the next change time
                next_update = next_change
            else:
                # No events found - update at midnight local time to check for new day
                local_tz = dt_util.get_time_zone(self._time_zone)
                now_local = now.astimezone(local_tz)
                midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                next_update = midnight_local.astimezone(timezone.utc)
            
            # === UPDATE STATE ATTRIBUTES ===
            # Base attributes for all sensors
            attributes = {
                "rising": today_rise.isoformat() if today_rise else "unknown",
                "setting": today_set.isoformat() if today_set else "unknown",
                "next_change": next_change.isoformat() if next_change else "unknown",
                "current_rising_elevation": self._current_rising_elev,
                "current_setting_elevation": self._current_setting_elev,
                "seasonally_dynamic": self._seasonally_dynamic,
                "sun_direction": sun_direction,
                "next_event_type": next_event_type,
                "current_elevation_raw": current_elev
            }
            
            # Add solstice curve only for dynamic sensors
            if self._seasonally_dynamic and normalized_curve is not None:
                attributes["solstice_curve"] = f"{normalized_curve:.8f}"
            
            self._attr_extra_state_attributes = attributes
            
            _LOGGER.debug(
                "%s: state=%s, elev=%.2f°, rising=%.2f°, setting=%.2f°, "
                "today_rise=%s, today_set=%s, next_change=%s (%s)",
                self.name, self._attr_is_on, current_elev, 
                self._current_rising_elev, self._current_setting_elev,
                today_rise.isoformat() if today_rise else "None",
                today_set.isoformat() if today_set else "None",
                next_change.isoformat() if next_change else "None",
                next_event_type
            )
            
            return next_update
                
        except Exception as e:
            _LOGGER.error("Error updating %s: %s", self.name, e, exc_info=True)
            return now + timedelta(minutes=5)
