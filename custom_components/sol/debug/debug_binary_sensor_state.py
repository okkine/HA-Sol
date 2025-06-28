#!/usr/bin/env python3
"""
Debug script to test binary sensor state determination logic.
This script helps identify why binary sensors sometimes don't change state when they should.
"""

import sys
import os
from datetime import datetime, timedelta, timezone
import math

# Add the parent directory to the path so we can import the helper
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from helper import SunHelper

def test_binary_sensor_state_logic():
    """Test the binary sensor state determination logic with various scenarios."""
    
    print("=== Testing Binary Sensor State Logic ===\n")
    
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
    
    # Test times throughout the day
    test_times = [
        datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0),   # Early morning
        datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0),   # Morning
        datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0),  # Mid-morning
        datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0),  # Solar noon
        datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0),  # Afternoon
        datetime.now(timezone.utc).replace(hour=16, minute=0, second=0, microsecond=0),  # Late afternoon
        datetime.now(timezone.utc).replace(hour=18, minute=0, second=0, microsecond=0),  # Evening
        datetime.now(timezone.utc).replace(hour=20, minute=0, second=0, microsecond=0),  # Night
    ]
    
    print(f"Rising threshold: {rising_elev}°")
    print(f"Setting threshold: {setting_elev}°")
    print("-" * 80)
    
    for test_time in test_times:
        print(f"\nTime: {test_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Get current elevation and direction
        current_elev, current_azimuth = sun_helper.calculate_position(test_time)
        sun_direction = sun_helper.sun_direction(test_time)
        
        print(f"  Current elevation: {current_elev:.2f}°")
        print(f"  Current azimuth: {current_azimuth:.2f}°")
        print(f"  Sun direction: {sun_direction}")
        
        # Determine state based on direction and threshold (simulating binary sensor logic)
        if sun_direction == "rising":
            new_state = current_elev >= rising_elev
            threshold_used = rising_elev
        else:  # setting
            new_state = current_elev >= setting_elev
            threshold_used = setting_elev
        
        print(f"  Threshold used: {threshold_used}° ({sun_direction} threshold)")
        print(f"  State: {'ON' if new_state else 'OFF'}")
        print(f"  Logic: {current_elev:.2f}° {'>=' if current_elev >= threshold_used else '<'} {threshold_used}°")
        
        # Check if this would be a state change scenario
        if sun_direction == "rising":
            if rising_elev <= current_elev < rising_elev + 1:
                print(f"  ⚠️  NEAR RISING THRESHOLD - potential state change zone")
        else:  # setting
            if setting_elev <= current_elev < setting_elev + 1:
                print(f"  ⚠️  NEAR SETTING THRESHOLD - potential state change zone")
        
        # Test what happens if we use the opposite threshold
        if sun_direction == "rising":
            opposite_state = current_elev >= setting_elev
            print(f"  If using setting threshold: {'ON' if opposite_state else 'OFF'} ({current_elev:.2f}° {'>=' if current_elev >= setting_elev else '<'} {setting_elev}°)")
        else:
            opposite_state = current_elev >= rising_elev
            print(f"  If using rising threshold: {'ON' if opposite_state else 'OFF'} ({current_elev:.2f}° {'>=' if current_elev >= rising_elev else '<'} {rising_elev}°)")
        
        # Check if direction detection might be wrong
        future = test_time + timedelta(minutes=15)
        future_elev, _ = sun_helper.calculate_position(future)
        elevation_trend = "rising" if future_elev > current_elev else "setting"
        
        if elevation_trend != sun_direction:
            print(f"  ⚠️  DIRECTION MISMATCH: sun_direction={sun_direction}, elevation_trend={elevation_trend}")
            print(f"     Current: {current_elev:.2f}°, Future: {future_elev:.2f}°")

def test_state_transitions():
    """Test specific state transition scenarios."""
    
    print("\n=== Testing State Transitions ===\n")
    
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
    
    # Test around the rising threshold
    print("Testing around rising threshold (10°):")
    for elev in [9.5, 9.8, 10.0, 10.2, 10.5]:
        test_time = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
        
        # Simulate the elevation
        sun_direction = sun_helper.sun_direction(test_time)
        
        if sun_direction == "rising":
            state = elev >= rising_elev
            threshold_used = rising_elev
        else:
            state = elev >= setting_elev
            threshold_used = setting_elev
        
        print(f"  Elevation: {elev:.1f}°, Direction: {sun_direction}, State: {'ON' if state else 'OFF'} (threshold: {threshold_used}°)")
    
    print("\nTesting around setting threshold (5°):")
    for elev in [4.5, 4.8, 5.0, 5.2, 5.5]:
        test_time = datetime.now(timezone.utc).replace(hour=18, minute=0, second=0, microsecond=0)
        
        # Simulate the elevation
        sun_direction = sun_helper.sun_direction(test_time)
        
        if sun_direction == "rising":
            state = elev >= rising_elev
            threshold_used = rising_elev
        else:
            state = elev >= setting_elev
            threshold_used = setting_elev
        
        print(f"  Elevation: {elev:.1f}°, Direction: {sun_direction}, State: {'ON' if state else 'OFF'} (threshold: {threshold_used}°)")

def test_direction_consistency():
    """Test if sun direction is consistent around transition times."""
    
    print("\n=== Testing Direction Consistency ===\n")
    
    # Test parameters
    latitude = 40.7128
    longitude = -74.0060
    elevation = 10
    pressure = 1013.25
    temperature = 20
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Test times around solar noon and midnight
    test_times = [
        datetime.now(timezone.utc).replace(hour=11, minute=30, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=11, minute=45, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=12, minute=15, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=12, minute=30, second=0, microsecond=0),
    ]
    
    print("Testing direction consistency around solar noon:")
    for test_time in test_times:
        current_elev, _ = sun_helper.calculate_position(test_time)
        sun_direction = sun_helper.sun_direction(test_time)
        
        print(f"  {test_time.strftime('%H:%M:%S')}: elevation={current_elev:.2f}°, direction={sun_direction}")
    
    # Test times around solar midnight
    test_times = [
        datetime.now(timezone.utc).replace(hour=23, minute=30, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=23, minute=45, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=0, minute=15, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=0, minute=30, second=0, microsecond=0),
    ]
    
    print("\nTesting direction consistency around solar midnight:")
    for test_time in test_times:
        current_elev, _ = sun_helper.calculate_position(test_time)
        sun_direction = sun_helper.sun_direction(test_time)
        
        print(f"  {test_time.strftime('%H:%M:%S')}: elevation={current_elev:.2f}°, direction={sun_direction}")

if __name__ == "__main__":
    test_binary_sensor_state_logic()
    test_state_transitions()
    test_direction_consistency() 