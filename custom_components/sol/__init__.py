"""The Sol integration."""

from __future__ import annotations

import logging

from homeassistant.components import persistent_notification
from homeassistant.components.persistent_notification import (
    UpdateType,
    async_register_callback,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers.storage import Store

from .const import DOMAIN

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
from .config_store import store_config_entry_data, remove_config_entry_data
from .cache import get_cache_instance, get_solar_events_cache_instance
from .reversal_cache import get_reversal_cache_manager

_LOGGER = logging.getLogger(__name__)

LEGACY_NOTICE_NOTIFICATION_ID = "sol_legacy_notice_2026_05_01"
LEGACY_NOTICE_STORAGE_KEY = "sol_legacy_notice"
LEGACY_NOTICE_STORAGE_VERSION = 1
LEGACY_NOTICE_LISTENER_KEY = "_legacy_notice_listener"

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

    # One-time legacy notice: show once, never re-show after user dismisses it.
    store = Store(hass, LEGACY_NOTICE_STORAGE_VERSION, LEGACY_NOTICE_STORAGE_KEY)
    notice_data = await store.async_load() or {}

    if not notice_data.get("dismissed", False):
        persistent_notification.create(
            hass,
            title="Sol: Breaking Update Arrives May 1, 2026",
            message=(
                "This is the final maintenance release of the current PyEphem-based Sol integration.\n\n"
                "A fully rewritten version of Sol is scheduled for release on May 1, 2026.\n\n"
                "What is changing:\n"
                "- The new version replaces PyEphem with a new calculation engine to resolve "
                "edge-case instability at certain latitudes.\n"
                "- The update is a breaking change — review automations and entity references.\n"
                "- Sun tracking remains central, with expanded support for Moon and planetary tracking.\n\n"
                "Entity naming (examples):\n"
                "- sensor.sol_solar_elevation -> sensor.sol_sun_elevation\n"
                "- sensor.sol_solar_azimuth -> sensor.sol_sun_azimuth\n\n"
                "Pre-release:\n"
                "A pre-release is available for testing before the May 1 release:\n"
                "https://github.com/okkine/HA-Sol/releases\n\n"
                "If you rely on Sol in production, please test the pre-release and update automations "
                "that reference old entity IDs.\n\n"
                "If you dismiss this message, it will not be shown again."
            ),
            notification_id=LEGACY_NOTICE_NOTIFICATION_ID,
        )
        notice_data["shown"] = True
        await store.async_save(notice_data)

    if LEGACY_NOTICE_LISTENER_KEY not in hass.data[DOMAIN]:
        @callback
        def _handle_persistent_notification_update(
            update_type: UpdateType,
            notifications: dict,
        ) -> None:
            if update_type != UpdateType.REMOVED:
                return
            if LEGACY_NOTICE_NOTIFICATION_ID not in notifications:
                return

            async def _persist_dismissed() -> None:
                state_store = Store(
                    hass, LEGACY_NOTICE_STORAGE_VERSION, LEGACY_NOTICE_STORAGE_KEY
                )
                state_data = await state_store.async_load() or {}
                if state_data.get("dismissed"):
                    return
                state_data["dismissed"] = True
                await state_store.async_save(state_data)

            hass.async_create_task(_persist_dismissed())

        hass.data[DOMAIN][LEGACY_NOTICE_LISTENER_KEY] = async_register_callback(
            hass,
            _handle_persistent_notification_update,
        )
    
    # Register a device for this integration
    device_registry = async_get_device_registry(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Sol",
        manufacturer="Okkine",
        model="Solar Position Tracker",
        sw_version="2026.04.01",  # Match your integration version
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

    remaining_entries = [key for key in hass.data[DOMAIN] if key != LEGACY_NOTICE_LISTENER_KEY]

    # Clean up cache if no more entries
    if not remaining_entries:
        legacy_listener = hass.data[DOMAIN].pop(LEGACY_NOTICE_LISTENER_KEY, None)
        if legacy_listener:
            legacy_listener()
        cache = get_cache_instance(hass)
        await cache.cleanup()

    return unload_ok



