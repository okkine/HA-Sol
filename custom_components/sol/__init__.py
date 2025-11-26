"""The Sol integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.device_registry import async_get as async_get_device_registry

from .const import DOMAIN

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
from .config_store import store_config_entry_data, remove_config_entry_data
from .cache import get_cache_instance, get_solar_events_cache_instance
from .reversal_cache import get_reversal_cache_manager

_LOGGER = logging.getLogger(__name__)

# List of platforms to support. There should be a matching .py file for each,
# eg. <platform>.py
PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Sol integration."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sol from a config entry."""
    
    
    
    store_config_entry_data(entry.entry_id, entry.data)
    # Store an instance of the "connecting" class that does the work of speaking
    # with your actual devices.
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data
    
    # Register a device for this integration
    device_registry = async_get_device_registry(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Sol",
        manufacturer="Okkine",
        model="Solar Position Tracker",
        sw_version="2025.11.26",  # Match your integration version
        entry_type=DeviceEntryType.SERVICE,  # Indicates this is a service, not hardware
    )
    

    # Initialize both caches
    cache = get_cache_instance(hass)
    await cache.initialize()
    
    # Initialize solar events cache
    solar_events_cache = get_solar_events_cache_instance(hass)
    await solar_events_cache.initialize()
    
    # Initialize reversal cache manager
    reversal_cache_manager = get_reversal_cache_manager(hass)
    await reversal_cache_manager.initialize_entry(entry.entry_id)
    

    # This creates each HA object for each platform your device requires.
    # It's done by calling the `async_setup_entry` function in each platform module.
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    
    entry.async_on_unload(entry.add_update_listener(config_entry_update_listener))
    
    
    
    return True


async def config_entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener, called when the config entry options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # This is called when an entry/configured device is to be removed. The class
    # needs to unload itself, and remove callbacks. See the classes for further
    # details
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        # Clean up config store data
        remove_config_entry_data(entry.entry_id)
        
        # Clean up reversal cache for this entry
        reversal_cache_manager = get_reversal_cache_manager(hass)
        await reversal_cache_manager.remove_entry(entry.entry_id)

    # Clean up cache if no more entries
    if not hass.data[DOMAIN]:
        cache = get_cache_instance(hass)
        await cache.cleanup()

    return unload_ok



