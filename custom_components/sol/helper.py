# helper.py
import math
import ephem
from datetime import datetime, timezone, timedelta, time
from typing import Optional, Literal, Tuple, Union, cast, overload
import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import slugify
import homeassistant.util.dt as dt_util
from homeassistant.components.binary_sensor import BinarySensorEntity
from .const import DOMAIN, NAME, TEST_VERSION
from .exceptions import (
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
        self._sun = ephem.Sun()

    def _setup_observer(self, date_time: Optional[datetime] = None) -> ephem.Observer:
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
            
        observer = ephem.Observer()
        observer.lat = str(self.latitude)
        observer.lon = str(self.longitude)
        observer.elevation = self.elevation
        observer.pressure = self.pressure
        observer.temp = self.temperature
        observer.date = date_time.astimezone(timezone.utc).replace(tzinfo=None)
        
        return observer

    # === POSITION CALCULATION ===
    def calculate_position(self, date_time: Optional[datetime] = None) -> tuple[float, float]:
        """Calculate current sun elevation and azimuth (degrees).
        
        Args:
            date_time: Optional timezone-aware datetime. If None, current UTC time will be used.
            
        Returns:
            Tuple of (elevation, azimuth) in degrees
            
        Raises:
            TimezoneError: If the datetime is not timezone-aware
            ElevationError: If elevation calculation fails
            AzimuthError: If azimuth calculation fails
        """
        # Ensure we have a timezone-aware datetime
        if date_time is None:
            date_time = datetime.now(timezone.utc)
        elif date_time.tzinfo is None:
            raise TimezoneError("date_time must be timezone-aware")
        
        try:
            observer = self._setup_observer(date_time)
            
            self._sun.compute(observer)
            
            elevation = math.degrees(self._sun.alt)
            azimuth = math.degrees(self._sun.az)
            
            # Normalize -0.0 to 0.0 to avoid displaying negative zero
            if abs(elevation) < 1e-10:  # Very small values near zero
                elevation = 0.0
            if abs(azimuth) < 1e-10:  # Very small values near zero
                azimuth = 0.0
            
            # Validate results
            if not (-90 <= elevation <= 90):
                raise ElevationError(f"Calculated elevation {elevation}° is outside valid range [-90, 90]")
            
            # Azimuth should be 0-360°, but 360° is equivalent to 0°
            if azimuth < 0:
                _LOGGER.warning("Calculated azimuth %.2f° is negative, normalizing", azimuth)
                azimuth = azimuth % 360
            elif azimuth >= 360:
                azimuth = 0.0
            
            return elevation, azimuth
            
        except TimezoneError:
            raise
        except ElevationError:
            raise
        except Exception as e:
            raise SolarCalculationError(f"Error calculating sun position: {str(e)}")

    # === SOLAR EVENT CALCULATIONS ===
    @overload
    def get_peak_elevation_time(self, start_dt: datetime) -> Optional[datetime]:
        ...

    def get_peak_elevation_time(self, start_dt: datetime) -> Optional[datetime]:
        """Get the time of peak elevation (solar noon) after the given datetime."""
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
            
            dt_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
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

    def get_peak_elevation_time(self, dt: datetime) -> datetime:
        """Get the time when the sun reaches its maximum elevation for the day (solar noon)."""
        try:
            # Use solar noon as the peak elevation time
            # This is more accurate than trying to calculate the exact peak
            peak_time = self.get_next_solar_noon(dt)
            
            # If the next solar noon is too far in the future, use the previous one
            if peak_time > dt + timedelta(days=1):
                peak_time = self.get_previous_solar_noon(dt)
            
            _LOGGER.debug("Peak elevation time (solar noon): %s", peak_time)
            return peak_time
            
        except Exception as e:
            _LOGGER.error("Error calculating peak elevation time: %s", e)
            # Fallback to approximate solar noon
            dt_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
            approx_noon = dt_utc.replace(hour=12, minute=0, second=0, microsecond=0)
            return approx_noon.replace(tzinfo=timezone.utc)

    # === ELEVATION TIMING ===
    def get_time_at_elevation(
        self,
        start_dt: datetime,
        target_elev: float,
        direction: Literal['rising', 'setting'],
        max_days: int = 0,
        use_center: bool = True
    ) -> Optional[datetime]:
        """Find the next time the sun reaches target elevation in the given direction.
        
        Args:
            start_dt: The datetime to start searching from
            target_elev: The target elevation in degrees
            direction: Whether to look for 'rising' or 'setting' crossing of target
            max_days: Maximum days to search forward (0 for current day only)
            use_center: Whether to use sun's center (True) or upper limb (False)
        """
        if target_elev is None:
            _LOGGER.error("Target elevation cannot be None for direction '%s'", direction)
            return None
            
        if direction not in ('rising', 'setting'):
            raise ValueError("Direction must be 'rising' or 'setting'")
        
        # Convert to UTC for calculations
        start_dt_utc = start_dt.astimezone(timezone.utc)
        current_date = start_dt_utc
        
        # Determine end time based on max_days
        if max_days > 0:
            end_date = start_dt_utc + timedelta(days=max_days)
        else:
            # If max_days is 0, search until end of the current day in local time
            local_date = start_dt_utc.astimezone(start_dt.tzinfo).date()
            end_local = datetime.combine(local_date, time(23, 59, 59, 999999))
            end_date = end_local.astimezone(timezone.utc)
        
        _LOGGER.debug(
            "Searching for elevation %.2f° %s between %s and %s",
            target_elev, direction, current_date, end_date
        )
        
        # Track the last elevation to detect crossings
        last_elev = None
        
        while current_date <= end_date:
            try:
                # Calculate current elevation
                current_elev, _ = self.calculate_position(current_date)
                
                if last_elev is not None:
                    # Check if we've crossed the target elevation
                    if direction == 'rising' and last_elev <= target_elev < current_elev:
                        # Found rising crossing
                        _LOGGER.debug(
                            "Found rising crossing at %s (%.2f° -> %.2f°)",
                            current_date, last_elev, current_elev
                        )
                        return current_date
                    elif direction == 'setting' and last_elev >= target_elev > current_elev:
                        # Found setting crossing
                        _LOGGER.debug(
                            "Found setting crossing at %s (%.2f° -> %.2f°)",
                            current_date, last_elev, current_elev
                        )
                        return current_date
                
                last_elev = current_elev
                current_date += self.search_increment
                
            except Exception as e:
                _LOGGER.error(
                    "Error calculating elevation at %s: %s",
                    current_date, e
                )
                current_date += self.search_increment
        
        _LOGGER.debug(
            "No %s crossing of %.2f° found between %s and %s",
            direction, target_elev, start_dt_utc, end_date
        )
        return None

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
            if cur_dttm is None:
                cur_dttm = datetime.now(timezone.utc)
            elif cur_dttm.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            
            # Get timezone from input datetime
            tz = cur_dttm.tzinfo
            ONE_DAY = timedelta(days=1)
            
            # Get current date in the input datetime's timezone
            cur_date = cur_dttm.astimezone(tz).date()
        
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
                return self._get_direction_from_elevation_trend(cur_dttm)
    
            # Determine bracketing events
            tl_dttm: Optional[datetime] = None
            tr_dttm: Optional[datetime] = None
            
            if lo_dttm is not None and cur_dttm < lo_dttm:
                tl_dttm = get_solar_event(cur_date - ONE_DAY, "solar_noon")
                tr_dttm = lo_dttm
            elif hi_dttm is not None and cur_dttm < hi_dttm:
                tl_dttm = lo_dttm
                tr_dttm = hi_dttm
            else:
                lo_dttm_next = get_solar_event(cur_date + ONE_DAY, "solar_midnight")
                if lo_dttm_next is None:
                    _LOGGER.debug("Missing next solar midnight, using elevation trend")
                    return self._get_direction_from_elevation_trend(cur_dttm)
                    
                if cur_dttm < lo_dttm_next:
                    tl_dttm = hi_dttm
                    tr_dttm = lo_dttm_next
                else:
                    tl_dttm = lo_dttm_next
                    tr_dttm = nxt_noon
    
            # If we couldn't get bracketing events, use elevation trend
            if tl_dttm is None or tr_dttm is None:
                _LOGGER.debug("No bracketing events found, using elevation trend")
                return self._get_direction_from_elevation_trend(cur_dttm)
            
            # Get elevations at bracketing points
            tl_elev = self.calculate_position(tl_dttm)[0]  # elevation is first value
            tr_elev = self.calculate_position(tr_dttm)[0]
            
            # Final validation before return
            if tr_elev > tl_elev:
                result = "rising"
            else:
                result = "setting"
                
            # Validate result before returning
            if result not in ["rising", "setting"]:
                _LOGGER.debug("Invalid direction result, using elevation trend")
                return self._get_direction_from_elevation_trend(cur_dttm)
                
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
            if cur_dttm is None:
                cur_dttm = datetime.now(timezone.utc)
            elif cur_dttm.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
            
            future = cur_dttm + timedelta(minutes=15)
            future_elev = self.calculate_position(future)[0]
            current_elev = self.calculate_position(cur_dttm)[0]
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
            if now is None:
                now = datetime.now(timezone.utc)
            elif now.tzinfo is None:
                raise TimezoneError("date_time must be timezone-aware")
                
            direction = self.sun_direction(now)
            _LOGGER.debug("Sun direction determined: %s", direction)
            return direction
        except Exception as e:
            raise DirectionError(f"Error getting sun direction: {str(e)}")

class BaseSolEntity:
    """Base class for Sol entities."""

    def __init__(self, name_suffix: str, unique_id_suffix: str) -> None:
        """Initialize the base entity."""
        self._name_suffix = name_suffix
        self._unique_id_suffix = unique_id_suffix
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_name = None  # Use device name
        self._attr_unique_id = None  # Set by platform
        self.next_change = None

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {("sol", "sun_helper")},
            "name": "Sol Sun Helper",
            "manufacturer": "Sol Integration",
            "model": "Sun Helper",
            "sw_version": TEST_VERSION,
        }

    def _get_current_elevation(self, now: Optional[datetime], sun_helper: 'SunHelper') -> Tuple[Optional[float], Optional[float]]:
        """Get current elevation with error handling."""
        try:
            if now is None:
                now = datetime.now(timezone.utc)
            elevation, azimuth = sun_helper.calculate_position(now)
            return elevation, azimuth
        except Exception as e:
            _LOGGER.error("Error calculating elevation: %s", e)
            return None, None

    def _get_sun_direction_with_fallback(self, now: Optional[datetime], sun_helper: 'SunHelper') -> Optional[str]:
        """Get sun direction. Fallback is now handled within sun_direction."""
        try:
            if now is None:
                now = datetime.now(timezone.utc)
            elif now.tzinfo is None:
                raise ValueError("date_time must be timezone-aware")
                
            direction = sun_helper.sun_direction(now)
            _LOGGER.debug("Sun direction determined: %s", direction)
            return direction
        except Exception as e:
            _LOGGER.error("Error getting sun direction: %s", e)
            return None

    def _get_solar_event_fallback(self, now: Optional[datetime], sun_helper: 'SunHelper') -> datetime:
        """Get solar event fallback when elevation events are not found.
        
        Args:
            now: A timezone-aware datetime object, or None to use current UTC time
            sun_helper: The SunHelper instance to use for calculations
            
        Returns:
            The next solar event time, or current time + 5 minutes as fallback
        """
        # Ensure we have a valid datetime
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            raise ValueError("date_time must be timezone-aware")
            
        try:
            next_peak = sun_helper.get_peak_elevation_time(now)
            next_midnight = sun_helper.get_next_solar_midnight(now)
            
            if next_peak is not None and next_midnight is not None:
                event_time = min(next_peak, next_midnight)
                _LOGGER.debug("Using earlier solar event: %s (peak: %s, midnight: %s)", 
                             event_time, next_peak, next_midnight)
                return event_time
            elif next_peak is not None:
                _LOGGER.debug("Using next peak elevation: %s", next_peak)
                return next_peak
            elif next_midnight is not None:
                _LOGGER.debug("Using next solar midnight: %s", next_midnight)
                return next_midnight
            else:
                # Emergency fallback
                event_time = now + timedelta(minutes=5)
                _LOGGER.warning("No solar events found, using emergency fallback: %s", event_time)
                return event_time
            
        except Exception as e:
            _LOGGER.error("Error getting solar events for fallback: %s", e)
            return now + timedelta(minutes=5)

    async def async_update(self):
        """Update the entity."""
        raise NotImplementedError("Subclasses must implement this method")

class BaseSolSensor(BaseSolEntity, SensorEntity):
    """Base class for Sol sensor entities."""
    pass

class BaseSolBinarySensor(BaseSolEntity, BinarySensorEntity):
    """Base class for Sol binary sensor entities."""
    pass

        

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
        self._sun = ephem.Sun()

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

    def get_normalized_curve(self, date_time: datetime = None) -> tuple[float, datetime, datetime]:
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
