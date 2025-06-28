#!/usr/bin/env python3
"""
Debug script to test the peak elevation fix for the Sol integration.

This script simulates the elevation sensor logic to verify that when the calculated
target elevation exceeds the sun's maximum elevation, the sensor properly schedules
updates for the peak elevation time instead of using small fractional intervals.
"""

import sys
import os
import logging
from datetime import datetime, timedelta, timezone
import math

# Add the parent directory to the path so we can import the helper
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from helper import SunHelper

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)

def test_peak_elevation_fix():
    """Test the peak elevation fix logic."""
    
    # Example coordinates (you can change these to your location)
    latitude = 40.7128  # New York City
    longitude = -74.0060
    elevation = 10.0
    pressure = 1013.25
    temperature = 15.0
    
    # Create sun helper
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Test parameters
    step = 5.0  # 5-degree elevation steps
    
    print("=== Testing Peak Elevation Fix ===")
    print(f"Location: {latitude}°N, {longitude}°E")
    print(f"Elevation step: {step}°")
    print()
    
    # Test times around solar maximum (adjust these for your location and current date)
    test_times = [
        datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=11, minute=0, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=13, minute=0, second=0, microsecond=0),
        datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0),
    ]
    
    for test_time in test_times:
        print(f"\n--- Testing at {test_time.strftime('%Y-%m-%d %H:%M:%S UTC')} ---")
        
        # Get current elevation and direction
        current_elev, current_azimuth = sun_helper.calculate_position(test_time)
        direction = sun_helper.sun_direction(test_time)
        
        print(f"Current elevation: {current_elev:.2f}°")
        print(f"Current azimuth: {current_azimuth:.2f}°")
        print(f"Sun direction: {direction}")
        
        # Calculate next target elevation (simulating sensor logic)
        if direction == "rising":
            next_target = round(current_elev / step) * step + step
        else:
            next_target = round(current_elev / step) * step - step
        
        # Clamp to physical limits
        next_target = max(min(next_target, 90), -90)
        
        print(f"Calculated target elevation: {next_target:.2f}°")
        
        # Check peak elevation
        try:
            peak_time = sun_helper.get_peak_elevation_time(test_time)
            if peak_time:
                peak_elev, _ = sun_helper.calculate_position(peak_time)
                print(f"Peak elevation time: {peak_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                print(f"Peak elevation: {peak_elev:.2f}°")
                
                # Check if target exceeds peak
                if next_target > peak_elev:
                    print("✅ TARGET EXCEEDS PEAK - Should schedule for peak time")
                    print(f"   Next update would be at: {peak_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                    time_diff = (peak_time - test_time).total_seconds() / 60
                    print(f"   Time until update: {time_diff:.1f} minutes")
                else:
                    print("✅ Target within peak range - Normal elevation tracking")
                    
                    # Test normal elevation tracking
                    event_time = sun_helper.get_time_at_elevation(
                        start_dt=test_time,
                        target_elev=next_target,
                        direction=direction,  # type: ignore[arg-type]
                        max_days=1
                    )
                    
                    if event_time:
                        print(f"   Next update would be at: {event_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                        time_diff = (event_time - test_time).total_seconds() / 60
                        print(f"   Time until update: {time_diff:.1f} minutes")
                    else:
                        print("   ❌ No elevation event found - This would trigger fallback")
            else:
                print("❌ Could not determine peak elevation time")
                
        except Exception as e:
            print(f"❌ Error checking peak elevation: {e}")
        
        print("-" * 50)

def test_edge_cases():
    """Test edge cases around solar maximum."""
    
    print("\n=== Testing Edge Cases ===")
    
    # Example coordinates
    latitude = 40.7128
    longitude = -74.0060
    elevation = 10.0
    pressure = 1013.25
    temperature = 15.0
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    step = 5.0
    
    # Test with different step sizes
    step_sizes = [1.0, 2.0, 5.0, 10.0]
    
    for step_size in step_sizes:
        print(f"\n--- Testing with step size: {step_size}° ---")
        
        # Test around solar noon
        test_time = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        current_elev, _ = sun_helper.calculate_position(test_time)
        direction = sun_helper.sun_direction(test_time)
        
        print(f"Current elevation: {current_elev:.2f}°")
        print(f"Direction: {direction}")
        
        # Calculate target
        if direction == "rising":
            next_target = round(current_elev / step_size) * step_size + step_size
        else:
            next_target = round(current_elev / step_size) * step_size - step_size
        
        next_target = max(min(next_target, 90), -90)
        print(f"Target elevation: {next_target:.2f}°")
        
        # Check peak
        try:
            peak_time = sun_helper.get_peak_elevation_time(test_time)
            if peak_time:
                peak_elev, _ = sun_helper.calculate_position(peak_time)
                print(f"Peak elevation: {peak_elev:.2f}°")
                
                if next_target > peak_elev:
                    print(f"✅ Would schedule for peak time: {peak_time.strftime('%H:%M:%S')}")
                else:
                    print("✅ Normal elevation tracking")
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_peak_elevation_fix()
    test_edge_cases()
    print("\n=== Test Complete ===") 