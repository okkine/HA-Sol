# __init__.py
"""Sol integration for Home Assistant."""
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import discovery
from .helper import SOLSTICE_CURVE_STORE, SolCalculateSolsticeCurve
from .const import CONF_PRESSURE, CONF_TEMPERATURE, DEFAULT_PRESSURE, DEFAULT_TEMPERATURE, DOMAIN

import homeassistant.util.dt as dt_util
import logging

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Sol component."""
    if DOMAIN not in config:
        return True
        
    domain_config = config[DOMAIN]
    
    # Initialize solstice curve value
    if SOLSTICE_CURVE_STORE.get('value') is None:
        try:
            _LOGGER.info("Initializing solstice curve value")
            
            calculator = SolCalculateSolsticeCurve(
                hass.config.latitude,
                hass.config.longitude,
                hass.config.elevation,
                domain_config.get(CONF_PRESSURE, DEFAULT_PRESSURE),
                domain_config.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)
            )
            now = dt_util.utcnow()
            normalized, prev_solstice, next_solstice = calculator.get_normalized_curve(now)
            
            SOLSTICE_CURVE_STORE['value'] = normalized
            SOLSTICE_CURVE_STORE['prev_solstice'] = prev_solstice
            SOLSTICE_CURVE_STORE['next_solstice'] = next_solstice
            SOLSTICE_CURVE_STORE['calculation_time'] = now
            
            _LOGGER.debug("Initialized solstice curve value: %.4f", normalized)
        except Exception as e:
            _LOGGER.error("Error initializing solstice curve: %s", e)
    
    # Forward to sensor platform
    hass.async_create_task(
        discovery.async_load_platform(
            hass, 'sensor', DOMAIN, domain_config, config
        )
    )
    
    # Forward to binary_sensor platform
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
