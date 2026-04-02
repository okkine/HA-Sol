"""The Sol integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, NAME
from .config_store import store_config_entry_data, remove_config_entry_data
from .legacy_storage_cleanup import async_cleanup_legacy_notice_storage
from .ephemeris_cache import EphemerisCacheManager
from .declination_cache import get_declination_cache_instance
from ambiance import Atmosphere

_LOGGER = logging.getLogger(__name__)

# List of platforms to support
PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Sol integration."""
    hass.data.setdefault(DOMAIN, {})

    await async_cleanup_legacy_notice_storage(hass)

    # Initialize ephemeris cache manager (no body needed in __init__)
    cache_manager = EphemerisCacheManager(hass, DOMAIN)
    hass.data[DOMAIN]['cache_manager'] = cache_manager
    
    # Initialize declination cache manager
    declination_cache = get_declination_cache_instance(hass)
    await declination_cache.initialize()
    hass.data[DOMAIN]['declination_cache'] = declination_cache
    
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sol from a config entry."""
    # Backwards compatibility: Calculate and store pressure if pressure_mode is auto but pressure_mbar is missing
    entry_data = dict(entry.data)
    pressure_mode = entry_data.get("pressure_mode")
    if pressure_mode is None:
        # Backward compatibility: check old temperature_mode
        temperature_mode = entry_data.get("temperature_mode", "estimator")
        if temperature_mode == "manual":
            pressure_mode = "manual"
        else:
            pressure_mode = "auto"
    
    if pressure_mode == "auto" and "pressure_mbar" not in entry_data:
        try:
            elevation = entry_data.get("elevation", hass.config.elevation)
            pressure_pa = Atmosphere(elevation).pressure[0]
            pressure_mbar = pressure_pa / 100.0
            entry_data["pressure_mbar"] = pressure_mbar
            
            # Update config entry
            hass.config_entries.async_update_entry(entry, data=entry_data)
            
            _LOGGER.info(
                f"Calculated pressure for entry {entry.entry_id}: {pressure_mbar:.2f} mbar "
                f"(from elevation {elevation}m, pressure_mode=auto)"
            )
        except Exception as e:
            _LOGGER.error(f"Error calculating pressure for entry {entry.entry_id}: {e}", exc_info=True)
    
    # Store config entry data
    store_config_entry_data(entry.entry_id, entry.data)
    
    # Store entry data in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data
    
    # Initialize ephemeris cache for each body in this entry
    bodies = entry.data.get("bodies", ["sun"])  # Default to sun for backward compatibility
    cache_manager = hass.data[DOMAIN].get('cache_manager')
    if cache_manager:
        for body_key in bodies:
            await cache_manager.initialize_entry(entry.entry_id, body_key)
    
    # Update declination cache for this entry (if sensor is enabled)
    declination_cache = hass.data[DOMAIN].get('declination_cache')
    if declination_cache and entry.data.get("enable_declination_normalized", False):
        await declination_cache.update_entry(entry.entry_id)
    
    # Forward entry setup to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)  # Re-enabled, only elevation sensor active
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Remove ephemeris cache for all bodies in this entry
        cache_manager = hass.data[DOMAIN].get('cache_manager')
        if cache_manager:
            bodies = entry.data.get("bodies", ["sun"])  # Default to sun for backward compatibility
            for body_key in bodies:
                await cache_manager.remove_entry(entry.entry_id, body_key)
        
        # Remove from hass.data
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Clean up config store data
        remove_config_entry_data(entry.entry_id)
        
        # Remove declination cache observer for this entry
        declination_cache = hass.data[DOMAIN].get('declination_cache')
        if declination_cache:
            declination_cache.remove_entry(entry.entry_id)
        
        # Clean up declination cache if no more entries
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries or len(entries) == 0:
            if declination_cache:
                await declination_cache.cleanup()
                hass.data[DOMAIN].pop('declination_cache', None)
    
    return unload_ok

