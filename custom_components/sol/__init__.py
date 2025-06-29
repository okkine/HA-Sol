# __init__.py
"""Sol integration for Home Assistant."""
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import discovery
from .helper import SOLSTICE_CURVE_STORE, SolCalculateSolsticeCurve, SunHelper
from .const import CONF_PRESSURE, CONF_TEMPERATURE, DEFAULT_PRESSURE, DEFAULT_TEMPERATURE, DOMAIN

import homeassistant.util.dt as dt_util
from datetime import datetime, timedelta, time
import logging

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Sol component."""
    if DOMAIN not in config:
        return True
        
    domain_config = config[DOMAIN]
    
    # Always calculate initial solstice curve value
    try:
        _LOGGER.info("Initializing solstice curve value")
        
        # Create helpers
        sun_helper = SunHelper(
            hass.config.latitude,
            hass.config.longitude,
            hass.config.elevation,
            domain_config.get(CONF_PRESSURE, DEFAULT_PRESSURE),
            domain_config.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)
        )
        
        calculator = SolCalculateSolsticeCurve(
            hass.config.latitude,
            hass.config.longitude,
            hass.config.elevation,
            domain_config.get(CONF_PRESSURE, DEFAULT_PRESSURE),
            domain_config.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)
        )
        
        # Get current time and ensure timezone awareness
        now = dt_util.utcnow()  # Always returns timezone-aware UTC datetime
        now_local = dt_util.as_local(now)  # Convert to local timezone
        
        # Start from beginning of today in local time
        start_of_today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_today_utc = start_of_today_local.astimezone(dt_util.UTC)
        
        # Get today's sunrise and sunset times
        todays_sunrise = sun_helper.get_time_at_elevation(
            start_dt=start_of_today_utc,  # Already timezone-aware UTC
            target_elev=0,
            direction='rising',
            max_days=0,
            caller="init"
        )
        todays_sunset = sun_helper.get_time_at_elevation(
            start_dt=start_of_today_utc,  # Already timezone-aware UTC
            target_elev=0,
            direction='setting',
            max_days=0,
            caller="init"
        )
        
        # Determine calculation time based on whether it's before or after noon
        if now_local.hour < 12:
            # Before noon: use today's sunrise
            calculation_time = todays_sunrise
            event_type_used = "today's sunrise"
        else:
            # After noon: use today's sunset
            calculation_time = todays_sunset
            event_type_used = "today's sunset"
        
        # Fallback to current time only if we couldn't get either time
        if not calculation_time:
            _LOGGER.warning("Could not determine today's %s time, using current time", 
                          "sunrise" if now_local.hour < 12 else "sunset")
            calculation_time = now  # Already timezone-aware UTC
            event_type_used = "current_time (fallback)"
        
        # Calculate solstice curve at the determined calculation time
        normalized, prev_solstice, next_solstice = calculator.get_normalized_curve(date_time=calculation_time)
        
        # Update global storage
        SOLSTICE_CURVE_STORE['value'] = normalized
        SOLSTICE_CURVE_STORE['prev_solstice'] = prev_solstice
        SOLSTICE_CURVE_STORE['next_solstice'] = next_solstice
        SOLSTICE_CURVE_STORE['calculation_time'] = calculation_time
        
        _LOGGER.debug(
            "Initialized solstice curve value using %s at %s: %.4f (local time: %s, sunrise: %s, sunset: %s)",
            event_type_used, calculation_time, normalized, now_local,
            todays_sunrise.isoformat() if todays_sunrise else "unknown",
            todays_sunset.isoformat() if todays_sunset else "unknown"
        )
    except Exception as e:
        _LOGGER.error("Error initializing solstice curve: %s", e)
    
    # Forward to sensor platform
    hass.async_create_task(
        discovery.async_load_platform(
            hass, 'sensor', DOMAIN, domain_config, config
        )
    )
    
    # Forward to binary sensor platform
    hass.async_create_task(
        discovery.async_load_platform(
            hass, 'binary_sensor', DOMAIN, domain_config, config
        )
    )
    
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigType) -> bool:
    """Set up Sol from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    # Forward the setup to the sensor and binary_sensor platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigType) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    
    return unload_ok