#!/usr/bin/env python3
"""
Debug script for Sol binary sensor to identify issues with state updates and next_change attributes.
"""

import sys
import os
from datetime import datetime, timedelta, timezone
import math
import ephem

# Add the current directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helper import SunHelper, SolCalculateSolsticeCurve, SOLSTICE_CURVE_STORE

def test_binary_sensor_logic(latitude, longitude, elevation, rising_elev, setting_elev, test_time=None):
    """Test binary sensor logic at the given time."""
    
    if test_time is None:
        test_time = datetime.now(timezone.utc)
    
    print(f"Testing binary sensor logic at {test_time}")
    print(f"Location: {latitude}°N, {longitude}°E")
    print(f"Elevations: rising={rising_elev}°, setting={setting_elev}°")
    print("-" * 60)
    
    # Create sun helper
    sun_helper = SunHelper(latitude, longitude, elevation)
    
    # Get current position and direction
    current_elev, current_azimuth = sun_helper.calculate_position(test_time)
    print(f"Current elevation: {current_elev:.2f}°")
    print(f"Current azimuth: {current_azimuth:.2f}°")
    
    # Get sun direction
    try:
        sun_direction = sun_helper.sun_direction(test_time)
        print(f"Sun direction: {sun_direction}")
    except Exception as e:
        print(f"Error getting sun direction: {e}")
        sun_direction = "unknown"
    
    # Determine state
    if sun_direction == "rising":
        new_state = current_elev >= rising_elev
        print(f"State determination: rising direction -> ON if elev >= {rising_elev}°")
    else:
        new_state = current_elev >= setting_elev
        print(f"State determination: setting direction -> ON if elev >= {setting_elev}°")
    
    print(f"Calculated state: {new_state}")
    
    # Get next events
    print("\nNext event calculations:")
    next_rising = sun_helper.get_time_at_elevation(
        start_dt=test_time,
        target_elev=rising_elev,
        direction='rising',  # type: ignore[arg-type]
        max_days=365
    )
    
    next_setting = sun_helper.get_time_at_elevation(
        start_dt=test_time,
        target_elev=setting_elev,
        direction='setting',  # type: ignore[arg-type]
        max_days=365
    )
    
    print(f"Next rising at {rising_elev}°: {next_rising}")
    print(f"Next setting at {setting_elev}°: {next_setting}")
    
    # Determine next event
    if new_state:
        # When ON: next event is always setting
        next_event = next_setting
        event_type = "setting"
        print(f"When ON: next event is setting")
    else:
        # When OFF: next event depends on current sun movement
        if sun_direction == "rising":
            next_event = next_rising
            event_type = "rising"
            print(f"When OFF and rising: next event is rising")
        else:
            next_event = next_rising  # Next day's rising
            event_type = "rising"
            print(f"When OFF and setting: next event is next rising")
    
    print(f"Selected next event: {next_event} ({event_type})")
    
    # Calculate time to next event
    if next_event:
        time_diff = (next_event - test_time).total_seconds()
        hours = time_diff // 3600
        minutes = (time_diff % 3600) // 60
        print(f"Time to next event: {hours:.0f}h {minutes:.0f}m")
    
    print("-" * 60)
    return new_state, next_event, event_type

def test_around_transitions(latitude, longitude, elevation, rising_elev, setting_elev):
    """Test binary sensor logic around state transitions."""
    
    print("Testing around state transitions...")
    print("=" * 60)
    
    sun_helper = SunHelper(latitude, longitude, elevation)
    
    # Get current time
    now = datetime.now(timezone.utc)
    
    # Get next solar events
    try:
        next_noon = sun_helper.get_next_solar_noon(now)
        next_midnight = sun_helper.get_next_solar_midnight(now)
        
        print(f"Next solar noon: {next_noon}")
        print(f"Next solar midnight: {next_midnight}")
        
        # Test times around transitions
        test_times = []
        
        # Around noon (potential rising transition)
        if next_noon:
            test_times.extend([
                next_noon - timedelta(hours=2),
                next_noon - timedelta(hours=1),
                next_noon - timedelta(minutes=30),
                next_noon,
                next_noon + timedelta(minutes=30),
                next_noon + timedelta(hours=1),
            ])
        
        # Around midnight (potential setting transition)
        if next_midnight:
            test_times.extend([
                next_midnight - timedelta(hours=2),
                next_midnight - timedelta(hours=1),
                next_midnight - timedelta(minutes=30),
                next_midnight,
                next_midnight + timedelta(minutes=30),
                next_midnight + timedelta(hours=1),
            ])
        
        # Remove duplicates and sort
        test_times = sorted(list(set(test_times)))
        
        for test_time in test_times:
            print(f"\n{'='*40}")
            test_binary_sensor_logic(latitude, longitude, elevation, rising_elev, setting_elev, test_time)
            
    except Exception as e:
        print(f"Error getting solar events: {e}")

if __name__ == "__main__":
    # Example coordinates (you can change these)
    LATITUDE = 40.7128  # New York City
    LONGITUDE = -74.0060
    ELEVATION = 10.0
    RISING_ELEV = 30.0
    SETTING_ELEV = 30.0
    
    print("Sol Binary Sensor Debug Tool")
    print("=" * 60)
    
    # Test current time
    test_binary_sensor_logic(LATITUDE, LONGITUDE, ELEVATION, RISING_ELEV, SETTING_ELEV)
    
    # Test around transitions
    test_around_transitions(LATITUDE, LONGITUDE, ELEVATION, RISING_ELEV, SETTING_ELEV)
    
    print("\nDebug complete!") 