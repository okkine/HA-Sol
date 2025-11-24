"""Module for storing configuration data by entry ID."""

import logging

_LOGGER = logging.getLogger(__name__)

_config_entry_data = {}

def store_config_entry_data(entry_id: str, data: dict):
    """Store config data for a specific entry."""
    global _config_entry_data
    
    _config_entry_data[entry_id] = data

def get_config_entry_data(entry_id: str) -> dict:
    """Get config data for a specific entry."""
    data = _config_entry_data.get(entry_id, {})
    
    return data

def remove_config_entry_data(entry_id: str):
    """Remove config data for a specific entry."""
    global _config_entry_data
    
    _config_entry_data.pop(entry_id, None) 