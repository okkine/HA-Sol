#!/usr/bin/env python3
"""
Test script to check binary sensor timing precision.
This helps identify if the issue is with the scheduled update times being slightly off.
"""

import sys
import os
from datetime import datetime, timedelta, timezone
import math

# Add the parent directory to the path so we can import the helper
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from helper import SunHelper

def test_binary_sensor_timing():
    """Test if the binary sensor timing is precise enough."""
    
    print("=== Testing Binary Sensor Precision ===\n")
    
    # Test parameters
    latitude = 40.7128  # New York
    longitude = -74.0060
    elevation = 10
    pressure = 1013.25
    temperature = 20
    
    # Binary sensor thresholds
    rising_elev = 10.0
    setting_elev = 5.0
    
    # Create sun helper
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Test around a specific time when the sun should be crossing a threshold
    test_time = datetime(2025, 6, 29, 10, 0, 0, tzinfo=timezone.utc)
    
    print(f"Test time: {test_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    
    # Get the scheduled update time for the rising threshold
    today_start = test_time.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    
    scheduled_rise = sun_helper.get_time_at_elevation(
        start_dt=today_start,
        target_elev=rising_elev,
        direction='rising',
        max_days=1
    )
    
    scheduled_set = sun_helper.get_time_at_elevation(
        start_dt=today_start,
        target_elev=setting_elev,
        direction='setting',
        max_days=1
    )
    
    print(f"Scheduled rise time: {scheduled_rise.strftime('%Y-%m-%d %H:%M:%S UTC') if scheduled_rise else 'None'}")
    print(f"Scheduled set time: {scheduled_set.strftime('%Y-%m-%d %H:%M:%S UTC') if scheduled_set else 'None'}")
    
    if scheduled_rise:
        # Test elevation at the scheduled time and around it
        print(f"\nTesting around scheduled rise time ({rising_elev}°):")
        
        # Test 5 minutes before, at, and 5 minutes after the scheduled time
        for offset in [-5, 0, 5]:
            test_dt = scheduled_rise + timedelta(minutes=offset)
            elev, az = sun_helper.calculate_position(test_dt)
            direction = sun_helper.sun_direction(test_dt)
            
            # Determine state using binary sensor logic
            if direction == "rising":
                state = elev >= rising_elev
                threshold_used = rising_elev
            else:
                state = elev >= setting_elev
                threshold_used = setting_elev
            
            print(f"  {offset:+3d} min: elev={elev:.2f}°, direction={direction}, state={'ON' if state else 'OFF'} (threshold: {threshold_used}°)")
    
    if scheduled_set:
        # Test elevation at the scheduled time and around it
        print(f"\nTesting around scheduled set time ({setting_elev}°):")
        
        # Test 5 minutes before, at, and 5 minutes after the scheduled time
        for offset in [-5, 0, 5]:
            test_dt = scheduled_set + timedelta(minutes=offset)
            elev, az = sun_helper.calculate_position(test_dt)
            direction = sun_helper.sun_direction(test_dt)
            
            # Determine state using binary sensor logic
            if direction == "rising":
                state = elev >= rising_elev
                threshold_used = rising_elev
            else:
                state = elev >= setting_elev
                threshold_used = setting_elev
            
            print(f"  {offset:+3d} min: elev={elev:.2f}°, direction={direction}, state={'ON' if state else 'OFF'} (threshold: {threshold_used}°)")

def test_state_transition_scenario():
    """Test a specific state transition scenario."""
    
    print("\n=== Testing State Transition Scenario ===\n")
    
    # Test parameters
    latitude = 40.7128
    longitude = -74.0060
    elevation = 10
    pressure = 1013.25
    temperature = 20
    
    # Binary sensor thresholds
    rising_elev = 10.0
    setting_elev = 5.0
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Simulate a scenario where the sun is setting and should turn OFF
    # Start with sun above setting threshold
    start_time = datetime(2025, 6, 29, 18, 0, 0, tzinfo=timezone.utc)
    
    print("Simulating setting scenario:")
    print(f"Start time: {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    
    # Get elevation at start
    start_elev, _ = sun_helper.calculate_position(start_time)
    start_direction = sun_helper.sun_direction(start_time)
    
    if start_direction == "rising":
        start_state = start_elev >= rising_elev
        threshold_used = rising_elev
    else:
        start_state = start_elev >= setting_elev
        threshold_used = setting_elev
    
    print(f"Start: elev={start_elev:.2f}°, direction={start_direction}, state={'ON' if start_state else 'OFF'} (threshold: {threshold_used}°)")
    
    # Find when the sun will cross the setting threshold
    scheduled_set = sun_helper.get_time_at_elevation(
        start_dt=start_time,
        target_elev=setting_elev,
        direction='setting',
        max_days=1
    )
    
    if scheduled_set:
        print(f"Scheduled set time: {scheduled_set.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Test at the scheduled time
        set_elev, _ = sun_helper.calculate_position(scheduled_set)
        set_direction = sun_helper.sun_direction(scheduled_set)
        
        if set_direction == "rising":
            set_state = set_elev >= rising_elev
            threshold_used = rising_elev
        else:
            set_state = set_elev >= setting_elev
            threshold_used = setting_elev
        
        print(f"At scheduled time: elev={set_elev:.2f}°, direction={set_direction}, state={'ON' if set_state else 'OFF'} (threshold: {threshold_used}°)")
        
        # Check if state should have changed
        if start_state != set_state:
            print("✅ State should change from {} to {}".format(
                "ON" if start_state else "OFF",
                "ON" if set_state else "OFF"
            ))
        else:
            print("❌ State should NOT change - this might be the issue!")
            
            # Test slightly after the scheduled time
            after_time = scheduled_set + timedelta(minutes=1)
            after_elev, _ = sun_helper.calculate_position(after_time)
            after_direction = sun_helper.sun_direction(after_time)
            
            if after_direction == "rising":
                after_state = after_elev >= rising_elev
                threshold_used = rising_elev
            else:
                after_state = after_elev >= setting_elev
                threshold_used = setting_elev
            
            print(f"1 min after: elev={after_elev:.2f}°, direction={after_direction}, state={'ON' if after_state else 'OFF'} (threshold: {threshold_used}°)")
            
            if start_state != after_state:
                print("✅ State would change 1 minute after scheduled time")
            else:
                print("❌ State still wouldn't change 1 minute after")

if __name__ == "__main__":
    test_binary_sensor_timing()
    test_state_transition_scenario() 