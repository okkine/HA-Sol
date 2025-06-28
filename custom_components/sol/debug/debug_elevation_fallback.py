#!/usr/bin/env python3
"""
Debug script to test elevation sensor fallback logic.
Tests the new logic for handling cases where:
1. Calculated target elevation exceeds sun's maximum elevation
2. Calculated target elevation is below sun's minimum elevation (midnight)
"""

import sys
import os
from datetime import datetime, timedelta, timezone
import math

# Add the parent directory to the path so we can import the helper
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from helper import SunHelper

def test_elevation_fallback_logic():
    """Test the elevation fallback logic with various scenarios."""
    
    # Test parameters
    latitude = 40.7128  # New York
    longitude = -74.0060
    elevation = 10
    pressure = 1013.25
    temperature = 20
    
    # Create sun helper
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    print("=== Testing Elevation Fallback Logic ===\n")
    
    # Test times throughout the day
    test_times = [
        datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0),  # Early morning
        datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0), # Mid-morning
        datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0), # Solar noon
        datetime.now(timezone.utc).replace(hour=16, minute=0, second=0, microsecond=0), # Late afternoon
        datetime.now(timezone.utc).replace(hour=20, minute=0, second=0, microsecond=0), # Evening
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0),  # Midnight
    ]
    
    # Test different step sizes
    step_sizes = [5.0, 10.0, 15.0]
    
    for step in step_sizes:
        print(f"\n--- Testing with step size: {step}° ---")
        
        for test_time in test_times:
            print(f"\nTime: {test_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            
            # Get current elevation and direction
            current_elev, current_azimuth = sun_helper.calculate_position(test_time)
            direction = sun_helper.sun_direction(test_time)
            
            print(f"  Current elevation: {current_elev:.2f}°")
            print(f"  Current azimuth: {current_azimuth:.2f}°")
            print(f"  Sun direction: {direction}")
            
            # Calculate next target elevation (simulating sensor logic)
            if direction == "rising":
                next_target = round(current_elev / step) * step + step
            else:
                next_target = round(current_elev / step) * step - step
            
            # Clamp to physical limits
            next_target = max(min(next_target, 90), -90)
            
            print(f"  Calculated target: {next_target:.2f}°")
            
            # Check peak elevation
            try:
                peak_time = sun_helper.get_peak_elevation_time(test_time)
                if peak_time:
                    peak_elev, _ = sun_helper.calculate_position(peak_time)
                    print(f"  Peak elevation: {peak_elev:.2f}° at {peak_time.strftime('%H:%M:%S')}")
                    
                    if next_target > peak_elev:
                        print(f"  *** TARGET EXCEEDS PEAK! Using peak elevation: {peak_elev:.2f}° ***")
                        print(f"  Next update scheduled for: {peak_time.strftime('%H:%M:%S')}")
                    else:
                        print(f"  Target is within peak range")
            except Exception as e:
                print(f"  Error checking peak elevation: {e}")
            
            # Check midnight elevation
            try:
                next_midnight = sun_helper.get_next_solar_midnight(test_time)
                if next_midnight:
                    midnight_elev, _ = sun_helper.calculate_position(next_midnight)
                    print(f"  Midnight elevation: {midnight_elev:.2f}° at {next_midnight.strftime('%H:%M:%S')}")
                    
                    if next_target < midnight_elev:
                        print(f"  *** TARGET BELOW MIDNIGHT! Using midnight elevation: {midnight_elev:.2f}° ***")
                        print(f"  Next update scheduled for: {next_midnight.strftime('%H:%M:%S')}")
                    else:
                        print(f"  Target is above midnight range")
            except Exception as e:
                print(f"  Error checking midnight elevation: {e}")
            
            print("-" * 50)

def test_edge_cases():
    """Test edge cases and boundary conditions."""
    
    print("\n=== Testing Edge Cases ===\n")
    
    # Test parameters
    latitude = 40.7128
    longitude = -74.0060
    elevation = 10
    pressure = 1013.25
    temperature = 20
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Test near solar noon
    noon_time = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    current_elev, _ = sun_helper.calculate_position(noon_time)
    
    print(f"Near solar noon (elevation: {current_elev:.2f}°)")
    
    # Test with very small step size
    step = 1.0
    next_target = round(current_elev / step) * step + step
    
    try:
        peak_time = sun_helper.get_peak_elevation_time(noon_time)
        if peak_time:
            peak_elev, _ = sun_helper.calculate_position(peak_time)
            print(f"  Peak elevation: {peak_elev:.2f}°")
            print(f"  Calculated target: {next_target:.2f}°")
            
            if next_target > peak_elev:
                print(f"  *** Would use peak elevation fallback ***")
            else:
                print(f"  Target is within range")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Test near solar midnight
    midnight_time = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    current_elev, _ = sun_helper.calculate_position(midnight_time)
    
    print(f"\nNear solar midnight (elevation: {current_elev:.2f}°)")
    
    next_target = round(current_elev / step) * step - step
    
    try:
        next_midnight = sun_helper.get_next_solar_midnight(midnight_time)
        if next_midnight:
            midnight_elev, _ = sun_helper.calculate_position(next_midnight)
            print(f"  Midnight elevation: {midnight_elev:.2f}°")
            print(f"  Calculated target: {next_target:.2f}°")
            
            if next_target < midnight_elev:
                print(f"  *** Would use midnight elevation fallback ***")
            else:
                print(f"  Target is within range")
    except Exception as e:
        print(f"  Error: {e}")

if __name__ == "__main__":
    test_elevation_fallback_logic()
    test_edge_cases()
    print("\n=== Test Complete ===") 