"""BodyObserver class for calculating celestial body positions using Skyfield."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from skyfield.api import Topos, load, Time
from skyfield import almanac
from skyfield.positionlib import Apparent
from skyfield.earthlib import refraction
import numpy as np

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class BodyObserver:
    """Observer for calculating celestial body positions and events."""
    
    def __init__(
        self,
        entry_id: str,
        body,
        search_start: Optional[datetime] = None,
        search_end: Optional[datetime] = None,
        horizon: float = 0.0,
        hass: Optional[HomeAssistant] = None
    ):
        """Initialize BodyObserver.
        
        Args:
            entry_id: Config entry ID to retrieve location data from
            body: Skyfield body object (from const.py)
            search_start: Start time for event searches (defaults to now)
            search_end: End time for event searches (defaults to search_start + 365 days)
            horizon: Horizon angle in degrees (defaults to 0)
            hass: Home Assistant instance (needed for temperature sensor entity retrieval)
        """
        # Import here to avoid circular imports
        from .config_store import get_config_entry_data
        
        # Get config data
        config_data = get_config_entry_data(entry_id)
        if not config_data:
            raise ValueError(f"No config data found for entry_id: {entry_id}")
        
        # Store hass for entity state retrieval
        self.hass = hass
        self._entry_id = entry_id
        self._config_data = config_data
        
        # Extract location data (always from config, not overrideable)
        latitude = config_data.get('latitude')
        self._longitude = config_data.get('longitude')
        self._elevation = config_data.get('elevation', 0)
        
        if latitude is None or self._longitude is None:
            raise ValueError(f"Missing latitude or longitude in config for entry_id: {entry_id}")
        
        # Clamp latitude to avoid exact pole issues (90°/-90°)
        if latitude > 89.9999:
            latitude = 89.9999
        elif latitude < -89.9999:
            latitude = -89.9999
        
        self._latitude = latitude
        
        # Get body (always from constants, not overrideable)
        self._body = body
        
        # Set search window
        if search_start is None:
            search_start = dt_util.now()
        self._search_start = search_start
        
        if search_end is None:
            search_end = search_start + timedelta(days=365)
        self._search_end = search_end
        
        self._horizon = horizon
        
        # Load ephemeris and timescale (shared across instances)
        # Use cached ephemeris from const.py to avoid blocking I/O
        from .const import (
            eph,
            ts as const_ts,
            AZIMUTH_TOLERANCE_BASE,
            AZIMUTH_TOLERANCE_MIN,
            AZIMUTH_TRANSIT_FOCUS_WINDOW_MINUTES,
            AZIMUTH_TRANSIT_RATE_THRESHOLD_DEG_PER_SEC,
            AZIMUTH_SINGULARITY_RATE_THRESHOLD_DEG_PER_SEC,
            AZIMUTH_SINGULARITY_GUARD_WINDOW_SECONDS,
            ELEVATION_TOLERANCE,
        )
        self._ts = const_ts
        self._eph = eph
        self._azimuth_tolerance_base = AZIMUTH_TOLERANCE_BASE
        self._azimuth_tolerance_min = AZIMUTH_TOLERANCE_MIN
        self._azimuth_transit_focus_window_minutes = AZIMUTH_TRANSIT_FOCUS_WINDOW_MINUTES
        self._azimuth_transit_rate_threshold_deg_per_sec = AZIMUTH_TRANSIT_RATE_THRESHOLD_DEG_PER_SEC
        self._azimuth_singularity_rate_threshold_deg_per_sec = AZIMUTH_SINGULARITY_RATE_THRESHOLD_DEG_PER_SEC
        self._azimuth_singularity_guard_window_seconds = AZIMUTH_SINGULARITY_GUARD_WINDOW_SECONDS
        self._elevation_tolerance = ELEVATION_TOLERANCE
        
        # Create observer location
        self._observer_location = Topos(
            latitude_degrees=self._latitude,
            longitude_degrees=self._longitude,
            elevation_m=self._elevation
        )
        
        # Create earth + observer position
        self._earth_observer = self._eph['earth'] + self._observer_location
        
        # Convert search times to Skyfield Time objects
        self._t0 = self._ts.from_datetime(self._search_start)
        self._t1 = self._ts.from_datetime(self._search_end)
        
        # Calculate search window duration for previous events
        self._window_duration = self._search_end - self._search_start
        self._last_step_search_capped = False
        self._last_step_search_windows_used = 0
        self._last_step_search_result_count = 0
    
    def _estimate_temperature(self, date: datetime) -> float:
        """Estimate average temperature based on latitude and date.
        
        Args:
            date: datetime object
            
        Returns:
            Estimated temperature in Celsius
        """
        
        # Base equator-to-pole gradient: ~25°C at equator, drops to -10°C at poles
        base_temp = 25 - 0.4 * abs(self._latitude)
        
        # Get day of year (1-365/366)
        day_of_year = date.timetuple().tm_yday
        
        # Seasonal amplitude increases with latitude (zero at equator, ±15°C at poles)
        if self._latitude >= 0:  # Northern hemisphere
            # Summer solstice around day 172 (June 21)
            phase_offset = 2 * math.pi * (172 - 1) / 365.25
        else:  # Southern hemisphere
            # Summer solstice around day 355 (December 21)
            phase_offset = 2 * math.pi * (355 - 1) / 365.25
        
        seasonal_factor = -15 * (abs(self._latitude) / 90) * math.cos(2 * math.pi * (day_of_year - 1) / 365.25 - phase_offset)
        
        return base_temp + seasonal_factor
    
    def _get_temperature_from_entity(self) -> Optional[float]:
        """Get temperature from Home Assistant entity.
        
        Returns:
            Temperature in Celsius or None if unavailable
        """
        if not self.hass:
            return None
        
        temperature_entity_id = self._config_data.get('temperature_entity_id')
        if not temperature_entity_id:
            return None
        
        try:
            state = self.hass.states.get(temperature_entity_id)
            if state is None or state.state in ('unavailable', 'unknown', None):
                return None
            
            # Try to parse state as float
            try:
                return float(state.state)
            except (ValueError, TypeError):
                _LOGGER.warning(f"Temperature entity {temperature_entity_id} returned non-numeric value: {state.state}")
                return None
        except Exception as e:
            _LOGGER.debug(f"Error getting temperature from entity {temperature_entity_id}: {e}")
            return None
    
    def _get_angular_radius(self, time_obj: Time) -> Optional[float]:
        """Get angular radius of the celestial body in degrees.
        
        Only calculates for Sun and Moon. Returns None for other bodies.
        
        Args:
            time_obj: Skyfield Time object
            
        Returns:
            Angular radius in degrees, or None if body is not Sun/Moon
        """
        # Only calculate for Sun and Moon
        sun = self._eph['sun']
        moon = self._eph['moon']
        # Check if body is Sun or Moon (handle Moon represented as sum of vectors)
        body_str = str(self._body)
        is_sun = self._body is sun
        is_moon = (self._body is moon) or ('MOON' in body_str.upper() or '301' in body_str)
        
        if not is_sun and not is_moon:
            return None
        
        try:
            # Get distance to body
            astrometric = self._earth_observer.at(time_obj).observe(self._body)
            apparent = astrometric.apparent()
            distance_km = apparent.distance().km
            
            # Get body radius from Skyfield if available, otherwise use constants
            # Try to get radius from Skyfield's built-in properties
            radius_km = None
            if hasattr(self._body, 'radius_km'):
                radius_km = self._body.radius_km
            elif hasattr(self._body, 'equatorial_radius_km'):
                radius_km = self._body.equatorial_radius_km
            
            # Fallback to constants if Skyfield doesn't provide radius
            if radius_km is None:
                if self._body is sun:
                    radius_km = 696340.0  # Sun's radius in km
                elif self._body is moon:
                    radius_km = 1737.1  # Moon's radius in km
            
            if radius_km is None:
                return None
            
            # Calculate angular radius: arcsin(radius / distance)
            angular_radius_rad = math.asin(radius_km / distance_km)
            angular_radius_deg = math.degrees(angular_radius_rad)
            
            return angular_radius_deg
            
        except Exception as e:
            _LOGGER.debug(f"Error calculating angular radius: {e}")
            return None
    
    def _get_temperature_for_refraction(self, date: datetime) -> float:
        """Get temperature for refraction calculation based on configured mode.
        
        Args:
            date: datetime object for temperature estimation
            
        Returns:
            Temperature in Celsius (always returned)
        """
        # Default to 'estimator' for backward compatibility with old config entries
        temperature_mode = self._config_data.get('temperature_mode', 'estimator')
        
        if temperature_mode == 'manual':
            # Manual temperature mode - get from config
            temp = self._config_data.get('temperature_C')
            if temp is None:
                raise ValueError(f"temperature_C not set in config for manual temperature mode (entry_id: {self._entry_id})")
            return float(temp)
        elif temperature_mode == 'sensor':
            # Try to get from sensor, fallback to estimator if unavailable
            temp = self._get_temperature_from_entity()
            if temp is not None:
                return temp
            # Fallback to estimator
            return self._estimate_temperature(date)
        else:  # 'estimator' or default
            # Use temperature estimator
            return self._estimate_temperature(date)
    
    def _get_refraction_params(self, t: Time | datetime) -> tuple[float, Optional[float]]:
        """Get temperature and pressure for refraction calculation.
        
        This method ensures consistent temperature/pressure calculation across all methods.
        
        Args:
            t: Skyfield Time object (or array of Time objects) or datetime object for temperature estimation
            
        Returns:
            Tuple of (temperature_C, pressure_mbar) where temperature is always returned,
            and pressure is calculated from elevation during config for auto mode
        """
        # Handle arrays - extract first Time object if array (temperature is day-based, so first is fine)
        if isinstance(t, Time):
            try:
                # Check if it's an array by trying to access shape attribute
                shape = t.shape
                # It's an array - use first element
                t = t[0] if len(t) > 0 else t
            except (AttributeError, TypeError):
                # Single Time object (no shape attribute or len() raises TypeError)
                pass
        
        # Convert Time to datetime for temperature estimation
        if isinstance(t, Time):
            date = t.utc_datetime()
        else:
            date = t
        
        # Ensure timezone-aware datetime
        if not date.tzinfo:
            date = date.replace(tzinfo=timezone.utc)
        
        # Get temperature (always returned)
        temperature = self._get_temperature_for_refraction(date)
        
        # Get pressure
        # Check for backward compatibility: if pressure_mode doesn't exist, check old temperature_mode
        pressure_mode = self._config_data.get('pressure_mode')
        if pressure_mode is None:
            # Backward compatibility: old config where temperature_mode == 'manual' meant pressure was manual
            temperature_mode = self._config_data.get('temperature_mode', 'estimator')
            if temperature_mode == 'manual':
                pressure_mode = 'manual'
            else:
                pressure_mode = 'auto'
        
        if pressure_mode == 'manual':
            pressure = self._config_data.get('pressure_mbar')
        else:  # 'auto' or default
            pressure = self._config_data.get('pressure_mbar')  # Pressure calculated from elevation during config
        
        return temperature, pressure
    
    def set_search_window(
        self,
        search_start: Optional[datetime] = None,
        search_end: Optional[datetime] = None,
        horizon: Optional[float] = None
    ):
        """Update search window parameters.
        
        Args:
            search_start: New start time (only updates if provided)
            search_end: New end time (only updates if provided)
            horizon: New horizon angle (only updates if provided)
        """
        if search_start is not None:
            self._search_start = search_start
            self._t0 = self._ts.from_datetime(search_start)
        
        if search_end is not None:
            self._search_end = search_end
            self._t1 = self._ts.from_datetime(search_end)
        elif search_start is not None:
            # If only search_start was updated, recalculate search_end to 24 hours
            self._search_end = search_start + timedelta(hours=24)
            self._t1 = self._ts.from_datetime(self._search_end)
        
        if horizon is not None:
            self._horizon = horizon
        
        # Recalculate window duration
        self._window_duration = self._search_end - self._search_start
    
    def _convert_to_local_time(self, utc_time: datetime) -> datetime:
        """Convert UTC datetime to local system time."""
        if utc_time is None:
            return None
        return dt_util.as_local(utc_time)
    
    def position(self, time: Optional[Time | datetime] = None, apply_refraction: bool = True) -> tuple[float, float, float]:
        """Get position (altitude, azimuth, distance) at specified time.
        
        Args:
            time: Skyfield Time object or datetime object. If None, uses observer's search_start time.
            apply_refraction: If True, apply atmospheric refraction to elevation. Defaults to True.
            
        Returns:
            Tuple of (elevation, azimuth, distance)
        """
        
        # Convert to Time object if needed
        if time is None:
            t = self._ts.from_datetime(self._search_start)
        elif isinstance(time, Time):
            t = time
        else:
            # datetime object
            t = self._ts.from_datetime(time)
        
        astrometric = self._earth_observer.at(t).observe(self._body)
        apparent = astrometric.apparent()
        
        # Always get unrefracted (geometric) elevation first
        alt, az, distance = apparent.altaz()
        geometric_elevation = alt.degrees
        azimuth = az.degrees
        
        # Check if we're dealing with arrays (from find_discrete)
        is_array = hasattr(geometric_elevation, 'shape') and len(geometric_elevation.shape) > 0
        
        # NOTE:
        # We apply refraction manually (with taper below -1°) instead of relying
        # on Skyfield's built-in altaz() refraction path.
        #
        # Why:
        # - Skyfield has a sharp behavior change around -1° below horizon that
        #   can interfere with horizon-near event timing for this integration.
        # - This tapered approach smooths the transition between -1° and -6°.
        # - This manual path needs explicit pressure input, so pressure_mbar is
        #   estimated from elevation (auto mode) via ambiance when not provided.
        #
        # Future cleanup target:
        # If Skyfield adds native tapered refraction (or equivalent behavior that
        # works for our horizon crossing use-cases), remove this custom taper and
        # reevaluate removing the ambiance pressure estimation dependency.
        #
        # Related discussion:
        # https://github.com/skyfielders/python-skyfield/issues/1069
        #
        # Apply manual refraction with tapering (only if requested)
        if apply_refraction:
            # Get temperature and pressure for refraction (using consistent method)
            temperature, pressure = self._get_refraction_params(t)
            
            pressure_for_refraction = pressure
            
            if temperature is not None:
                if is_array:
                    # Handle array inputs using numpy operations
                    final_elevation = np.zeros_like(geometric_elevation)
                    
                    # For elevations >= -1°, use full refraction for this specific elevation
                    mask_above_minus_one = geometric_elevation >= -1.0
                    if np.any(mask_above_minus_one):
                        refraction_angle = refraction(geometric_elevation[mask_above_minus_one], temperature_C=temperature, pressure_mbar=pressure_for_refraction)
                        final_elevation[mask_above_minus_one] = geometric_elevation[mask_above_minus_one] + refraction_angle
                    
                    # For elevations between -1° and -6°, taper refraction
                    mask_between = (geometric_elevation >= -6.0) & (geometric_elevation < -1.0)
                    if np.any(mask_between):
                        # Calculate refraction at -1° (the reference point)
                        refraction_at_minus_one = refraction(-1.0, temperature_C=temperature, pressure_mbar=pressure_for_refraction)
                        # Calculate taper factor: 1.0 at -1°, 0.0 at -6°
                        taper_factor = (geometric_elevation[mask_between] + 6.0) / 5.0
                        # Apply tapered refraction
                        applied_refraction = refraction_at_minus_one * taper_factor
                        final_elevation[mask_between] = geometric_elevation[mask_between] + applied_refraction
                    
                    # For elevations < -6°, no refraction
                    mask_below_minus_six = geometric_elevation < -6.0
                    if np.any(mask_below_minus_six):
                        final_elevation[mask_below_minus_six] = geometric_elevation[mask_below_minus_six]
                else:
                    # Handle scalar input
                    if geometric_elevation >= -1.0:
                        # For elevations >= -1°, use full refraction for this specific elevation
                        refraction_angle = refraction(geometric_elevation, temperature_C=temperature, pressure_mbar=pressure_for_refraction)
                        final_elevation = geometric_elevation + refraction_angle
                    elif geometric_elevation >= -6.0:
                        # For elevations between -1° and -6°, taper refraction
                        # Calculate refraction at -1° (the reference point)
                        refraction_at_minus_one = refraction(-1.0, temperature_C=temperature, pressure_mbar=pressure_for_refraction)
                        # Calculate taper factor: 1.0 at -1°, 0.0 at -6°
                        taper_factor = (geometric_elevation + 6.0) / 5.0
                        # Apply tapered refraction
                        applied_refraction = refraction_at_minus_one * taper_factor
                        final_elevation = geometric_elevation + applied_refraction
                    else:
                        # For elevations < -6°, no refraction
                        final_elevation = geometric_elevation
            else:
                # No temperature available, return geometric elevation
                final_elevation = geometric_elevation
        else:
            # Skip refraction, use geometric elevation
            final_elevation = geometric_elevation
        
        # Handle NaN azimuth at the poles (azimuth is undefined at exactly 90°N or 90°S)
        if is_array:
            # Array input - check for NaN values
            nan_mask = np.isnan(azimuth)
            if np.any(nan_mask):
                # Replace NaN values based on latitude
                if self._latitude > 0:
                    azimuth = np.where(nan_mask, 180.0, azimuth)  # North Pole
                else:
                    azimuth = np.where(nan_mask, 0.0, azimuth)  # South Pole
            # Normalize azimuth to [0, 360) range
            azimuth = azimuth % 360.0
        else:
            # Scalar input - check for NaN
            if math.isnan(azimuth):
                # At North Pole (90°N): all directions are south (180°)
                # At South Pole (90°S): all directions are north (0°)
                if self._latitude > 0:
                    azimuth = 180.0  # North Pole
                else:
                    azimuth = 0.0  # South Pole
            # Normalize azimuth to [0, 360) range
            azimuth = azimuth % 360.0
        
        
        return final_elevation, azimuth, distance.au
    
    @property
    def next_transit(self) -> Optional[datetime]:
        """Get next meridian transit (highest point)."""
        try:
            # Find meridian transits (returns both upper and lower)
            f = almanac.meridian_transits(self._eph, self._body, self._observer_location)
            # Calculate step_days as 40% of search window
            window_days = (self._search_end - self._search_start).total_seconds() / 86400.0
            f.step_days = 0.4 * window_days
            times, is_upper = almanac.find_discrete(self._t0, self._t1, f)
            
            if len(times) > 0:
                # Get first upper transit (highest point) after search_start
                search_start_utc = dt_util.as_utc(self._search_start)
                for i, t in enumerate(times):
                    if is_upper[i]:  # Only upper transits (highest point)
                        transit_time = t.utc_datetime()
                        if transit_time > search_start_utc:
                            return self._convert_to_local_time(transit_time)
            return None
        except Exception as e:
            _LOGGER.error(f"Error calculating next_transit: {e}")
            return None
    
    @property
    def previous_transit(self) -> Optional[datetime]:
        """Get previous meridian transit."""
        try:
            # Search backward from search_start
            t_prev_start = self._ts.from_datetime(self._search_start - self._window_duration)
            t_prev_end = self._t0
            
            f = almanac.meridian_transits(self._eph, self._body, self._observer_location)
            # Calculate step_days as 40% of search window (same as next_transit)
            window_days = self._window_duration.total_seconds() / 86400.0
            f.step_days = 0.4 * window_days
            times, is_upper = almanac.find_discrete(t_prev_start, t_prev_end, f)
            
            if len(times) > 0:
                # Get last upper transit (highest point) before search_start
                search_start_utc = dt_util.as_utc(self._search_start)
                for i in range(len(times) - 1, -1, -1):
                    if is_upper[i]:  # Only upper transits (highest point)
                        transit_time = times[i].utc_datetime()
                        if transit_time < search_start_utc:
                            return self._convert_to_local_time(transit_time)
            return None
        except Exception as e:
            _LOGGER.error(f"Error calculating previous_transit: {e}")
            return None
    
    @property
    def next_antitransit(self) -> Optional[datetime]:
        """Get next anti-meridian transit (lowest point)."""
        try:
            # Find meridian transits (returns both upper and lower)
            f = almanac.meridian_transits(self._eph, self._body, self._observer_location)
            # Calculate step_days as 40% of search window
            window_days = (self._search_end - self._search_start).total_seconds() / 86400.0
            f.step_days = 0.4 * window_days
            times, is_upper = almanac.find_discrete(self._t0, self._t1, f)
            
            if len(times) > 0:
                # Get first lower transit (lowest point) after search_start
                search_start_utc = dt_util.as_utc(self._search_start)
                for i, t in enumerate(times):
                    if not is_upper[i]:  # Lower transits (lowest point)
                        transit_time = t.utc_datetime()
                        if transit_time > search_start_utc:
                            return self._convert_to_local_time(transit_time)
            return None
        except Exception as e:
            _LOGGER.error(f"Error calculating next_antitransit: {e}")
            return None
    
    @property
    def previous_antitransit(self) -> Optional[datetime]:
        """Get previous anti-meridian transit."""
        try:
            # Search backward from search_start
            t_prev_start = self._ts.from_datetime(self._search_start - self._window_duration)
            t_prev_end = self._t0
            
            f = almanac.meridian_transits(self._eph, self._body, self._observer_location)
            # Calculate step_days as 40% of search window (same as next_antitransit)
            window_days = self._window_duration.total_seconds() / 86400.0
            f.step_days = 0.4 * window_days
            times, is_upper = almanac.find_discrete(t_prev_start, t_prev_end, f)
            
            if len(times) > 0:
                # Get last lower transit (lowest point) before search_start
                search_start_utc = dt_util.as_utc(self._search_start)
                for i in range(len(times) - 1, -1, -1):
                    if not is_upper[i]:  # Lower transits (lowest point)
                        transit_time = times[i].utc_datetime()
                        if transit_time < search_start_utc:
                            return self._convert_to_local_time(transit_time)
            return None
        except Exception as e:
            _LOGGER.error(f"Error calculating previous_antitransit: {e}")
            return None
    
    @property
    def next_rising(self) -> Optional[datetime]:
        """Get next rising time (altitude crosses horizon)."""
        try:
            # Use find_risings for rising events
            # Signature: find_risings(observer, target, start_time, end_time, horizon_degrees=None)
            # observer is earth + observer_location
            times, _ = almanac.find_risings(self._earth_observer, self._body, self._t0, self._t1, horizon_degrees=self._horizon)
            
            if len(times) > 0:
                # Find first rising after search_start
                search_start_utc = dt_util.as_utc(self._search_start)
                for t in times:
                    rise_time = t.utc_datetime()
                    # Ensure both are timezone-aware for comparison
                    if not rise_time.tzinfo:
                        rise_time = rise_time.replace(tzinfo=timezone.utc)
                    if rise_time > search_start_utc:
                        return self._convert_to_local_time(rise_time)
            return None
        except Exception as e:
            _LOGGER.error(f"Error calculating next_rising: {e}", exc_info=True)
            return None
    
    @property
    def previous_rising(self) -> Optional[datetime]:
        """Get previous rising time."""
        try:
            # Search backward
            t_prev_start = self._ts.from_datetime(self._search_start - self._window_duration)
            t_prev_end = self._t0
            
            # Use find_risings for rising events
            # Signature: find_risings(observer, target, start_time, end_time, horizon_degrees=None)
            # observer is earth + observer_location
            times, _ = almanac.find_risings(self._earth_observer, self._body, t_prev_start, t_prev_end, horizon_degrees=self._horizon)
            
            if len(times) > 0:
                # Find last rising before search_start
                search_start_utc = dt_util.as_utc(self._search_start)
                for t in reversed(times):
                    rise_time = t.utc_datetime()
                    # Ensure both are timezone-aware for comparison
                    if not rise_time.tzinfo:
                        rise_time = rise_time.replace(tzinfo=timezone.utc)
                    if rise_time < search_start_utc:
                        return self._convert_to_local_time(rise_time)
            return None
        except Exception as e:
            _LOGGER.error(f"Error calculating previous_rising: {e}", exc_info=True)
            return None
    
    @property
    def next_setting(self) -> Optional[datetime]:
        """Get next setting time (altitude crosses horizon)."""
        try:
            # Use find_settings for setting events
            # Signature: find_settings(observer, target, start_time, end_time, horizon_degrees=None)
            # observer is earth + observer_location
            times, _ = almanac.find_settings(self._earth_observer, self._body, self._t0, self._t1, horizon_degrees=self._horizon)
            
            if len(times) > 0:
                # Find first setting after search_start
                search_start_utc = dt_util.as_utc(self._search_start)
                for t in times:
                    set_time = t.utc_datetime()
                    # Ensure both are timezone-aware for comparison
                    if not set_time.tzinfo:
                        set_time = set_time.replace(tzinfo=timezone.utc)
                    if set_time > search_start_utc:
                        return self._convert_to_local_time(set_time)
            return None
        except Exception as e:
            _LOGGER.error(f"Error calculating next_setting: {e}", exc_info=True)
            return None
    
    @property
    def previous_setting(self) -> Optional[datetime]:
        """Get previous setting time."""
        try:
            # Search backward
            t_prev_start = self._ts.from_datetime(self._search_start - self._window_duration)
            t_prev_end = self._t0
            
            # Use find_settings for setting events
            # Signature: find_settings(observer, target, start_time, end_time, horizon_degrees=None)
            # observer is earth + observer_location
            times, _ = almanac.find_settings(self._earth_observer, self._body, t_prev_start, t_prev_end, horizon_degrees=self._horizon)
            
            if len(times) > 0:
                # Find last setting before search_start
                search_start_utc = dt_util.as_utc(self._search_start)
                for t in reversed(times):
                    set_time = t.utc_datetime()
                    # Ensure both are timezone-aware for comparison
                    if not set_time.tzinfo:
                        set_time = set_time.replace(tzinfo=timezone.utc)
                    if set_time < search_start_utc:
                        return self._convert_to_local_time(set_time)
            return None
        except Exception as e:
            _LOGGER.error(f"Error calculating previous_setting: {e}", exc_info=True)
            return None
    
    @property
    def current_declination(self) -> float:
        """Get current declination in degrees."""
        try:
            t = self._ts.from_datetime(self._search_start)
            astrometric = self._earth_observer.at(t).observe(self._body)
            apparent = astrometric.apparent()
            ra, dec, distance = apparent.radec()
            return dec.degrees
        except Exception as e:
            _LOGGER.error(f"Error calculating current_declination: {e}")
            return 0.0
    
    
    @property
    def current_elongation(self) -> float:
        """Get current elongation (angular separation from sun) in degrees."""
        try:
            t = self._ts.from_datetime(self._search_start)
            
            # Get position of target body
            astrometric_body = self._earth_observer.at(t).observe(self._body)
            apparent_body = astrometric_body.apparent()
            
            # Get position of sun
            sun = self._eph['sun']
            astrometric_sun = self._earth_observer.at(t).observe(sun)
            apparent_sun = astrometric_sun.apparent()
            
            # Calculate angular separation
            separation = apparent_body.separation_from(apparent_sun)
            return separation.degrees
        except Exception as e:
            _LOGGER.error(f"Error calculating current_elongation: {e}")
            return 0.0

    @property
    def current_phase_angle(self) -> float:
        """Get current phase angle: body's ecliptic longitude minus Sun's, mod 360°.

        Returns 0.0–360.0. For the Moon: 0°=New Moon, 90°=First Quarter,
        180°=Full Moon, 270°=Third Quarter.
        """
        try:
            t = self._ts.from_datetime(self._search_start)
            sun = self._eph['sun']
            _, ecl_lon_body, _ = self._earth_observer.at(t).observe(self._body).apparent().ecliptic_latlon()
            _, ecl_lon_sun, _  = self._earth_observer.at(t).observe(sun).apparent().ecliptic_latlon()
            return (ecl_lon_body.degrees - ecl_lon_sun.degrees) % 360
        except Exception as e:
            _LOGGER.error(f"Error calculating current_phase_angle: {e}")
            return 0.0

    @property
    def current_parallactic_angle(self) -> float:
        """Get current parallactic angle in degrees (0 to 360).

        The parallactic angle is the angle between the direction 'up' (toward
        the zenith) and 'north' (toward the north celestial pole) at the
        body's current position in the sky.  It encodes both the observer's
        hemisphere and the body's position across the sky throughout the night.

        Returns:
            Angle in degrees, 0 to 360.
            Returns 0.0 on error.
        """
        try:
            t = self._ts.from_datetime(self._search_start)
            apparent = self._earth_observer.at(t).observe(self._body).apparent()
            ra, dec, _ = apparent.radec()

            # Hour angle: LAST - RA  (in hours → convert to radians)
            last = t.gast + self._longitude / 15.0
            H_rad = math.radians((last - ra.hours) * 15.0)
            dec_rad = math.radians(dec.degrees)
            lat_rad = math.radians(self._latitude)

            q = math.atan2(
                math.cos(lat_rad) * math.sin(H_rad),
                math.sin(lat_rad) * math.cos(dec_rad)
                    - math.cos(lat_rad) * math.sin(dec_rad) * math.cos(H_rad),
            )
            return math.degrees(q) % 360.0
        except Exception as e:
            _LOGGER.error(f"Error calculating current_parallactic_angle: {e}")
            return 0.0

    def get_time_at_parallactic_angle(
        self,
        current_q: float,
        direction: int,
        search_start: Optional[datetime] = None,
        search_end: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """Return the next time the parallactic angle crosses the next integer
        boundary in the given direction.

        Unlike the phase angle, the parallactic angle is not monotonic, so the
        caller must supply the current direction of change (+1 or -1).  The
        threshold is the next whole-degree boundary: floor(current_q)+1 when
        increasing, ceil(current_q)-1 when decreasing.

        Args:
            current_q: Current parallactic angle in degrees.
            direction: +1 (increasing) or -1 (decreasing).
            search_start: Start of search window (defaults to self._search_start).
            search_end: End of search window (defaults to search_start + 6 hours).

        Returns:
            Local datetime of the next crossing, or None if not found.
        """
        if direction == 0:
            return None

        try:
            from .const import AZIMUTH_TOLERANCE_BASE

            if search_start is None:
                search_start = self._search_start
            if search_end is None:
                search_end = search_start + timedelta(hours=6)

            t_start = self._ts.from_datetime(search_start)
            t_end   = self._ts.from_datetime(search_end)

            # Use the next integer boundary as the threshold so the update fires
            # exactly when the angle crosses a whole degree, not at the half-degree.
            if direction == 1:
                threshold = math.floor(current_q) + 1.0
            else:
                threshold = math.ceil(current_q) - 1.0
            lat_rad = math.radians(self._latitude)

            def q_crossed_threshold(t):
                apparent = self._earth_observer.at(t).observe(self._body).apparent()
                ra, dec, _ = apparent.radec()
                last = t.gast + self._longitude / 15.0
                H_rad = np.radians((last - ra.hours) * 15.0)
                dec_rad = np.radians(dec.degrees)
                q = np.degrees(np.arctan2(
                    math.cos(lat_rad) * np.sin(H_rad),
                    math.sin(lat_rad) * np.cos(dec_rad)
                        - math.cos(lat_rad) * np.sin(dec_rad) * np.cos(H_rad),
                )) % 360.0
                if direction == 1:
                    crossed = q >= threshold
                else:
                    crossed = q <= threshold
                if isinstance(crossed, np.ndarray):
                    return crossed.astype(int)
                return int(crossed)

            # 2-minute steps — parallactic angle changes at most ~15°/h
            q_crossed_threshold.step_days = 2.0 / (24 * 60)

            times, values = almanac.find_discrete(
                t_start, t_end, q_crossed_threshold, epsilon=AZIMUTH_TOLERANCE_BASE
            )

            for i, v in enumerate(values):
                if v == 1:
                    crossing = times[i].utc_datetime().replace(tzinfo=timezone.utc)
                    return self._convert_to_local_time(crossing)

            return None
        except Exception as e:
            _LOGGER.error(
                f"Error finding time at parallactic angle (current={current_q:.1f}°, "
                f"dir={direction}): {e}",
                exc_info=True,
            )
            return None

    def get_time_at_phase_angle(
        self,
        target_angle: float,
        search_start: Optional[datetime] = None,
        search_end: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """Return the next time the phase angle crosses target_angle (ascending).

        The phase angle always increases from 0° to 360° over one lunar cycle
        (~29.5 days), so only ascending crossings are detected.  The
        forward-distance approach handles the 357°→3° wrap-around naturally.

        Args:
            target_angle: Target phase angle in degrees (0–360).
            search_start: Start of search window (defaults to self._search_start).
            search_end: End of search window (defaults to search_start + 35 days).

        Returns:
            Local datetime of the crossing, or None if not found.
        """
        try:
            from .const import PHASE_ANGLE_TOLERANCE

            target_angle = target_angle % 360

            if search_start is None:
                search_start = self._search_start
            if search_end is None:
                search_end = search_start + timedelta(days=35)

            t_start = self._ts.from_datetime(search_start)
            t_end   = self._ts.from_datetime(search_end)
            sun = self._eph['sun']

            def phase_above_target(t):
                """Return 1 when phase angle has crossed target (ascending), 0 otherwise.

                Uses forward_dist = (phase - target) % 360.
                Values < 180° mean we are on the 'above' side of the target.
                This handles wrap-around (e.g. target=3°, phase=358°) correctly.
                """
                _, ecl_lon_b, _ = self._earth_observer.at(t).observe(self._body).apparent().ecliptic_latlon()
                _, ecl_lon_s, _ = self._earth_observer.at(t).observe(sun).apparent().ecliptic_latlon()
                phase = (ecl_lon_b.degrees - ecl_lon_s.degrees) % 360
                forward_dist = (phase - target_angle) % 360
                if isinstance(forward_dist, np.ndarray):
                    return (forward_dist < 180).astype(int)
                return int(forward_dist < 180)

            # 0.1 days ≈ 2.4 h — fine enough to detect the narrowest 3° bands (~6 h wide)
            phase_above_target.step_days = 0.1

            times, values = almanac.find_discrete(t_start, t_end, phase_above_target, epsilon=PHASE_ANGLE_TOLERANCE)

            for i, t in enumerate(times):
                if values[i] == 1:  # ascending crossing: phase just crossed target from below
                    crossing = t.utc_datetime()
                    if not crossing.tzinfo:
                        crossing = crossing.replace(tzinfo=timezone.utc)
                    return self._convert_to_local_time(crossing)

            return None
        except Exception as e:
            _LOGGER.error(f"Error finding time at phase angle {target_angle}: {e}", exc_info=True)
            return None

    @property
    def percent_illuminated(self) -> float:
        """Get percent of body illuminated (0-100). Only meaningful for Moon."""
        try:
            t = self._ts.from_datetime(self._search_start)
            
            # Get position of target body
            astrometric_body = self._earth_observer.at(t).observe(self._body)
            apparent_body = astrometric_body.apparent()
            
            # Get sun body object for fraction_illuminated
            sun = self._eph['sun']
            
            # Calculate fraction illuminated (0.0 to 1.0)
            # fraction_illuminated expects the sun body object, not the apparent position
            fraction = apparent_body.fraction_illuminated(sun)
            # Convert to percentage (0-100)
            return float(fraction * 100.0)
        except Exception as e:
            _LOGGER.error(f"Error calculating percent_illuminated: {e}")
            return 0.0
    
    @property
    def current_temperature(self) -> float:
        """Get current temperature used for refraction calculation.
        
        Returns:
            Temperature in Celsius based on configured mode (manual, estimator, or sensor)
        """
        temperature, _ = self._get_refraction_params(self._search_start)
        return temperature
    
    def calculate_azimuth_direction_from_subsolar(self, time: datetime | Time) -> int:
        """Calculate azimuth direction based on subsolar point position.
        
        This method determines whether azimuth is increasing or decreasing by comparing
        the subsolar point latitude to the observer's latitude.
        
        Args:
            time: Time to calculate direction at (datetime or Skyfield Time object)
            
        Returns:
            1 if azimuth is increasing (subsolar point south of observer)
            -1 if azimuth is decreasing (subsolar point north of observer)
        """
        try:
            # Determine body_key for logging
            body_key = "unknown"
            try:
                from .utils import get_body
                from .const import eph as const_eph
                for key in ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto"]:
                    body_obj = get_body(key, const_eph)
                    if body_obj is not None and self._body is body_obj:
                        body_key = key
                        break
            except Exception:
                pass
            
            # Convert to Skyfield Time if needed
            if isinstance(time, datetime):
                if time.tzinfo is None:
                    time = time.replace(tzinfo=timezone.utc)
                time_utc = time.astimezone(timezone.utc)
                t = self._ts.from_datetime(time_utc)
            else:
                t = time
            
            # Get apparent position of body
            astrometric = self._earth_observer.at(t).observe(self._body)
            apparent = astrometric.apparent()
            
            # Get right ascension and declination
            ra, dec, distance = apparent.radec()
            
            # Calculate azimuth direction using hour angle formula
            # φ = observer latitude, δ = declination, H = hour angle
            # Hour angle: H = Local Sidereal Time - Right Ascension
            # Local Sidereal Time = GMST + observer longitude (in hours)
            local_sidereal_time = t.gmst + (self._longitude / 15.0)
            hour_angle_hours = local_sidereal_time - ra.hours
            
            # Normalize hour angle to [-12, +12] hours range
            hour_angle_hours = ((hour_angle_hours + 12) % 24) - 12
            
            # Convert to radians
            phi_rad = math.radians(self._latitude)
            delta_rad = math.radians(dec.degrees)
            H_rad = math.radians(hour_angle_hours * 15.0)  # Convert hours to degrees, then to radians
            
            # Apply Deepseek formula for azimuth rate of change
            # dA/dt ∝ sin(φ) - cos(φ) * tan(δ) * cos(H)
            # The sign tells us if azimuth is increasing (+1) or decreasing (-1)
            direction_value = (math.sin(phi_rad) - 
                              math.cos(phi_rad) * math.tan(delta_rad) * math.cos(H_rad))
            
            # Get sign: +1 for increasing, -1 for decreasing
            if direction_value > 0:
                direction = 1
            elif direction_value < 0:
                direction = -1
            else:
                # Edge case: direction_value == 0
                # Use hemisphere as fallback: north = +1, south = -1
                direction = 1 if self._latitude >= 0 else -1
            
            return direction
                
        except Exception as e:
            _LOGGER.error(f"Error calculating azimuth direction from subsolar point: {e}", exc_info=True)
            return 1  # Default to increasing
    
    def get_time_at_azimuth(
        self, 
        target_azimuth: float, 
        direction: int, 
        search_end: Optional[datetime] = None,
        search_start: Optional[datetime] = None,
        step_days: Optional[float] = None
    ) -> Optional[datetime]:
        """Get time when azimuth reaches target value.
        
        Args:
            target_azimuth: Target azimuth in degrees (0-360)
            direction: Direction of movement (1 for increasing, -1 for decreasing)
            search_end: End time for search (defaults to search_start + 24 hours)
            search_start: Start time for search (defaults to self._search_start)
            step_days: Step size in days for find_discrete (defaults to 0.01)
            
        Returns:
            Datetime when azimuth reaches target, or None if not found
        """
        try:
            # Normalize target azimuth to 0-360 range
            target_azimuth = target_azimuth % 360
            
            # Set search start time
            if search_start is None:
                search_start = self._search_start
            
            # Set search end time
            if search_end is None:
                search_end = search_start + timedelta(hours=24)
            
            # Convert to Skyfield Time objects
            t_start = self._ts.from_datetime(search_start)
            t_end = self._ts.from_datetime(search_end)
            
            # Create function that checks if azimuth crosses target
            def azimuth_crosses_target(t):
                """Check if azimuth crosses target value."""
                # Use position() to get azimuth (handles arrays automatically)
                # Pass Time object directly to preserve array handling
                # Skip refraction calculation since azimuth doesn't need it
                _, current_az, _ = self.position(t, apply_refraction=False)
                
                # Handle both scalar and array inputs
                is_array = isinstance(current_az, np.ndarray) or hasattr(current_az, '__len__')
                
                # Handle NaN azimuth at the poles (azimuth is undefined at exactly 90°N or 90°S)
                if is_array:
                    # Array input - check for NaN values
                    nan_mask = np.isnan(current_az)
                    if np.any(nan_mask):
                        # Replace NaN values based on latitude
                        if self._latitude > 0:
                            current_az = np.where(nan_mask, 180.0, current_az)  # North Pole
                        else:
                            current_az = np.where(nan_mask, 0.0, current_az)  # South Pole
                else:
                    # Scalar input - check for NaN
                    if np.isnan(current_az):
                        if self._latitude > 0:
                            current_az = 180.0  # North Pole
                        else:
                            current_az = 0.0  # South Pole
                
                if is_array:
                    # Handle wraparound and crossing detection for arrays
                    # Use wraparound-aware checks to properly detect crossings
                    if direction == 1:  # Increasing
                        # Forward distance: how far forward from target to current (clockwise)
                        forward_dist = (current_az - target_azimuth) % 360
                        # Return True if we've crossed: forward distance is < 180° and > 0°, or exactly equal
                        crossing_mask = ((forward_dist < 180) & (forward_dist > 0)) | (current_az == target_azimuth)
                        return crossing_mask
                    else:  # direction == -1, Decreasing
                        # Backward distance: how far backward from target to current (counter-clockwise)
                        backward_dist = (target_azimuth - current_az) % 360
                        # Return True if we've crossed: backward distance is < 180° and > 0°, or exactly equal
                        crossing_mask = ((backward_dist < 180) & (backward_dist > 0)) | (current_az == target_azimuth)
                        return crossing_mask
                else:
                    # Scalar input - handle wraparound with wraparound-aware checks
                    if direction == 1:  # Increasing
                        # Forward distance: how far forward from target to current (clockwise)
                        forward_dist = (current_az - target_azimuth) % 360
                        # Return True if we've crossed: forward distance is < 180° and > 0°, or exactly equal
                        return ((forward_dist < 180 and forward_dist > 0) or (current_az == target_azimuth))
                    else:  # direction == -1, Decreasing
                        # Backward distance: how far backward from target to current (counter-clockwise)
                        backward_dist = (target_azimuth - current_az) % 360
                        # Return True if we've crossed: backward distance is < 180° and > 0°, or exactly equal
                        return ((backward_dist < 180 and backward_dist > 0) or (current_az == target_azimuth))
            
            # Set step_days attribute required by find_discrete
            if step_days is None:
                step_days = 0.01  # 0.01 days = ~14.4 minutes for fine-grained search
            azimuth_crosses_target.step_days = step_days
            
            # Use find_discrete to find when function changes state
            times, values = almanac.find_discrete(t_start, t_end, azimuth_crosses_target, epsilon=self._azimuth_tolerance_base)
            
            if len(times) > 0:
                # Get first crossing time
                crossing_time = times[0].utc_datetime()
                if not crossing_time.tzinfo:
                    crossing_time = crossing_time.replace(tzinfo=timezone.utc)
                return self._convert_to_local_time(crossing_time)
            
            return None
        except Exception as e:
            _LOGGER.error(f"Error finding time at azimuth {target_azimuth}: {e}", exc_info=True)
            return None

    def find_step_aligned_targets(
        self,
        quantity: Literal["azimuth", "elevation"],
        step_value: float,
        direction: int,
        search_start: Optional[datetime] = None,
        search_window_hours: float = 1.0,
        cap_time: Optional[datetime] = None,
        cap_value: Optional[float] = None,
        use_centre: bool = True,
        ephemeris_checkpoints: Optional[list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        """Find step-aligned targets using contiguous non-overlapping segments.

        Each segment spans ``search_window_hours`` (W). Segments advance forward in time
        until at least ``STEP_CANDIDATE_MINIMUM_COUNT`` crossings are collected, or a 24h
        hard limit from ``search_start`` is reached. If the first segment returns no
        crossings, the next segment is tried (no widening of the first window).

        Azimuth: optional reversal caps from ``ephemeris_checkpoints`` clip segments;
        when a segment ends at a reversal, that checkpoint is appended (``is_cap``)
        and search continues past it if the minimum count is not met.

        Elevation: optional ``cap_time`` / ``cap_value`` (next transit or antitransit)
        clip the segment; when hit, the cap row is appended and search may continue past it.

        For azimuth, ``ephemeris_checkpoints`` supplies cached ``azimuth_rate_deg_per_sec``
        for adaptive epsilon near transit/antitransit. When the cached rate meets
        ``AZIMUTH_TRANSIT_RATE_THRESHOLD_DEG_PER_SEC``, the interval around the next
        transit/antitransit (``AZIMUTH_TRANSIT_FOCUS_WINDOW_MINUTES`` each side, clipped
        to adjacent reversal checkpoints when present) is searched with that refined
        epsilon even if the segment end is not near the event; segments before and after
        use base epsilon.

        Singularities: when cached ``abs(azimuth_rate_deg_per_sec)`` at the nearby
        transit/antitransit meets ``AZIMUTH_SINGULARITY_RATE_THRESHOLD_DEG_PER_SEC``,
        scanning is skipped in a blackout window of
        ``AZIMUTH_SINGULARITY_GUARD_WINDOW_SECONDS`` on each side of the event
        (clipped to adjacent reversal checkpoints when present). No synthetic rows are
        added at blackout boundaries.
        """
        try:
            from .const import (
                STEP_CANDIDATE_MINIMUM_COUNT,
                STEP_CANDIDATE_MAX_SEARCH_SPAN_HOURS,
            )

            self._last_step_search_capped = False
            self._last_step_search_windows_used = 0
            self._last_step_search_result_count = 0
            if step_value <= 0:
                return []

            if search_start is None:
                search_start = self._search_start

            if search_start.tzinfo is None:
                search_start = search_start.replace(tzinfo=timezone.utc)

            if cap_time is not None and cap_time.tzinfo is None:
                cap_time = cap_time.replace(tzinfo=timezone.utc)

            direction_sign = 1 if direction >= 0 else -1

            def _sanitize_azimuth_value(az):
                """Replace NaN azimuth near poles with stable fallback values."""
                is_array = isinstance(az, np.ndarray) or hasattr(az, "__len__")
                if is_array:
                    nan_mask = np.isnan(az)
                    if np.any(nan_mask):
                        replacement = 180.0 if self._latitude > 0 else 0.0
                        az = np.where(nan_mask, replacement, az)
                    return az
                if np.isnan(az):
                    return 180.0 if self._latitude > 0 else 0.0
                return az

            def _step_bucket(t):
                """Return monotonic step bucket in selected direction."""
                if quantity == "azimuth":
                    _, raw_value, _ = self.position(t, apply_refraction=False)
                    raw_value = _sanitize_azimuth_value(raw_value)
                    directed_value = np.mod(raw_value, 360.0) if direction_sign == 1 else np.mod(-raw_value, 360.0)
                else:
                    raw_value, _, _ = self.position(t, apply_refraction=use_centre)
                    directed_value = raw_value if direction_sign == 1 else -raw_value

                return np.floor((directed_value + 1e-9) / step_value).astype(int)

            focus_delta = timedelta(minutes=self._azimuth_transit_focus_window_minutes)
            singularity_guard_delta = timedelta(seconds=self._azimuth_singularity_guard_window_seconds)

            def _epsilon_for_segment(
                segment_start: datetime,
                segment_end: datetime,
                window_was_capped: bool,
                near_event_time: Optional[datetime],
                near_event_rate_abs: Optional[float],
                in_transit_focus_window: bool = False,
            ) -> float:
                time_delta_days = max(
                    (segment_end - segment_start).total_seconds() / 86400.0,
                    1.0 / 86400.0,
                )
                _step_bucket.step_days = 0.4 * time_delta_days
                epsilon = self._elevation_tolerance
                if quantity != "azimuth":
                    return epsilon
                epsilon = self._azimuth_tolerance_base
                if (
                    near_event_time is not None
                    and near_event_rate_abs is not None
                    and near_event_rate_abs >= self._azimuth_transit_rate_threshold_deg_per_sec
                ):
                    if in_transit_focus_window:
                        ideal_epsilon = (0.005 / near_event_rate_abs) / 86400.0
                        epsilon = max(self._azimuth_tolerance_min, min(self._azimuth_tolerance_base, ideal_epsilon))
                    else:
                        window_is_capped_by_event = window_was_capped and segment_end == near_event_time
                        window_end_near_event = (
                            abs((segment_end - near_event_time).total_seconds()) <= focus_delta.total_seconds()
                        )
                        if window_is_capped_by_event or window_end_near_event:
                            ideal_epsilon = (0.005 / near_event_rate_abs) / 86400.0
                            epsilon = max(self._azimuth_tolerance_min, min(self._azimuth_tolerance_base, ideal_epsilon))
                return epsilon

            def _refinement_window_bounds(event_time: datetime) -> tuple[datetime, datetime]:
                """[T-W, T+W] clipped to adjacent reversal checkpoints when present."""
                rs = event_time - focus_delta
                re = event_time + focus_delta
                if not ephemeris_checkpoints:
                    return rs, re
                rev_times = sorted(
                    cp["time"]
                    for cp in ephemeris_checkpoints
                    if cp.get("event_type") == "reversal" and cp.get("time") is not None
                )
                prev_r = None
                next_r = None
                for rt in rev_times:
                    if rt < event_time:
                        prev_r = rt
                    elif rt > event_time:
                        next_r = rt
                        break
                if prev_r is not None:
                    rs = max(rs, prev_r)
                if next_r is not None:
                    re = min(re, next_r)
                return rs, re

            def _singularity_window_bounds(event_time: datetime) -> tuple[datetime, datetime]:
                """[T-G, T+G] clipped to adjacent reversal checkpoints when present."""
                rs = event_time - singularity_guard_delta
                re = event_time + singularity_guard_delta
                if not ephemeris_checkpoints:
                    return rs, re
                rev_times = sorted(
                    cp["time"]
                    for cp in ephemeris_checkpoints
                    if cp.get("event_type") == "reversal" and cp.get("time") is not None
                )
                prev_r = None
                next_r = None
                for rt in rev_times:
                    if rt < event_time:
                        prev_r = rt
                    elif rt > event_time:
                        next_r = rt
                        break
                if prev_r is not None:
                    rs = max(rs, prev_r)
                if next_r is not None:
                    re = min(re, next_r)
                return rs, re

            def _run_find_discrete_range(
                range_start: datetime,
                range_end: datetime,
                segment_window_was_capped: bool,
                near_event_time: Optional[datetime],
                near_event_rate_abs: Optional[float],
                in_transit_focus_window: bool,
                segment_next_rev_time: Optional[datetime],
            ) -> None:
                if range_end <= range_start:
                    return
                wcap_sub = (
                    segment_window_was_capped
                    and segment_next_rev_time is not None
                    and range_end == segment_next_rev_time
                )
                epsilon = _epsilon_for_segment(
                    range_start,
                    range_end,
                    wcap_sub,
                    near_event_time,
                    near_event_rate_abs,
                    in_transit_focus_window,
                )
                t_a = self._ts.from_datetime(range_start)
                t_b = self._ts.from_datetime(range_end)
                time_delta_days = max(
                    (range_end - range_start).total_seconds() / 86400.0,
                    1.0 / 86400.0,
                )
                _step_bucket.step_days = 0.4 * time_delta_days
                times, _ = almanac.find_discrete(t_a, t_b, _step_bucket, epsilon=epsilon)
                for t in times:
                    crossing_time = t.utc_datetime()
                    if not crossing_time.tzinfo:
                        crossing_time = crossing_time.replace(tzinfo=timezone.utc)
                    local_time = self._convert_to_local_time(crossing_time)
                    if quantity == "azimuth":
                        _, value, _ = self.position(local_time, apply_refraction=False)
                        value = _sanitize_azimuth_value(value)
                        value = float(np.mod(value, 360.0))
                    else:
                        value, _, _ = self.position(local_time, apply_refraction=use_centre)
                        value = float(value)
                    _append_unique({"time": local_time, "value": value, "is_cap": False})

            def _near_event_rate_for_segment(segment_start: datetime):
                near_event_time = None
                near_event_rate_abs = None
                rate_from_checkpoint = False
                if quantity != "azimuth":
                    return near_event_time, near_event_rate_abs, rate_from_checkpoint
                original_search_start = self._search_start
                original_search_end = self._search_end
                try:
                    bounded_end = segment_start + timedelta(hours=24)
                    self.set_search_window(search_start=segment_start, search_end=bounded_end)
                    next_transit = self.next_transit
                    next_antitransit = self.next_antitransit
                    self.set_search_window(search_start=original_search_start, search_end=original_search_end)
                    future_events = [
                        ev for ev in (next_transit, next_antitransit) if ev is not None and ev >= segment_start
                    ]
                    if future_events:
                        near_event_time = min(future_events)
                        if ephemeris_checkpoints:
                            best_delta_sec: Optional[float] = None
                            best_rate: Optional[float] = None
                            for cp in ephemeris_checkpoints:
                                if cp.get("event_type") not in ("transit", "antitransit"):
                                    continue
                                r = cp.get("azimuth_rate_deg_per_sec")
                                cp_time = cp.get("time")
                                if r is None or cp_time is None:
                                    continue
                                dt_sec = abs((cp_time - near_event_time).total_seconds())
                                if best_delta_sec is None or dt_sec < best_delta_sec:
                                    best_delta_sec = dt_sec
                                    best_rate = float(r)
                            if (
                                best_rate is not None
                                and best_delta_sec is not None
                                and best_delta_sec <= 120.0
                            ):
                                near_event_rate_abs = abs(best_rate)
                                rate_from_checkpoint = True
                        if near_event_rate_abs is None:
                            sample_seconds = 2.0
                            t_before = near_event_time - timedelta(seconds=sample_seconds)
                            t_after = near_event_time + timedelta(seconds=sample_seconds)
                            az_before = self.position(t_before, apply_refraction=False)[1] % 360.0
                            az_after = self.position(t_after, apply_refraction=False)[1] % 360.0
                            delta_az = ((az_after - az_before + 540.0) % 360.0) - 180.0
                            near_event_rate_abs = abs(delta_az / (2.0 * sample_seconds))
                except Exception:
                    try:
                        self.set_search_window(search_start=original_search_start, search_end=original_search_end)
                    except Exception:
                        pass
                    near_event_time = None
                    near_event_rate_abs = None
                    rate_from_checkpoint = False
                return near_event_time, near_event_rate_abs, rate_from_checkpoint

            reversal_queue: list[dict[str, Any]] = []
            if quantity == "azimuth" and ephemeris_checkpoints:
                for cp in ephemeris_checkpoints:
                    if cp.get("event_type") != "reversal":
                        continue
                    ct = cp.get("time")
                    if ct and ct > search_start:
                        reversal_queue.append(cp)
                reversal_queue.sort(key=lambda c: c["time"])

            elev_cap_time = cap_time
            elev_cap_value = cap_value
            cursor = search_start
            hard_end = search_start + timedelta(hours=STEP_CANDIDATE_MAX_SEARCH_SPAN_HOURS)
            all_results: list[dict[str, Any]] = []
            seen_times: set[datetime] = set()
            segment_windows_used = 0

            def _append_unique(item: dict[str, Any]) -> None:
                t = item["time"]
                if t in seen_times:
                    return
                seen_times.add(t)
                all_results.append(item)

            while cursor < hard_end and len(all_results) < STEP_CANDIDATE_MINIMUM_COUNT:
                next_rev = reversal_queue[0] if reversal_queue else None
                next_rev_time = next_rev["time"] if next_rev else None

                horizon_end = min(
                    cursor + timedelta(hours=search_window_hours),
                    hard_end,
                )
                seg_end = horizon_end
                window_was_capped = False
                if quantity == "azimuth" and next_rev_time is not None and next_rev_time > cursor:
                    if next_rev_time < horizon_end:
                        seg_end = next_rev_time
                        window_was_capped = True
                elif quantity == "elevation" and elev_cap_time is not None and elev_cap_time > cursor:
                    if elev_cap_time < horizon_end:
                        seg_end = elev_cap_time
                        window_was_capped = True

                if seg_end <= cursor:
                    break

                segment_windows_used += 1
                net, nrate, nrate_from_checkpoint = (
                    _near_event_rate_for_segment(cursor) if quantity == "azimuth" else (None, None, False)
                )
                if quantity == "azimuth":
                    wcap = window_was_capped and next_rev_time is not None and seg_end == next_rev_time
                else:
                    wcap = window_was_capped and elev_cap_time is not None and seg_end == elev_cap_time

                use_focus_split = False
                rs = None
                re = None
                if (
                    quantity == "azimuth"
                    and ephemeris_checkpoints
                    and net is not None
                    and nrate is not None
                    and nrate >= self._azimuth_transit_rate_threshold_deg_per_sec
                ):
                    rs, re = _refinement_window_bounds(net)
                    use_focus_split = rs < re

                use_singularity_blackout = False
                bs = None
                be = None
                if (
                    quantity == "azimuth"
                    and ephemeris_checkpoints
                    and net is not None
                    and nrate is not None
                    and nrate_from_checkpoint
                    and nrate >= self._azimuth_singularity_rate_threshold_deg_per_sec
                ):
                    bs, be = _singularity_window_bounds(net)
                    use_singularity_blackout = bs < be

                if (use_focus_split and rs is not None and re is not None) or (
                    use_singularity_blackout and bs is not None and be is not None
                ):
                    boundaries = {cursor, seg_end}
                    if use_focus_split and rs is not None and re is not None:
                        boundaries.add(max(cursor, min(seg_end, rs)))
                        boundaries.add(max(cursor, min(seg_end, re)))
                    if use_singularity_blackout and bs is not None and be is not None:
                        boundaries.add(max(cursor, min(seg_end, bs)))
                        boundaries.add(max(cursor, min(seg_end, be)))
                    points = sorted(boundaries)
                    for i in range(len(points) - 1):
                        sub_start = points[i]
                        sub_end = points[i + 1]
                        if sub_end <= sub_start:
                            continue
                        sub_mid = sub_start + (sub_end - sub_start) / 2
                        in_focus = (
                            use_focus_split
                            and rs is not None
                            and re is not None
                            and rs <= sub_mid <= re
                        )
                        in_blackout = (
                            use_singularity_blackout
                            and bs is not None
                            and be is not None
                            and bs <= sub_mid <= be
                        )
                        if in_blackout:
                            continue
                        _run_find_discrete_range(
                            sub_start,
                            sub_end,
                            window_was_capped,
                            net,
                            nrate,
                            in_focus,
                            next_rev_time,
                        )
                else:
                    epsilon = _epsilon_for_segment(cursor, seg_end, wcap, net, nrate)
                    t_start = self._ts.from_datetime(cursor)
                    t_end = self._ts.from_datetime(seg_end)
                    time_delta_days = max(
                        (seg_end - cursor).total_seconds() / 86400.0,
                        1.0 / 86400.0,
                    )
                    _step_bucket.step_days = 0.4 * time_delta_days
                    times, _ = almanac.find_discrete(t_start, t_end, _step_bucket, epsilon=epsilon)
                    for t in times:
                        crossing_time = t.utc_datetime()
                        if not crossing_time.tzinfo:
                            crossing_time = crossing_time.replace(tzinfo=timezone.utc)
                        local_time = self._convert_to_local_time(crossing_time)
                        if quantity == "azimuth":
                            _, value, _ = self.position(local_time, apply_refraction=False)
                            value = _sanitize_azimuth_value(value)
                            value = float(np.mod(value, 360.0))
                        else:
                            value, _, _ = self.position(local_time, apply_refraction=use_centre)
                            value = float(value)
                        _append_unique({"time": local_time, "value": value, "is_cap": False})

                hit_az_rev = (
                    quantity == "azimuth"
                    and next_rev_time is not None
                    and seg_end >= next_rev_time
                    and seg_end <= next_rev_time + timedelta(seconds=1)
                )
                hit_el_cap = (
                    quantity == "elevation"
                    and elev_cap_time is not None
                    and seg_end >= elev_cap_time
                    and seg_end <= elev_cap_time + timedelta(seconds=1)
                )

                if hit_az_rev and next_rev is not None:
                    self._last_step_search_capped = True
                    rt = next_rev["time"]
                    if rt not in seen_times:
                        rv = float(next_rev.get("azimuth", 0.0))
                        _append_unique({"time": rt, "value": rv, "is_cap": True})
                    reversal_queue.pop(0)
                    if len(all_results) >= STEP_CANDIDATE_MINIMUM_COUNT:
                        break
                    cursor = rt + timedelta(seconds=1)
                    continue

                if hit_el_cap and elev_cap_time is not None:
                    self._last_step_search_capped = True
                    et = elev_cap_time
                    if et not in seen_times:
                        ev = elev_cap_value
                        if ev is None:
                            ev = float(self.position(et, apply_refraction=use_centre)[0])
                        _append_unique({"time": et, "value": float(ev), "is_cap": True})
                    elev_cap_time = None
                    elev_cap_value = None
                    if len(all_results) >= STEP_CANDIDATE_MINIMUM_COUNT:
                        break
                    cursor = et + timedelta(seconds=1)
                    continue

                cursor = seg_end + timedelta(microseconds=1)
                if len(all_results) >= STEP_CANDIDATE_MINIMUM_COUNT:
                    break

            self._last_step_search_windows_used = segment_windows_used
            self._last_step_search_result_count = len(all_results)
            return all_results
        except Exception as e:
            _LOGGER.error(
                "Error finding step-aligned %s targets: %s",
                quantity,
                e,
                exc_info=True,
            )
            return []
    
    def _elevation_crosses_target(
        self,
        t,
        target_elevation: float,
        direction: int,
        use_centre: bool = True
    ):
        """Check if elevation crosses target value.
        
        Args:
            t: Skyfield Time object (scalar or array)
            target_elevation: Target elevation in degrees
            direction: Direction of movement (1 for increasing, -1 for decreasing)
            use_centre: If True, use center of body. If False, use leading edge (Sun/Moon only)
            
        Returns:
            Boolean or numpy array of booleans indicating if elevation has crossed target
        """
        
        # Adjust target for leading edge if use_centre is False
        adjusted_target = target_elevation
        if not use_centre:
            # Only use leading edge for Sun and Moon
            sun = self._eph['sun']
            moon = self._eph['moon']
            # Check if body is Sun or Moon (handle Moon represented as sum of vectors)
            body_str = str(self._body)
            is_sun = self._body is sun
            is_moon = (self._body is moon) or ('MOON' in body_str.upper() or '301' in body_str)
            if is_sun or is_moon:
                # Get angular radius - use first element if array, otherwise use t directly
                # Check if it's an array by trying to access shape attribute or index it
                try:
                    # Try to access shape - arrays have this, single Time objects don't
                    shape = t.shape
                    # It's an array - use first time for angular radius calculation
                    try:
                        time_for_radius = t[0] if len(t) > 0 else self._ts.from_datetime(self._search_start)
                    except TypeError:
                        # Single Time object (len() raises TypeError)
                        time_for_radius = t
                except AttributeError:
                    # Single Time object (no shape attribute)
                    time_for_radius = t
                
                angular_radius = self._get_angular_radius(time_for_radius)
                if angular_radius is not None:
                    if direction == 1:  # Rising - leading edge is below center
                        adjusted_target = target_elevation - angular_radius
                    else:  # direction == -1, Setting - leading edge is above center
                        adjusted_target = target_elevation + angular_radius
        
        # Use position() to get elevation (handles arrays automatically)
        # Pass Time object directly to preserve array handling
        current_elevation, _, _ = self.position(t)
        
        # Handle both scalar and array inputs
        # Check if it's an array by checking if it's a numpy array or has shape attribute
        try:
            is_array = isinstance(current_elevation, np.ndarray) or hasattr(current_elevation, 'shape')
        except (AttributeError, TypeError):
            is_array = False
        
        if is_array:
            # Array input - use numpy operations
            if direction == 1:  # Increasing
                return current_elevation >= adjusted_target
            else:  # direction == -1, Decreasing
                return current_elevation <= adjusted_target
        else:
            # Scalar input
            if direction == 1:  # Increasing
                return current_elevation >= adjusted_target
            else:  # direction == -1, Decreasing
                return current_elevation <= adjusted_target
    
    def get_time_at_elevation(
        self,
        target_elevation: float,
        direction: int,
        search_end: Optional[datetime] = None,
        use_centre: bool = True,
        search_start: Optional[datetime] = None,
        step_days: Optional[float] = None,
        take_last_match: bool = False,
    ) -> Optional[datetime] | str:
        """Get time when elevation reaches target value.
        
        Args:
            target_elevation: Target elevation in degrees
            direction: Direction of movement (1 for increasing, -1 for decreasing)
            search_end: End time for search (defaults to 24 hours from search_start if None)
            use_centre: If True, use center of body. If False, use leading edge (Sun/Moon only, defaults to True)
            search_start: Start time for search (defaults to self._search_start)
            step_days: Step size in days for find_discrete (defaults to 0.01, or 40% of time window if both search_start and search_end provided)
            take_last_match: If True, return the last matching crossing in the window (chronologically); otherwise the first.
            
        Returns:
            Datetime when elevation reaches target, "Out of Range" if not found, or None on error
        """
        try:
            
            # Validate use_centre parameter
            if not use_centre:
                sun = self._eph['sun']
                moon = self._eph['moon']
                # Check if body is Sun or Moon (handle Moon represented as sum of vectors)
                body_str = str(self._body)
                is_sun = self._body is sun
                is_moon = (self._body is moon) or ('MOON' in body_str.upper() or '301' in body_str)
                if not is_sun and not is_moon:
                    _LOGGER.warning(f"use_centre=False only supported for Sun and Moon, using center for {self._body}")
                    use_centre = True
            
            # Set search start time
            if search_start is None:
                search_start = self._search_start
            
            # Set search end time
            if search_end is None:
                search_end = search_start + timedelta(hours=24)
            
            # Convert to Skyfield Time objects
            t_start = self._ts.from_datetime(search_start)
            t_end = self._ts.from_datetime(search_end)
            
            # Create wrapper function that calls the helper
            def elevation_crosses_target(t):
                return self._elevation_crosses_target(t, target_elevation, direction, use_centre)
            
            # Set step_days attribute required by find_discrete
            if step_days is None:
                step_days = 600 / 86400.0  # 10 minutes in days
            elevation_crosses_target.step_days = step_days
            
            # Use find_discrete to find when function changes state
            times, values = almanac.find_discrete(t_start, t_end, elevation_crosses_target, epsilon=self._elevation_tolerance)
            
            if len(times) > 0:
                # find_discrete returns (times, values) where values[i] is the NEW state after transition at times[i]
                # For direction == 1 (rising): elevation_crosses_target returns True when elevation >= target
                #   We want False->True transitions (values[i] == True) = crossing from below to at/above target
                # For direction == -1 (setting): elevation_crosses_target returns True when elevation <= target
                #   We want False->True transitions (values[i] == True) = crossing from above to at/below target
                # In both cases, we want transitions where values[i] == True
                
                # Collect transitions that match our desired direction (False->True = crossing in desired direction)
                local_candidates: list[datetime] = []
                for i, new_state in enumerate(values):
                    if new_state:
                        crossing_time = times[i].utc_datetime()
                        if not crossing_time.tzinfo:
                            crossing_time = crossing_time.replace(tzinfo=timezone.utc)
                        local_candidates.append(self._convert_to_local_time(crossing_time))

                if not local_candidates:
                    return "Out of Range"

                picked = local_candidates[-1] if take_last_match else local_candidates[0]
                return picked
            
            # No crossing found within search range
            return "Out of Range"
            
        except Exception as e:
            _LOGGER.error(f"Error finding time at elevation {target_elevation}: {e}", exc_info=True)
            return None

