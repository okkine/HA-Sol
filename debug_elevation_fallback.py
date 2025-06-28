#!/usr/bin/env python3
"""
Debug script to test the improved fallback logic for elevation sensor.
This script simulates the elevation sensor's fallback behavior when the sun
doesn't reach the target elevation.
"""

import sys
import os
from datetime import datetime, timezone, timedelta
import math

# Add the current directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helper import SunHelper

def test_fallback_logic():
    """Test the improved fallback logic for different scenarios."""
    
    # Example coordinates (you can change these)
    latitude = 40.7128  # New York
    longitude = -74.0060
    elevation = 10.0
    pressure = 1013.25
    temperature = 15.0
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    print("=== Testing Improved Fallback Logic ===\n")
    
    # Test scenarios around solar midnight
    test_times = [
        datetime.now(timezone.utc).replace(hour=23, minute=30, second=0, microsecond=0),  # Near midnight
        datetime.now(timezone.utc).replace(hour=0, minute=30, second=0, microsecond=0),   # After midnight
        datetime.now(timezone.utc).replace(hour=18, minute=0, second=0, microsecond=0),   # Evening
        datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0),    # Early morning
    ]
    
    for test_time in test_times:
        print(f"Testing at: {test_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Get current elevation and direction
        current_elev, current_azimuth = sun_helper.calculate_position(test_time)
        direction = sun_helper.sun_direction(test_time)
        
        print(f"  Current elevation: {current_elev:.2f}°")
        print(f"  Current azimuth: {current_azimuth:.2f}°")
        print(f"  Sun direction: {direction}")
        
        # Simulate a target that the sun won't reach
        if direction == "setting":
            target_elev = current_elev + 5.0  # Higher than current (won't reach)
        else:
            target_elev = current_elev - 5.0  # Lower than current (won't reach)
        
        print(f"  Target elevation: {target_elev:.2f}° (unreachable)")
        
        # Try to find the elevation event
        event_time = sun_helper.get_time_at_elevation(
            start_dt=test_time,
            target_elev=target_elev,
            direction=direction,  # type: ignore[arg-type]
            max_days=1
        )
        
        if event_time:
            print(f"  Elevation event found: {event_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        else:
            print("  No elevation event found - using fallback logic")
            
            # Apply the improved fallback logic
            try:
                next_peak = sun_helper.get_peak_elevation_time(test_time)
                next_midnight = sun_helper.get_next_solar_midnight(test_time)
                
                print(f"    Next peak elevation: {next_peak.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                print(f"    Next solar midnight: {next_midnight.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                
                # Smart selection based on current time and direction
                if next_peak and next_midnight:
                    current_hour = test_time.hour
                    
                    if direction == "setting" and current_hour >= 18:  # Evening/night
                        fallback_time = next_midnight
                        reason = "Setting near midnight, using next solar midnight"
                    elif direction == "rising" and current_hour <= 6:  # Early morning
                        fallback_time = next_peak
                        reason = "Rising near dawn, using next peak elevation"
                    else:
                        # Choose the event that's closer in time but still in the right direction
                        time_to_peak = (next_peak - test_time).total_seconds() if next_peak > test_time else float('inf')
                        time_to_midnight = (next_midnight - test_time).total_seconds() if next_midnight > test_time else float('inf')
                        
                        if time_to_midnight < time_to_peak and direction == "setting":
                            fallback_time = next_midnight
                            reason = "Using closer solar midnight"
                        elif time_to_peak < time_to_midnight and direction == "rising":
                            fallback_time = next_peak
                            reason = "Using closer peak elevation"
                        else:
                            # Fallback to the earlier event
                            fallback_time = min(next_peak, next_midnight)
                            reason = "Using earlier solar event"
                    
                    print(f"    Fallback selected: {fallback_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                    print(f"    Reason: {reason}")
                    
                    # Calculate time until fallback
                    time_until = fallback_time - test_time
                    print(f"    Time until fallback: {time_until}")
                    
                elif next_peak:
                    print(f"    Using next peak elevation: {next_peak.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                elif next_midnight:
                    print(f"    Using next solar midnight: {next_midnight.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                else:
                    print("    No solar events found - emergency fallback")
                    
            except Exception as e:
                print(f"    Error in fallback logic: {e}")
        
        print()

def test_specific_midnight_scenario():
    """Test the specific scenario where the sensor stalls near solar midnight."""
    
    print("=== Testing Specific Midnight Scenario ===\n")
    
    # Example coordinates
    latitude = 40.7128
    longitude = -74.0060
    elevation = 10.0
    pressure = 1013.25
    temperature = 15.0
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Test time just before solar midnight
    test_time = datetime.now(timezone.utc).replace(hour=23, minute=45, second=0, microsecond=0)
    
    print(f"Test time: {test_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    
    # Get current elevation and direction
    current_elev, current_azimuth = sun_helper.calculate_position(test_time)
    direction = sun_helper.sun_direction(test_time)
    
    print(f"Current elevation: {current_elev:.2f}°")
    print(f"Sun direction: {direction}")
    
    # Simulate a step size and calculate target
    step = 5.0
    if direction == "setting":
        next_target = round(current_elev / step) * step - step
    else:
        next_target = round(current_elev / step) * step + step
    
    print(f"Step size: {step}°")
    print(f"Target elevation: {next_target:.2f}°")
    
    # Try to find the elevation event
    event_time = sun_helper.get_time_at_elevation(
        start_dt=test_time,
        target_elev=next_target,
        direction=direction,  # type: ignore[arg-type]
        max_days=1
    )
    
    if not event_time:
        print("No elevation event found - testing fallback logic")
        
        next_peak = sun_helper.get_peak_elevation_time(test_time)
        next_midnight = sun_helper.get_next_solar_midnight(test_time)
        
        print(f"Next peak: {next_peak.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Next midnight: {next_midnight.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Old logic (always choose earlier)
        old_fallback = min(next_peak, next_midnight)
        print(f"Old logic would choose: {old_fallback.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # New logic
        if direction == "setting" and test_time.hour >= 18:
            new_fallback = next_midnight
            reason = "Setting near midnight"
        else:
            new_fallback = min(next_peak, next_midnight)
            reason = "Default logic"
        
        print(f"New logic chooses: {new_fallback.strftime('%Y-%m-%d %H:%M:%S UTC')} ({reason})")
        
        # Show the difference
        if old_fallback != new_fallback:
            print(f"IMPROVEMENT: New logic avoids stalling by choosing {new_fallback.strftime('%H:%M:%S')} instead of {old_fallback.strftime('%H:%M:%S')}")
        else:
            print("No change in this scenario")

if __name__ == "__main__":
    test_fallback_logic()
    print("\n" + "="*50 + "\n")
    test_specific_midnight_scenario() 