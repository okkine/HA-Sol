"""Helper module for Sol integration."""
from datetime import datetime, timezone, timedelta, time
from typing import Optional, Literal, Tuple, Union, cast, overload, Any
import ephem  # type: ignore
import logging
import math
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_point_in_time, async_call_later
from homeassistant.util import slugify
import homeassistant.util.dt as dt_util
from homeassistant.components.binary_sensor import BinarySensorEntity
from custom_components.sol.const import DOMAIN, NAME, TEST_VERSION
from custom_components.sol.exceptions import (
    SolError, DateTimeError, TimezoneError, SolarCalculationError,
    DirectionError, ElevationError, AzimuthError, SolsticeError
)

_LOGGER = logging.getLogger(__name__)

# Global storage for solstice curve value
SOLSTICE_CURVE_STORE: dict = {
    'value': None,
    'prev_solstice': None,
    'next_solstice': None,
    'calculation_time': None
}

class SunHelper:
    """Unified helper for sun position and elevation timing calculations."""
    
    def __init__(self, latitude: float, longitude: float, elevation: float, 
                 pressure: float = 1010.0, temperature: float = 25.0):
        self.latitude = latitude
        self.longitude = longitude
        self.elevation = elevation
        self.pressure = pressure
        self.temperature = temperature
        self.search_increment = timedelta(hours=1)
        
        # Cache the sun object for reuse
        self._sun = ephem.Sun()  # type: ignore

    def _setup_observer(self, date_time: Optional[datetime] = None) -> ephem.Observer:  # type: ignore[valid-type]
        """Set up ephem observer with current location and time.
        
        Args:
            date_time: Optional timezone-aware datetime. If None, current UTC time will be used.
            
        Returns:
            Configured ephem.Observer object
            
        Raises:
            TimezoneError: If the datetime is not timezone-aware
        """
        # Ensure we have a timezone-aware datetime
        if date_time is None:
            date_time = datetime.now(timezone.utc)
        elif date_time.tzinfo is None:
            raise TimezoneError("date_time must be timezone-aware")
            
        observer = ephem.Observer()  # type: ignore[attr-defined]
        observer.lat = str(self.latitude)
        observer.lon = str(self.longitude)
        observer.elevation = self.elevation
        observer.pressure = self.pressure
        observer.temp = self.temperature
        observer.date = date_time.astimezone(timezone.utc).replace(tzinfo=None)
        
        return observer

    # === POSITION CALCULATION ===
    def calculate_position(self, dt: datetime, caller: str = "unknown") -> Tuple[float, float]:
        """Calculate sun's elevation and azimuth at the given datetime.
        
        Args:
            dt: A timezone-aware datetime object
            caller: Name of the calling sensor/entity for debugging
            
        Returns:
            A tuple of (elevation, azimuth) in degrees
            
        Raises:
            TimezoneError: If the datetime is not timezone-aware
            SolarCalculationError: If the calculation fails
        """
        try:
            if dt.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            
            # Convert to UTC for calculations
            dt_utc = dt.astimezone(timezone.utc)
            
            # Set up observer
            observer = self._setup_observer(dt_utc)
            
            # Calculate sun's position
            self._sun.compute(observer)
            
            # Convert to degrees and normalize
            elevation = math.degrees(self._sun.alt)
            azimuth = math.degrees(self._sun.az)
            
            # Only log every hour of simulation time to reduce spam
            # if dt.minute == 0:
            #     _LOGGER.debug("Sun position at %s: %.2f°, %.2f° (called by: %s)", dt, elevation, azimuth, caller)
            return elevation, azimuth
            
        except TimezoneError:
            raise
        except Exception as e:
            raise SolarCalculationError(f"Error calculating sun position: {str(e)}")

    # === SOLAR EVENT CALCULATIONS ===
    def get_peak_elevation_time(self, start_dt: datetime) -> Optional[datetime]:
        """Get the time of peak elevation (solar noon) after the given datetime.
        
        Args:
            start_dt: A timezone-aware datetime object
            
        Returns:
            The time of peak elevation, or None if it cannot be determined
            
        Raises:
            TimezoneError: If the datetime is not timezone-aware
            SolarCalculationError: If the calculation fails
        """
        try:
            if start_dt.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            event_time = self.get_next_solar_noon(start_dt)
            if event_time is None:
                _LOGGER.debug("No peak elevation time found")
            return event_time
        except TimezoneError:
            raise
        except Exception as e:
            raise SolarCalculationError(f"Error getting peak elevation time: {str(e)}")

    def get_next_solar_noon(self, start_dt: datetime) -> Optional[datetime]:
        """Get the next solar noon after the given datetime."""
        try:
            if start_dt.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            event_time = self._get_solar_event(start_dt, "next", "transit")
            return event_time
        except TimezoneError:
            raise
        except Exception as e:
            raise SolarCalculationError(f"Error getting next solar noon: {str(e)}")

    def get_next_solar_midnight(self, start_dt: datetime) -> Optional[datetime]:
        """Get the next solar midnight after the given datetime."""
        try:
            if start_dt.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            event_time = self._get_solar_event(start_dt, "next", "antitransit")
            return event_time
        except TimezoneError:
            raise
        except Exception as e:
            raise SolarCalculationError(f"Error getting next solar midnight: {str(e)}")

    def get_previous_solar_noon(self, start_dt: datetime) -> Optional[datetime]:
        """Get the previous solar noon before the given datetime."""
        try:
            if start_dt.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            event_time = self._get_solar_event(start_dt, "previous", "transit")
            return event_time
        except TimezoneError:
            raise
        except Exception as e:
            raise SolarCalculationError(f"Error getting previous solar noon: {str(e)}")

    def get_previous_solar_midnight(self, start_dt: datetime) -> Optional[datetime]:
        """Get the previous solar midnight before the given datetime."""
        try:
            if start_dt.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            event_time = self._get_solar_event(start_dt, "previous", "antitransit")
            return event_time
        except TimezoneError:
            raise
        except Exception as e:
            raise SolarCalculationError(f"Error getting previous solar midnight: {str(e)}")

    def _get_solar_event(
        self,
        dt: datetime,
        direction: Literal["next", "previous"],
        event_type: Literal["transit", "antitransit"]
    ) -> Optional[datetime]:
        """Calculate solar event time.
        
        Args:
            dt: A timezone-aware datetime object
            direction: Either 'next' or 'previous'
            event_type: Either 'transit' or 'antitransit'
            
        Returns:
            The calculated solar event time, or None if calculation fails
            
        Raises:
            TimezoneError: If the datetime is not timezone-aware
            SolarCalculationError: If the calculation fails
        """
        try:
            if dt.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            
            dt_utc = dt.astimezone(timezone.utc)
            observer = self._setup_observer(dt_utc)
            
            if event_type == "transit":
                if direction == "previous":
                    event_time = observer.previous_transit(self._sun)
                else:  # next
                    event_time = observer.next_transit(self._sun)
            else:  # antitransit
                if direction == "previous":
                    event_time = observer.previous_antitransit(self._sun)
                else:  # next
                    event_time = observer.next_antitransit(self._sun)
                    
            if event_time is not None:
                return event_time.datetime().replace(tzinfo=timezone.utc)
            return None
            
        except TimezoneError:
            raise
        except Exception as e:
            raise SolarCalculationError(f"Error calculating solar event: {str(e)}")

    def sun_direction(self, cur_dttm: Optional[datetime] = None) -> str:
        """Determine if the sun is rising at the given datetime.
        
        Args:
            cur_dttm: A timezone-aware datetime object, or None to use current UTC time
            
        Returns:
            'rising' or 'setting'
            
        Raises:
            TimezoneError: If the datetime is not timezone-aware
            DirectionError: If direction calculation fails
        """
        try:
            # Ensure datetime is timezone-aware
            current_time = datetime.now(timezone.utc) if cur_dttm is None else cur_dttm
            if current_time.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            
            # Get timezone from input datetime
            tz = current_time.tzinfo
            ONE_DAY = timedelta(days=1)
            
            # Get current date in the input datetime's timezone
            cur_date = current_time.astimezone(tz).date()
        
            # Helper function to get solar events
            def get_solar_event(date, event_type) -> Optional[datetime]:
                """Get solar event datetime for a given local date."""
                # Create timezone-aware datetime at midnight
                start = datetime.combine(date, time(0, 0)).replace(tzinfo=tz)
                end = start + ONE_DAY
                
                event: Optional[datetime] = None
                if event_type == "solar_noon":
                    event = self.get_next_solar_noon(start)
                    if event is not None and event >= end:
                        event = self.get_previous_solar_noon(start)
                elif event_type == "solar_midnight":
                    event = self.get_next_solar_midnight(start)
                    if event is not None and event >= end:
                        event = self.get_previous_solar_midnight(start)
                return event
                
            # Get solar events
            hi_dttm = get_solar_event(cur_date, "solar_noon")
            lo_dttm = get_solar_event(cur_date, "solar_midnight")
            nxt_noon = get_solar_event(cur_date + ONE_DAY, "solar_noon")
    
            # If we can't get solar events, use elevation trend
            if None in (hi_dttm, lo_dttm, nxt_noon):
                _LOGGER.debug("Missing solar events, using elevation trend")
                return self._get_direction_from_elevation_trend(current_time)
    
            # Determine bracketing events
            tl_dttm: Optional[datetime] = None
            tr_dttm: Optional[datetime] = None
            
            if lo_dttm is not None and current_time < lo_dttm:
                tl_dttm = get_solar_event(cur_date - ONE_DAY, "solar_noon")
                tr_dttm = lo_dttm
            elif hi_dttm is not None and current_time < hi_dttm:
                tl_dttm = lo_dttm
                tr_dttm = hi_dttm
            else:
                lo_dttm_next = get_solar_event(cur_date + ONE_DAY, "solar_midnight")
                if lo_dttm_next is None:
                    _LOGGER.debug("Missing next solar midnight, using elevation trend")
                    return self._get_direction_from_elevation_trend(current_time)
                    
                if current_time < lo_dttm_next:
                    tl_dttm = hi_dttm
                    tr_dttm = lo_dttm_next
                else:
                    tl_dttm = lo_dttm_next
                    tr_dttm = nxt_noon
    
            # If we couldn't get bracketing events, use elevation trend
            if tl_dttm is None or tr_dttm is None:
                _LOGGER.debug("No bracketing events found, using elevation trend")
                return self._get_direction_from_elevation_trend(current_time)
            
            # Get elevations at bracketing points
            tl_dttm_safe = cast(datetime, tl_dttm)
            tr_dttm_safe = cast(datetime, tr_dttm)
            tl_elev = self.calculate_position(tl_dttm_safe)[0]  # elevation is first value
            tr_elev = self.calculate_position(tr_dttm_safe)[0]
            
            # Final validation before return
            if tr_elev > tl_elev:
                result = "rising"
            else:
                result = "setting"
                
            # Validate result before returning
            if result not in ["rising", "setting"]:
                _LOGGER.debug("Invalid direction result, using elevation trend")
                return self._get_direction_from_elevation_trend(current_time)
                
            return result
            
        except TimezoneError:
            raise
        except Exception as e:
            raise DirectionError(f"Error determining sun direction: {str(e)}")

    def _get_direction_from_elevation_trend(self, cur_dttm: Optional[datetime] = None) -> str:
        """Determine sun direction by comparing current elevation with future elevation.
        
        Args:
            cur_dttm: A timezone-aware datetime object, or None to use current UTC time
            
        Returns:
            'rising' or 'setting'
        """
        try:
            current_time = datetime.now(timezone.utc) if cur_dttm is None else cur_dttm
            if current_time.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            
            future = current_time + timedelta(minutes=15)
            future_elev = self.calculate_position(future)[0]
            current_elev = self.calculate_position(current_time)[0]
            direction = "rising" if future_elev > current_elev else "setting"
            _LOGGER.debug("Direction from elevation trend: %s (%.2f° -> %.2f°)", 
                         direction, current_elev, future_elev)
            return direction
            
        except TimezoneError:
            raise
        except Exception as e:
            raise DirectionError(f"Error calculating direction from elevation trend: {str(e)}")

    def _get_sun_direction_with_fallback(self, now: Optional[datetime] = None) -> str:
        """Get sun direction with fallback to elevation trend.
        
        Args:
            now: A timezone-aware datetime object, or None to use current UTC time
            
        Returns:
            'rising' or 'setting'
            
        Raises:
            TimezoneError: If the datetime is not timezone-aware
            DirectionError: If direction calculation fails
        """
        try:
            current_time = datetime.now(timezone.utc) if now is None else now
            if current_time.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
                
            direction = self.sun_direction(current_time)
            _LOGGER.debug("Sun direction determined: %s", direction)
            return direction
        except Exception as e:
            raise DirectionError(f"Error getting sun direction: {str(e)}")

    def get_time_at_elevation(
        self,
        start_dt: datetime,
        target_elev: float,
        direction: Literal['rising', 'setting'],
        max_days: int = 0,
        use_center: bool = True,
        caller: str = "unknown"
    ) -> Optional[datetime]:
        """Find the next time the sun reaches target elevation in the given direction.
        
        Args:
            start_dt: The datetime to start searching from
            target_elev: The target elevation in degrees
            direction: Whether to look for 'rising' or 'setting' crossing of target
            max_days: Maximum days to search forward (0 for current day only)
            use_center: Whether to use sun's center (True) or upper limb (False)
            caller: Name of the calling sensor/entity for debugging
            
        Returns:
            The datetime when the sun reaches the target elevation, or None if not found
            
        Raises:
            ValueError: If direction is not 'rising' or 'setting'
            TimezoneError: If the datetime is not timezone-aware
            SolarCalculationError: If the calculation fails
        """
        try:
            if start_dt.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            
            if direction not in ['rising', 'setting']:
                raise ValueError("direction must be 'rising' or 'setting'")
            
            # Convert to UTC for calculations
            start_dt_utc = start_dt.astimezone(timezone.utc)
            
            # Create observer with target elevation as horizon
            observer = self._setup_observer(start_dt_utc)
            observer.horizon = str(target_elev)  # Set target elevation as horizon
            
            # Get initial elevation to determine if we need to advance to next day
            elev, _ = self.calculate_position(start_dt_utc, caller)
            
            # Determine search direction based on current elevation and target
            # Only advance to next day if max_days > 0 (not when searching for today's events)
            if max_days > 0:
                if direction == 'rising':
                    if elev > target_elev:
                        # Sun is already above target, wait for next rising
                        start_dt_utc = start_dt_utc + timedelta(days=1)
                        start_dt_utc = start_dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)
                else:  # setting
                    if elev < target_elev:
                        # Sun is already below target, wait for next day
                        start_dt_utc = start_dt_utc + timedelta(days=1)
                        start_dt_utc = start_dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Set observer date using just the date part (ephem handles timezone conversion automatically)
            # Convert local date to UTC date - this automatically handles date boundary crossing
            local_date = start_dt.date()  # Get just the date part
            utc_date = start_dt.astimezone(timezone.utc).date()  # Convert to UTC date
            observer.date = ephem.Date(utc_date)
            
            _LOGGER.debug(
                "%s: Date conversion - start_dt=%s, local_date=%s, utc_date=%s, observer_date=%s",
                caller, start_dt, local_date, utc_date, observer.date
            )
            
            # Use ephem's built-in rise/set calculations
            try:
                if direction == 'rising':
                    # Get next rising time
                    next_rise = observer.next_rising(self._sun)
                    if next_rise is not None:
                        # Convert ephem date to datetime
                        rise_dt = next_rise.datetime().replace(tzinfo=timezone.utc)
                        
                        _LOGGER.debug(
                            "%s: Ephem rising calculation - target_elev=%.2f°, start_dt_utc=%s, next_rise=%s, rise_dt=%s",
                            caller, target_elev, start_dt_utc, next_rise, rise_dt
                        )
                        
                        # Check if it's within our search window
                        end_dt = start_dt_utc + timedelta(days=max_days if max_days > 0 else 1)
                        if rise_dt <= end_dt:
                            return rise_dt
                else:  # setting
                    # Get next setting time
                    next_set = observer.next_setting(self._sun)
                    if next_set is not None:
                        # Convert ephem date to datetime
                        set_dt = next_set.datetime().replace(tzinfo=timezone.utc)
                        
                        _LOGGER.debug(
                            "%s: Ephem setting calculation - target_elev=%.2f°, start_dt_utc=%s, next_set=%s, set_dt=%s",
                            caller, target_elev, start_dt_utc, next_set, set_dt
                        )
                        
                        # Check if it's within our search window
                        end_dt = start_dt_utc + timedelta(days=max_days if max_days > 0 else 1)
                        if set_dt <= end_dt:
                            return set_dt
                            
            except (ephem.AlwaysUpError, ephem.NeverUpError):
                # Sun never reaches this elevation at this location
                return None
            
            return None
            
        except TimezoneError:
            raise
        except Exception as e:
            raise SolarCalculationError(f"Error calculating elevation at {start_dt}: {str(e)}")

class BaseSolEntity:
    """Base class for all Sol entities."""
    
    def __init__(self, name_suffix: str, unique_id_suffix: str) -> None:
        """Initialize the entity."""
        self._name_suffix = name_suffix
        self._unique_id_suffix = unique_id_suffix
        self._sun = ephem.Sun()  # type: ignore[attr-defined]
        self._state: Optional[Union[float, bool]] = None
        self._next_change: Optional[datetime] = None
        self._next_rising: Optional[datetime] = None
        self._next_setting: Optional[datetime] = None
        self._next_noon: Optional[datetime] = None
        self._next_midnight: Optional[datetime] = None
        self._elevation: Optional[float] = None
        self._azimuth: Optional[float] = None
        self._direction: Optional[str] = None
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_name = f"{NAME} {name_suffix}"
        self._attr_unique_id = f"{DOMAIN}_{unique_id_suffix}"
        self.hass = None  # Will be set by Home Assistant

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._attr_name

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._unique_id_suffix)},
            "name": f"{NAME} {self._name_suffix}",
            "model": TEST_VERSION,
            "manufacturer": NAME,
        }

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()  # type: ignore[misc]
        # Initialize _next_change to current time + 5 minutes as fallback
        self._next_change = dt_util.utcnow() + timedelta(minutes=5)
        # The first update will be handled by the child class implementation

    def _get_current_elevation(self, now: Optional[datetime], sun_helper: 'SunHelper') -> Tuple[Optional[float], Optional[float]]:
        """Get current elevation and azimuth."""
        try:
            if now is None:
                now = datetime.now(timezone.utc)
            elevation, azimuth = sun_helper.calculate_position(now)
            return elevation, azimuth
        except Exception as e:
            _LOGGER.error("Error getting current elevation: %s", str(e))
            return None, None

    def _get_sun_direction_with_fallback(self, now: Optional[datetime], sun_helper: 'SunHelper') -> Optional[str]:
        """Get sun direction with fallback."""
        try:
            if now is None:
                now = datetime.now(timezone.utc)
            return sun_helper.sun_direction(now)
        except Exception as e:
            _LOGGER.error("Error getting sun direction: %s", str(e))
            return None

    def _get_solar_event_fallback(self, now: Optional[datetime], sun_helper: 'SunHelper') -> datetime:
        """Get next solar event with fallback.
        
        This method always returns a datetime, never None.
        """
        try:
            # Ensure now is not None
            if now is None:
                now = datetime.now(timezone.utc)
            
            # Try to get next solar noon
            next_noon = sun_helper.get_next_solar_noon(now)
            if next_noon is not None:
                self._next_noon = next_noon
            
            # Try to get next solar midnight
            next_midnight = sun_helper.get_next_solar_midnight(now)
            if next_midnight is not None:
                self._next_midnight = next_midnight
            
            # Return the earlier of noon or midnight
            if next_noon is not None and next_midnight is not None:
                return min(next_noon, next_midnight)
            elif next_noon is not None:
                return next_noon
            elif next_midnight is not None:
                return next_midnight
            else:
                # If both are None, return current time + 1 hour as fallback
                return now + timedelta(hours=1)
                
        except Exception as e:
            _LOGGER.error("Error getting solar events: %s", str(e))
            # Always return a valid datetime
            return datetime.now(timezone.utc) + timedelta(hours=1)

class BaseSolSensor(BaseSolEntity, SensorEntity):
    """Base class for Sol sensors."""
    
    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        _LOGGER.info("%s: Entity added to hass, scheduling first update in 1 second", self.name)
        # Trigger the first update immediately
        if self.hass is not None:
            async_call_later(
                self.hass,
                1,  # 1 second delay to ensure everything is initialized
                lambda _: self.async_update()
            )
        else:
            _LOGGER.error("Entity %s not properly initialized - hass instance is None", self._attr_name)
    
    async def async_update(self) -> None:
        """Update the sensor."""
        _LOGGER.info("%s: async_update called", self.name)
        # Call child update logic and schedule next update
        now = dt_util.utcnow()
        if hasattr(self, '_async_update_logic'):
            try:
                _LOGGER.debug("%s: Calling _async_update_logic", self.name)
                next_update = await getattr(self, '_async_update_logic')(now)
                if next_update is not None:
                    # Calculate delay until next update
                    delay_seconds = (next_update - now).total_seconds()
                    if delay_seconds > 0:
                        # Ensure minimum delay of 30 seconds to prevent rapid updates
                        if delay_seconds < 30:
                            _LOGGER.warning("%s: Calculated delay %.1f seconds is too short, using 30 seconds", 
                                          self.name, delay_seconds)
                            delay_seconds = 30
                        
                        _LOGGER.info("%s: Scheduling next update in %.1f seconds (at %s)", 
                                    self.name, delay_seconds, next_update)
                        async_call_later(
                            self.hass, 
                            delay_seconds,
                            lambda _: self.async_update()
                        )
                    else:
                        _LOGGER.warning("%s: Next update time is in the past, scheduling update in 30 seconds", self.name)
                        async_call_later(
                            self.hass,
                            30,  # 30 second delay instead of 1 second
                            lambda _: self.async_update()
                        )
                else:
                    _LOGGER.warning("%s: No next update time returned from _async_update_logic", self.name)
                    # Schedule retry in 5 minutes
                    async_call_later(
                        self.hass,
                        300,  # 5 minutes
                        lambda _: self.async_update()
                    )
            except Exception as e:
                _LOGGER.error("%s: Error in async_update: %s", self.name, e, exc_info=True)
                # Schedule retry in 5 minutes
                async_call_later(
                    self.hass,
                    300,  # 5 minutes
                    lambda _: self.async_update()
                )
        else:
            _LOGGER.error("%s: No _async_update_logic method found", self.name)

class BaseSolBinarySensor(BaseSolEntity, BinarySensorEntity):
    """Base class for Sol binary sensors."""
    
    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        # Trigger the first update immediately
        if self.hass is not None:
            async_call_later(
                self.hass,
                1,  # 1 second delay to ensure everything is initialized
                lambda _: self.async_update()
            )
        else:
            _LOGGER.error("Entity %s not properly initialized - hass instance is None", self._attr_name)
    
    async def async_update(self) -> None:
        """Update the binary sensor."""
        # Call child update logic and schedule next update
        now = dt_util.utcnow()
        if hasattr(self, '_async_update_logic'):
            try:
                next_update = await getattr(self, '_async_update_logic')(now)
                if next_update is not None:
                    # Calculate delay until next update
                    delay_seconds = (next_update - now).total_seconds()
                    if delay_seconds > 0:
                        # Ensure minimum delay of 30 seconds to prevent rapid updates
                        if delay_seconds < 30:
                            _LOGGER.warning("%s: Calculated delay %.1f seconds is too short, using 30 seconds", 
                                          self.name, delay_seconds)
                            delay_seconds = 30
                        
                        _LOGGER.info("%s: Scheduling next update in %.1f seconds (at %s)", 
                                    self.name, delay_seconds, next_update)
                        async_call_later(
                            self.hass, 
                            delay_seconds,
                            lambda _: self.async_update()
                        )
                    else:
                        _LOGGER.warning("%s: Next update time is in the past, scheduling update in 30 seconds", self.name)
                        async_call_later(
                            self.hass,
                            30,  # 30 second delay instead of 1 second
                            lambda _: self.async_update()
                        )
                else:
                    _LOGGER.warning("%s: No next update time returned from _async_update_logic", self.name)
                    # Schedule retry in 5 minutes
                    async_call_later(
                        self.hass,
                        300,  # 5 minutes
                        lambda _: self.async_update()
                    )
            except Exception as e:
                _LOGGER.error("%s: Error in async_update: %s", self.name, e, exc_info=True)
                # Schedule retry in 5 minutes
                async_call_later(
                    self.hass,
                    300,  # 5 minutes
                    lambda _: self.async_update()
                )

class SolCalculateSolsticeCurve:
    """Calculate the normalized solstice transition curve (0-1) where 0=winter solstice, 1=summer solstice."""
    
    def __init__(self, latitude: float, longitude: float, elevation: float,
                 pressure: float = 1010.0, temperature: float = 25.0):
        self.latitude = latitude
        self.longitude = longitude
        self.elevation = elevation
        self.pressure = pressure
        self.temperature = temperature
        
        # Cache the sun object for reuse
        self._sun = ephem.Sun()  # type: ignore[attr-defined]  # Add type ignore comment

    def _setup_observer(self, date_time: Optional[datetime] = None) -> ephem.Observer:
        """Create and configure an ephem observer with current settings."""
        observer = ephem.Observer()
        observer.lat = str(self.latitude)
        observer.lon = str(self.longitude)
        observer.elevation = self.elevation
        observer.pressure = self.pressure
        observer.temp = self.temperature
        
        if date_time is not None:
            utc_time = date_time.astimezone(timezone.utc).replace(tzinfo=None)
            observer.date = ephem.Date(utc_time)
            
        return observer

    def get_normalized_curve(self, date_time: Optional[datetime] = None) -> tuple[float, datetime, datetime]:
        """Calculate normalized solstice curve (0-1) and adjacent solstices.
        
        Args:
            date_time: The datetime to calculate for (must be timezone-aware)
                      If None, current UTC time will be used.
        
        Returns:
            Tuple (normalized_value, previous_solstice, next_solstice)
        """
        # Use current UTC time if not specified
        if date_time is None:
            date_time = datetime.now(timezone.utc)
        elif date_time.tzinfo is None:
            raise ValueError("date_time must be timezone-aware")
        
        # Create observer - no need to convert to UTC again since ephem.Date handles timezone-aware datetimes
        observer = self._setup_observer(date_time)
        
        # Find solstices
        next_summer = ephem.next_summer_solstice(observer.date)
        next_winter = ephem.next_winter_solstice(observer.date)
        prev_summer = ephem.previous_summer_solstice(observer.date)
        prev_winter = ephem.previous_winter_solstice(observer.date)
        
        # Convert to datetime objects
        solstices = {
            "next_summer": next_summer.datetime().replace(tzinfo=timezone.utc),
            "next_winter": next_winter.datetime().replace(tzinfo=timezone.utc),
            "prev_summer": prev_summer.datetime().replace(tzinfo=timezone.utc),
            "prev_winter": prev_winter.datetime().replace(tzinfo=timezone.utc),
        }
        
        # Determine adjacent solstices
        if solstices["next_summer"] < solstices["next_winter"]:
            previous_solstice = solstices["prev_winter"]
            next_solstice = solstices["next_summer"]
        else:
            previous_solstice = solstices["prev_summer"]
            next_solstice = solstices["next_winter"]
        
        # Calculate solar declination at given time
        current_declination = self._get_solar_declination(date_time)
        
        # Calculate declination at solstices
        prev_declination = self._get_solar_declination(previous_solstice)
        next_declination = self._get_solar_declination(next_solstice)
        
        # Normalize between solstice values
        max_dec = max(next_declination, prev_declination)
        min_dec = min(next_declination, prev_declination)
        
        if max_dec == min_dec:  # Prevent division by zero
            normalized = 0.5
        else:
            normalized = (current_declination - min_dec) / (max_dec - min_dec)
            # Clamp between 0-1
            normalized = max(0.0, min(1.0, normalized))
        
        return normalized, previous_solstice, next_solstice

    def _get_solar_declination(self, dt: datetime) -> float:
        """Calculate solar declination in degrees for a given datetime."""
        # Create temporary observer
        observer = self._setup_observer(dt)
        self._sun.compute(observer)
        return math.degrees(self._sun.dec)
