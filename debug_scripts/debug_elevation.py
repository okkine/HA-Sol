#!/usr/bin/env python3
"""
Debug script for Sol elevation sensor to identify stalling issues around solar midnight.
Run this script to test the elevation calculation logic at different times.
"""

import sys
import os
from datetime import datetime, timedelta, timezone
import math
import ephem

# Add the current directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helper import SunHelper

def test_elevation_calculations(latitude, longitude, elevation, step_size, test_time=None):
    """Test elevation calculations around the given time."""
    
    if test_time is None:
        test_time = datetime.now(timezone.utc)
    
    print(f"Testing elevation calculations for location: {latitude}°N, {longitude}°E")
    print(f"Test time: {test_time}")
    print(f"Step size: {step_size}°")
    print("-" * 60)
    
    # Create sun helper
    sun_helper = SunHelper(latitude, longitude, elevation)
    
    # Test current position
    current_elev, current_azimuth = sun_helper.calculate_position(test_time)
    print(f"Current elevation: {current_elev:.2f}°")
    print(f"Current azimuth: {current_azimuth:.2f}°")
    
    # Test direction detection
    try:
        direction = sun_helper.sun_direction(test_time)
        print(f"Sun direction: {direction}")
    except Exception as e:
        print(f"Error determining direction: {e}")
        direction = "unknown"
    
    # Calculate target elevation
    if direction == "rising":
        next_target = round(current_elev / step_size) * step_size + step_size
    else:
        next_target = round(current_elev / step_size) * step_size - step_size
    
    next_target = max(min(next_target, 90), -90)
    print(f"Next target elevation: {next_target:.2f}°")
    
    # Test getting time at elevation
    if direction in ["rising", "setting"]:
        # Type assertion is safe here since we've validated direction
        event_time = sun_helper.get_time_at_elevation(
            start_dt=test_time,
            target_elev=next_target,
            direction=direction,  # type: ignore[arg-type]
            max_days=1
        )
        
        if event_time:
            print(f"Next elevation event: {event_time}")
        else:
            print("No elevation event found!")
            
            # Test solar events as fallback
            print("\nTesting solar event fallbacks:")
            try:
                next_noon = sun_helper.get_next_solar_noon(test_time)
                next_midnight = sun_helper.get_next_solar_midnight(test_time)
                
                print(f"Next solar noon: {next_noon}")
                print(f"Next solar midnight: {next_midnight}")
                
                if next_noon and next_midnight:
                    earlier = min(next_noon, next_midnight)
                    print(f"Earlier solar event: {earlier}")
                elif next_noon:
                    print(f"Using next solar noon: {next_noon}")
                elif next_midnight:
                    print(f"Using next solar midnight: {next_midnight}")
                else:
                    print("No solar events found!")
                    
            except Exception as e:
                print(f"Error getting solar events: {e}")
    
    print("-" * 60)

def test_around_midnight(latitude, longitude, elevation, step_size):
    """Test elevation calculations around solar midnight."""
    
    print("Testing around solar midnight...")
    print("=" * 60)
    
    # Create sun helper
    sun_helper = SunHelper(latitude, longitude, elevation)
    
    # Get current time
    now = datetime.now(timezone.utc)
    
    # Get next solar midnight
    try:
        next_midnight = sun_helper.get_next_solar_midnight(now)
        print(f"Next solar midnight: {next_midnight}")
        
        # Test times around midnight
        test_times = [
            next_midnight - timedelta(hours=2),
            next_midnight - timedelta(hours=1),
            next_midnight - timedelta(minutes=30),
            next_midnight,
            next_midnight + timedelta(minutes=30),
            next_midnight + timedelta(hours=1),
            next_midnight + timedelta(hours=2),
        ]
        
        for test_time in test_times:
            print(f"\nTesting at: {test_time}")
            test_elevation_calculations(latitude, longitude, elevation, step_size, test_time)
            
    except Exception as e:
        print(f"Error getting solar midnight: {e}")

if __name__ == "__main__":
    # Example coordinates (you can change these)
    LATITUDE = 40.7128  # New York City
    LONGITUDE = -74.0060
    ELEVATION = 10.0
    STEP_SIZE = 5.0
    
    print("Sol Elevation Sensor Debug Tool")
    print("=" * 60)
    
    # Test current time
    test_elevation_calculations(LATITUDE, LONGITUDE, ELEVATION, STEP_SIZE)
    
    # Test around midnight
    test_around_midnight(LATITUDE, LONGITUDE, ELEVATION, STEP_SIZE)
    
    print("\nDebug complete!") 