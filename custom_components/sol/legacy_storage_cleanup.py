"""One-time cleanup of legacy .storage keys from pre-rewrite integrations.

Old PyEphem Sol:
- Store key "sol_legacy_notice" (breaking-change dismissal).
- Per-entry azimuth reversal files: "sol_azimuth_reversals_<entry_id>" (reversal_store.py in
  HA-Sol_Final_Release_Before_Rewrite).

Luna (HA-Luna_Final_Release_Before_Rewrite):
- "luna_legacy_notice" (breaking-change dismissal).
- Per-entry azimuth reversal files: "luna_azimuth_reversals_<entry_id>" (reversal_store.py).

The rewrite uses unified ephemeris caches only ("sol_ephemeris_cache_..."); it does not write
the old reversal or legacy-notice keys above.

Cleanup policy:
- sol_legacy_notice, sol_azimuth_reversals_*: remove when this Sol loads (upgrade path).
- luna_legacy_notice, luna_azimuth_reversals_*: remove only when Luna has no config entries.

TODO: Delete this file and remove the call from __init__.async_setup once most users have
upgraded (e.g. after several Sol releases post-rewrite).
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

# Must match LEGACY_NOTICE_STORAGE_VERSION / reversal STORAGE_VERSION in pre-rewrite sol/luna.
_LEGACY_STORAGE_VERSION = 1

_SOL_LEGACY_NOTICE_KEY = "sol_legacy_notice"
_LUNA_LEGACY_NOTICE_KEY = "luna_legacy_notice"
_LUNA_DOMAIN = "luna"

# Prefixes from reversal_store.py in old sol and luna.
_SOL_AZIMUTH_REVERSALS_PREFIX = "sol_azimuth_reversals_"
_LUNA_AZIMUTH_REVERSALS_PREFIX = "luna_azimuth_reversals_"


async def async_cleanup_legacy_notice_storage(hass: HomeAssistant) -> None:
    """Remove legacy notice and old per-entry azimuth reversal storage when safe."""
    await _async_remove_store_if_exists(hass, _SOL_LEGACY_NOTICE_KEY)
    await _async_remove_stores_with_prefix(hass, _SOL_AZIMUTH_REVERSALS_PREFIX)

    if hass.config_entries.async_entries(_LUNA_DOMAIN):
        _LOGGER.debug(
            "Skipping Luna legacy storage cleanup: Luna config entries still present",
        )
        return

    await _async_remove_store_if_exists(hass, _LUNA_LEGACY_NOTICE_KEY)
    await _async_remove_stores_with_prefix(hass, _LUNA_AZIMUTH_REVERSALS_PREFIX)


async def _async_remove_stores_with_prefix(hass: HomeAssistant, prefix: str) -> None:
    """Remove every Store in config/.storage whose key starts with ``prefix``."""
    try:
        storage_dir = Path(hass.config.path(".storage"))
        if not storage_dir.is_dir():
            return
        for path in sorted(storage_dir.glob(f"{prefix}*")):
            if not path.is_file():
                continue
            key = path.name
            if not key.startswith(prefix):
                continue
            await _async_remove_store_if_exists(hass, key)
    except Exception as err:
        _LOGGER.debug("Legacy prefix cleanup for %s*: %s", prefix, err)


async def _async_remove_store_if_exists(hass: HomeAssistant, key: str) -> None:
    try:
        store = Store(hass, _LEGACY_STORAGE_VERSION, key)
        await store.async_remove()
        _LOGGER.debug("Removed legacy storage key %s", key)
    except Exception as err:
        _LOGGER.debug("Legacy storage cleanup for %s: %s", key, err)
