#!/usr/bin/env python3
"""
Debug script for Sol binary sensor logic.
Tests the new state determination and next change time calculation.
"""

import sys
import os
from datetime import datetime, timezone, timedelta

# Add the custom_components directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'custom_components'))

from sol.helper import SunHelper

def test_binary_sensor_logic():
    """Test the binary sensor logic with various scenarios."""
    
    # Test location (San Francisco)
    latitude = 37.7749
    longitude = -122.4194
    elevation = 0
    pressure = 1010.0
    temperature = 25.0
    
    # Create sun helper
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Test times throughout the day
    now = datetime.now(timezone.utc)
    test_times = [
        now.replace(hour=6, minute=0, second=0, microsecond=0),   # Early morning
        now.replace(hour=9, minute=0, second=0, microsecond=0),   # Morning
        now.replace(hour=12, minute=0, second=0, microsecond=0),  # Noon
        now.replace(hour=15, minute=0, second=0, microsecond=0),  # Afternoon
        now.replace(hour=18, minute=0, second=0, microsecond=0),  # Evening
        now.replace(hour=21, minute=0, second=0, microsecond=0),  # Night
    ]
    
    # Test elevation thresholds
    rising_elev = 10.0  # 10 degrees above horizon
    setting_elev = 5.0   # 5 degrees above horizon
    
    print("=== Binary Sensor Logic Test ===")
    print(f"Location: {latitude}, {longitude}")
    print(f"Rising threshold: {rising_elev}°")
    print(f"Setting threshold: {setting_elev}°")
    print()
    
    for test_time in test_times:
        print(f"--- Testing at {test_time.strftime('%Y-%m-%d %H:%M:%S UTC')} ---")
        
        # Get current elevation and direction
        current_elev, azimuth = sun_helper.calculate_position(test_time)
        sun_direction = sun_helper.sun_direction(test_time)
        
        print(f"Current elevation: {current_elev:.2f}°")
        print(f"Sun direction: {sun_direction}")
        
        # Determine state based on direction and threshold
        if sun_direction == "rising":
            state = current_elev >= rising_elev
            threshold_used = rising_elev
        else:  # setting
            state = current_elev >= setting_elev
            threshold_used = setting_elev
        
        print(f"State: {'ON' if state else 'OFF'} (threshold: {threshold_used}°)")
        
        # Calculate today's rise and set times
        today_rise = sun_helper.get_time_at_elevation(
            start_dt=test_time,
            target_elev=rising_elev,
            direction='rising',
            max_days=365
        )
        
        today_set = sun_helper.get_time_at_elevation(
            start_dt=test_time,
            target_elev=setting_elev,
            direction='setting',
            max_days=365
        )
        
        print(f"Today's rise: {today_rise.strftime('%H:%M:%S') if today_rise else 'None'}")
        print(f"Today's set: {today_set.strftime('%H:%M:%S') if today_set else 'None'}")
        
        # Calculate next change
        next_change = None
        next_event_type = None
        
        if today_rise and today_set:
            if test_time < today_rise:
                next_change = today_rise
                next_event_type = "rising"
            elif test_time < today_set:
                next_change = today_set
                next_event_type = "setting"
            else:
                # Both passed - look for tomorrow's rise
                tomorrow_rise = sun_helper.get_time_at_elevation(
                    start_dt=today_set + timedelta(minutes=1),
                    target_elev=rising_elev,
                    direction='rising',
                    max_days=365
                )
                if tomorrow_rise:
                    next_change = tomorrow_rise
                    next_event_type = "rising"
        elif today_rise:
            if test_time < today_rise:
                next_change = today_rise
                next_event_type = "rising"
            else:
                # Rise passed - look for next rise
                next_rise = sun_helper.get_time_at_elevation(
                    start_dt=today_rise + timedelta(minutes=1),
                    target_elev=rising_elev,
                    direction='rising',
                    max_days=365
                )
                if next_rise:
                    next_change = next_rise
                    next_event_type = "rising"
        elif today_set:
            if test_time < today_set:
                next_change = today_set
                next_event_type = "setting"
            else:
                # Set passed - look for next set
                next_set = sun_helper.get_time_at_elevation(
                    start_dt=today_set + timedelta(minutes=1),
                    target_elev=setting_elev,
                    direction='setting',
                    max_days=365
                )
                if next_set:
                    next_change = next_set
                    next_event_type = "setting"
        
        if next_change:
            print(f"Next change: {next_change.strftime('%Y-%m-%d %H:%M:%S')} ({next_event_type})")
        else:
            print("Next change: unknown (no events found within 365 days)")
        
        print()

if __name__ == "__main__":
    test_binary_sensor_logic() 