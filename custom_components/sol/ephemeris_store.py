"""Persistent storage for ephemeris cache."""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional
from datetime import timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "sol_ephemeris_cache"


class EphemerisStore:
    """Manages persistent storage for ephemeris cache."""
    
    def __init__(self, hass: HomeAssistant, entry_id: str, body_key: str):
        """Initialize the ephemeris store.
        
        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID
            body_key: Body identifier (e.g., "sun", "moon", "jupiter")
        """
        self.hass = hass
        self.entry_id = entry_id
        self.body_key = body_key

        # Prefer human-readable entry title when available (for easier debugging).
        # If we can't resolve a title yet, fall back to the legacy key.
        entry = hass.config_entries.async_get_entry(entry_id)
        entry_title = getattr(entry, "title", None) if entry is not None else None
        if entry_title:
            raw = str(entry_title)
            safe = "".join((c if (c.isalnum() or c in ("-", "_")) else "_") for c in raw.strip())
            safe = safe.strip("_")
            new_key = f"{STORAGE_KEY_PREFIX}_{safe}_{entry_id}_{body_key}"
        else:
            # Should be rare; if title isn't available yet, degrade to entry_id-only.
            new_key = f"{STORAGE_KEY_PREFIX}_{entry_id}_{body_key}"

        self._store = Store(hass, STORAGE_VERSION, new_key)
    
    async def async_load(self) -> Optional[Dict[str, Any]]:
        """
        Load ephemeris cache from persistent storage.
        
        Returns:
            Dictionary with cache data or None if not found/invalid
        """
        try:
            data = await self._store.async_load()
            
            if data is None:
                _LOGGER.debug(f"No cached ephemeris data found for entry {self.entry_id}, body {self.body_key}")
                return None
            
            # Deserialize datetime strings
            if 'last_known_state' in data:
                time_str = data['last_known_state'].get('time')
                if time_str:
                    data['last_known_state']['time'] = datetime.datetime.fromisoformat(time_str)
            
            # Handle checkpoints
            if 'checkpoints' in data:
                for checkpoint in data['checkpoints']:
                    if 'time' in checkpoint and isinstance(checkpoint['time'], str):
                        checkpoint['time'] = datetime.datetime.fromisoformat(checkpoint['time'])
                    # Handle legacy is_reversal field
                    if 'is_reversal' in checkpoint and 'event_type' not in checkpoint:
                        checkpoint['event_type'] = 'reversal' if checkpoint['is_reversal'] else 'transit'
                        del checkpoint['is_reversal']
            
            checkpoint_count = len(data.get('checkpoints', []))
            _LOGGER.debug(
                f"Loaded cache for entry {self.entry_id}, body {self.body_key}: "
                f"{checkpoint_count} checkpoints"
            )
            
            return data
            
        except Exception as e:
            _LOGGER.error(f"Error loading ephemeris cache for entry {self.entry_id}, body {self.body_key}: {e}")
            return None
    
    def _serialize_datetime(self, obj: Any) -> Any:
        """Recursively serialize datetime objects to ISO format strings."""
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {key: self._serialize_datetime(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._serialize_datetime(item) for item in obj]
        else:
            return obj
    
    async def async_save(self, data: Dict[str, Any]) -> None:
        """
        Save ephemeris cache to persistent storage.
        
        Args:
            data: Dictionary with cache data to save
        """
        try:
            # Serialize datetime objects to ISO format strings, keep everything else as-is
            save_data = self._serialize_datetime(data)
            
            _LOGGER.debug(f"Saving cache for {self.entry_id}/{self.body_key} with keys: {save_data.keys()}")
            await self._store.async_save(save_data)
            _LOGGER.debug(f"Cache saved successfully for {self.entry_id}/{self.body_key}")
            
            checkpoint_count = len(save_data.get('checkpoints', [])) if isinstance(save_data.get('checkpoints'), list) else 0
            _LOGGER.debug(
                f"Saved cache for entry {self.entry_id}, body {self.body_key}: "
                f"{checkpoint_count} checkpoints"
            )
            
        except Exception as e:
            _LOGGER.error(f"Error saving ephemeris cache for entry {self.entry_id}, body {self.body_key}: {e}")
    
    async def async_remove(self) -> None:
        """Remove the cached data from storage."""
        try:
            await self._store.async_remove()
            _LOGGER.debug(f"Removed ephemeris cache for entry {self.entry_id}, body {self.body_key}")
        except Exception as e:
            _LOGGER.error(f"Error removing ephemeris cache for entry {self.entry_id}, body {self.body_key}: {e}")


async def load_ephemeris_cache(hass: HomeAssistant, entry_id: str, body_key: str) -> Optional[Dict[str, Any]]:
    """
    Convenience function to load ephemeris cache.
    
    Args:
        hass: Home Assistant instance
        entry_id: Config entry ID
        body_key: Body identifier (e.g., "sun", "moon", "jupiter")
        
    Returns:
        Cache data or None
    """
    store = EphemerisStore(hass, entry_id, body_key)
    return await store.async_load()


async def save_ephemeris_cache(hass: HomeAssistant, entry_id: str, body_key: str, data: Dict[str, Any]) -> None:
    """
    Convenience function to save ephemeris cache.
    
    Args:
        hass: Home Assistant instance
        entry_id: Config entry ID
        body_key: Body identifier (e.g., "sun", "moon", "jupiter")
        data: Cache data to save
    """
    try:
        store = EphemerisStore(hass, entry_id, body_key)
        await store.async_save(data)
    except Exception as e:
        _LOGGER.error(f"Error saving ephemeris cache for {entry_id}/{body_key}: {e}", exc_info=True)


async def remove_ephemeris_cache(hass: HomeAssistant, entry_id: str, body_key: str) -> None:
    """
    Convenience function to remove ephemeris cache.
    
    Args:
        hass: Home Assistant instance
        entry_id: Config entry ID
        body_key: Body identifier (e.g., "sun", "moon", "jupiter")
    """
    store = EphemerisStore(hass, entry_id, body_key)
    await store.async_remove()

