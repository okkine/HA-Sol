# helper.py
import math
import ephem
from datetime import datetime, timezone, timedelta, time
from typing import Optional, Literal
import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import slugify
import homeassistant.util.dt as dt_util
from homeassistant.components.binary_sensor import BinarySensorEntity
from .const import DOMAIN, NAME

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

    # === POSITION CALCULATION ===
    def calculate_position(self, date_time: Optional[datetime] = None) -> tuple[float, float]:
        """Calculate current sun elevation and azimuth (degrees)."""
        if date_time is None:
            date_time = datetime.now(timezone.utc)
        
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
                _LOGGER.warning("Calculated elevation %.2f° is outside valid range [-90, 90]", elevation)
                elevation = max(-90, min(90, elevation))
            
            # Azimuth should be 0-360°, but 360° is equivalent to 0°
            if azimuth < 0:
                _LOGGER.warning("Calculated azimuth %.2f° is negative, normalizing", azimuth)
                azimuth = azimuth % 360
            elif azimuth > 360 or abs(azimuth - 360.0) < 1e-6:
                # Normalize values very close to 360° to 0°
                if abs(azimuth - 360.0) < 1e-6:
                    azimuth = 0.0
                else:
                    _LOGGER.warning("Calculated azimuth %.2f° is outside valid range [0, 360]", azimuth)
                    azimuth = azimuth % 360
            elif abs(azimuth - 0.0) < 1e-6:
                azimuth = 0.0
            
            return elevation, azimuth
            
        except Exception as e:
            _LOGGER.error("Error calculating sun position: %s", e)
            # Return fallback values (sun below horizon)
            return -90.0, 0.0

    # === SOLAR EVENT CALCULATIONS ===
    def get_previous_solar_noon(self, dt: datetime) -> datetime:
        """Get the previous solar noon before the given datetime."""
        return self._get_solar_transit(dt, "previous")

    def get_next_solar_noon(self, dt: datetime) -> datetime:
        """Get the next solar noon after the given datetime."""
        return self._get_solar_transit(dt, "next")

    def get_previous_solar_midnight(self, dt: datetime) -> datetime:
        """Get the previous solar midnight before the given datetime."""
        return self._get_solar_antitransit(dt, "previous")

    def get_next_solar_midnight(self, dt: datetime) -> datetime:
        """Get the next solar midnight after the given datetime."""
        return self._get_solar_antitransit(dt, "next")

    def _get_solar_transit(self, dt: datetime, direction: str) -> datetime:
        """Get solar transit (noon) time."""
        return self._get_solar_event(dt, direction, "transit")

    def _get_solar_antitransit(self, dt: datetime, direction: str) -> datetime:
        """Get solar anti-transit (midnight) time."""
        return self._get_solar_event(dt, direction, "antitransit")

    def _get_solar_event(self, dt: datetime, direction: str, event_type: str) -> datetime:
        """Get solar event time using ephem."""
        dt_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
        observer = self._setup_observer(dt_utc)
        
        try:
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
                    
            return event_time.datetime().replace(tzinfo=timezone.utc)
        except Exception as e:
            _LOGGER.debug("Error calculating solar %s %s: %s", direction, event_type, e)
            # Fallback to approximate calculation
            hours = 12 if event_type == "transit" else 0
            days_offset = -1 if direction == "previous" else 1
            approx = dt_utc.replace(hour=hours, minute=0, second=0, microsecond=0)
            approx += timedelta(days=days_offset)
            return approx.replace(tzinfo=timezone.utc)

    def get_peak_elevation_time(self, dt: datetime) -> datetime:
        """Get the time when the sun reaches its maximum elevation for the day."""
        dt_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
        observer = self._setup_observer(dt_utc)
        
        try:
            # Use next_pass() to get the actual maximum altitude time
            # next_pass() returns (rise_time, rise_az, max_alt_time, max_alt, set_time, set_az)
            pass_info = observer.next_pass(self._sun)
            max_alt_time = pass_info[2]  # Maximum altitude time
            max_alt = pass_info[3]       # Maximum altitude
            
            # Convert to datetime
            peak_dt = max_alt_time.datetime().replace(tzinfo=timezone.utc)
            
            # Verify this is actually the peak by checking if it's after our start time
            if peak_dt < dt.astimezone(timezone.utc):
                # If the next pass is before our start time, get the previous pass
                observer.date = ephem.Date(dt_utc - timedelta(days=1))  # Go back one day
                pass_info = observer.next_pass(self._sun)
                max_alt_time = pass_info[2]
                peak_dt = max_alt_time.datetime().replace(tzinfo=timezone.utc)
            
            _LOGGER.debug("Peak elevation time calculated: %s (max altitude: %.2f°)", peak_dt, math.degrees(max_alt))
            return peak_dt
            
        except Exception as e:
            _LOGGER.error("Error calculating peak elevation time: %s", e)
            # Fallback to approximate solar noon
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
        if target_elev is None:
            _LOGGER.error("Target elevation cannot be None for direction '%s'", direction)
            return None
            
        if direction not in ('rising', 'setting'):
            raise ValueError("Direction must be 'rising' or 'setting'")
        
        start_dt_utc = start_dt.astimezone(timezone.utc)
        naive_start_dt_utc = start_dt_utc.replace(tzinfo=None)
        
        observer = self._setup_observer(naive_start_dt_utc)
        
        if max_days > 0:
            end_date = naive_start_dt_utc + timedelta(days=max_days)
        else:
            end_date = naive_start_dt_utc + timedelta(days=365)
        
        current_date = naive_start_dt_utc
        sun = self._sun
        always_up_count = 0
        never_up_count = 0
        
        while current_date <= end_date:
            try:
                observer.date = ephem.Date(current_date)
                
                if direction == 'rising':
                    observer.horizon = math.radians(target_elev) 
                    event_time = observer.next_rising(sun, use_center=use_center)
                else:
                    observer.horizon = math.radians(target_elev) 
                    event_time = observer.next_setting(sun, use_center=use_center)
                
                event_dt = event_time.datetime().replace(tzinfo=timezone.utc)
                
                if event_dt >= start_dt_utc:
                    _LOGGER.debug(
                        "Found %s event at elevation %.2f°: %s", 
                        direction, target_elev, event_dt
                    )
                    return event_dt
                    
            except ephem.AlwaysUpError:
                always_up_count += 1
                if always_up_count == 1:  # Log only first occurrence
                    _LOGGER.debug(
                        "Sun always above %.2f° at %s (AlwaysUpError)", 
                        target_elev, current_date
                    )
            except ephem.NeverUpError:
                never_up_count += 1
                if never_up_count == 1:  # Log only first occurrence
                    _LOGGER.debug(
                        "Sun never reaches %.2f° at %s (NeverUpError)", 
                        target_elev, current_date
                    )
            except Exception as e:
                _LOGGER.error("Error calculating %s event at elevation %.2f°: %s", 
                             direction, target_elev, e)
            
            current_date += self.search_increment
        
        # Log summary if no event found
        if always_up_count > 0 or never_up_count > 0:
            _LOGGER.debug(
                "No %s event found for elevation %.2f° after %d days. "
                "AlwaysUp: %d times, NeverUp: %d times", 
                direction, target_elev, max_days or 365, always_up_count, never_up_count
            )
        else:
            _LOGGER.debug(
                "No %s event found for elevation %.2f° after %d days", 
                direction, target_elev, max_days or 365
            )
        
        return None

    def sun_direction(self, cur_dttm: datetime) -> str:
        try:
            """Determine if the sun is rising at the given datetime."""
            
            # Get timezone from input datetime
            tz = cur_dttm.tzinfo
            ONE_DAY = timedelta(days=1)
            
            # Get current date in the input datetime's timezone
            cur_date = cur_dttm.astimezone(tz).date()
        
            # Helper function to get solar events
            def get_solar_event(date, event_type):
                """Get solar event datetime for a given local date."""
                # FIXED: Use replace instead of localize
                start = datetime.combine(date, time(0, 0)).replace(tzinfo=tz)
                end = start + ONE_DAY
                
                if event_type == "solar_noon":
                    event = self.get_next_solar_noon(start)
                    if event >= end:
                        event = self.get_previous_solar_noon(start)
                    return event
                elif event_type == "solar_midnight":
                    event = self.get_next_solar_midnight(start)
                    if event >= end:
                        event = self.get_previous_solar_midnight(start)
                    return event
                
            # Get solar events
            hi_dttm = get_solar_event(cur_date, "solar_noon")
            lo_dttm = get_solar_event(cur_date, "solar_midnight")
            nxt_noon = get_solar_event(cur_date + ONE_DAY, "solar_noon")
    
            # Determine bracketing events
            if cur_dttm < lo_dttm:
                tl_dttm = get_solar_event(cur_date - ONE_DAY, "solar_noon")
                tr_dttm = lo_dttm
            elif cur_dttm < hi_dttm:
                tl_dttm = lo_dttm
                tr_dttm = hi_dttm
            else:
                lo_dttm_next = get_solar_event(cur_date + ONE_DAY, "solar_midnight")
                if cur_dttm < lo_dttm_next:
                    tl_dttm = hi_dttm
                    tr_dttm = lo_dttm_next
                else:
                    tl_dttm = lo_dttm_next
                    tr_dttm = nxt_noon
    
            # Get elevations at bracketing points
            tl_elev = self.calculate_position(tl_dttm)[0]  # elevation is first value
            tr_elev = self.calculate_position(tr_dttm)[0]
            
            # Add fallback at the end of the method
            if not tl_dttm or not tr_dttm:
                    # Fallback to elevation trend method
                    future = cur_dttm + timedelta(minutes=15)
                    future_elev = self.calculate_position(future)[0]
                    current_elev = self.calculate_position(cur_dttm)[0]
                    return "rising" if future_elev > current_elev else "setting"
            
            
            # Final validation before return
            if tr_elev > tl_elev:
                result = "rising"
            else:
                result = "setting"
                
            # Validate result before returning
            if result not in ["rising", "setting"]:
                raise ValueError(f"Invalid direction value: {result}")
                
            return result
            
        except Exception as e:
            _LOGGER.warning("Error in sun_direction: %s. Using fallback method.", e)
            # Fallback to elevation trend method
            future = cur_dttm + timedelta(minutes=15)
            future_elev = self.calculate_position(future)[0]
            current_elev = self.calculate_position(cur_dttm)[0]
            direction = "rising" if future_elev > current_elev else "setting"
            _LOGGER.debug("Using fallback direction: %s (%.2f° -> %.2f°)", direction, current_elev, future_elev)
            return direction


class BaseSolEntity:
    """Base class for Sol entities handling common scheduling and update logic."""
    
    def __init__(self, base_name, unique_suffix, name=None):
        """
        Initialize base entity with consistent naming conventions.
        
        Args:
            base_name: The descriptive part of the name
            unique_suffix: The unique identifier suffix
            name: The prefix for entity names (defaults to NAME from const.py)
        """
        if name is None:
            name = NAME
            
        formatted_name = f"{name} - {base_name}"
        self._attr_name = ' '.join(word.capitalize() for word in formatted_name.split())
        self._attr_unique_id = f"{DOMAIN}_{slugify(unique_suffix)}"
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
            # Call sensor-specific update logic
            next_update_time = await self._async_update_logic(now)
            self._next_update = next_update_time
            
            # Handle scheduling
            if next_update_time:
                # Ensure we don't schedule in the past
                if next_update_time <= dt_util.utcnow():
                    next_update_time = dt_util.utcnow() + timedelta(seconds=5)
                    _LOGGER.warning("Rescheduling %s to %s", self.name, next_update_time)
                
                # Cancel existing update before scheduling new one
                self.cancel_scheduled_update()
                
                # Schedule next update
                self._unsub_update = async_track_point_in_time(
                    self.hass, self.async_update, next_update_time
                )
                _LOGGER.debug("Scheduled next update for %s at %s", self.name, next_update_time)
            else:
                # No update time returned - cancel any existing updates
                self.cancel_scheduled_update()
                _LOGGER.warning("No update time returned for %s", self.name)
            
            # Set entity as available after successful update
            self._attr_available = True
            
            # Write state to Home Assistant
            if self.entity_id:
                self.async_write_ha_state()
                
        except Exception as e:
            _LOGGER.error("Error updating %s: %s", self.name, e, exc_info=True)
            
            # Set entity as unavailable on error
            self._attr_available = False
            
            # Schedule retry in 5 minutes
            next_update_time = dt_util.utcnow() + timedelta(minutes=5)
            
            # Cancel existing update and schedule retry
            self.cancel_scheduled_update()
            self._unsub_update = async_track_point_in_time(
                self.hass, self.async_update, next_update_time
            )
            
            # Write state to Home Assistant
            if self.entity_id:
                self.async_write_ha_state()

    async def _async_update_logic(self, now):
        """Sensor-specific update logic to be implemented by subclasses.
        
        Should return the next update time or None if no scheduling needed.
        """
        raise NotImplementedError("Subclasses must implement this method")

    def _get_sun_direction_with_fallback(self, now, sun_helper):
        """Get sun direction with fallback to elevation trend method."""
        try:
            direction = sun_helper.sun_direction(now)
            if direction not in ["rising", "setting"]:
                raise ValueError(f"Invalid direction: {direction}")
            _LOGGER.debug("Sun direction determined: %s", direction)
            return direction
        except Exception as e:
            _LOGGER.warning("Error getting sun direction: %s. Using elevation trend", e)
            # Fallback to elevation trend method - reuse current elevation if available
            future = now + timedelta(minutes=5)
            future_elev = sun_helper.calculate_position(future)[0]
            current_elev = sun_helper.calculate_position(now)[0]
            direction = "rising" if future_elev > current_elev else "setting"
            _LOGGER.debug("Using fallback direction: %s (%.2f° -> %.2f°)", direction, current_elev, future_elev)
            return direction

    def _get_current_elevation(self, now, sun_helper):
        """Get current elevation with error handling."""
        try:
            current_elev, azimuth = sun_helper.calculate_position(now)
            return current_elev, azimuth
        except Exception as e:
            _LOGGER.error("Error getting sun position: %s", e)
            return None, None

    def _get_solar_event_fallback(self, now, sun_helper):
        """Get solar event fallback when elevation events are not found."""
        try:
            next_peak = sun_helper.get_peak_elevation_time(now)
            next_midnight = sun_helper.get_next_solar_midnight(now)
            
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
                
            return event_time
            
        except Exception as e:
            _LOGGER.error("Error getting solar events for fallback: %s", e)
            return now + timedelta(minutes=5)

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
        
        Returns:
            Tuple (normalized_value, previous_solstice, next_solstice)
        """
        # Use current UTC time if not specified
        if date_time is None:
            date_time = datetime.now(timezone.utc)
        else:
            date_time = date_time.astimezone(timezone.utc)
        
        # Create observer
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
