#!/usr/bin/env python3
"""
Debug script to test the improved peak elevation time calculation.
This script compares the old transit-based method with the new next_pass() method.
"""

import sys
import os
from datetime import datetime, timezone, timedelta
import math
import ephem

# Add the current directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helper import SunHelper

def test_peak_elevation_calculation():
    """Test the improved peak elevation time calculation."""
    
    # Example coordinates
    latitude = 40.7128  # New York
    longitude = -74.0060
    elevation = 10.0
    pressure = 1013.25
    temperature = 15.0
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    print("=== Testing Peak Elevation Time Calculation ===\n")
    
    # Test times throughout the day
    test_times = [
        datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0),   # Morning
        datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0),  # Noon
        datetime.now(timezone.utc).replace(hour=18, minute=0, second=0, microsecond=0),  # Evening
        datetime.now(timezone.utc).replace(hour=23, minute=0, second=0, microsecond=0),  # Night
    ]
    
    for test_time in test_times:
        print(f"Testing at: {test_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Get current elevation
        current_elev, current_azimuth = sun_helper.calculate_position(test_time)
        print(f"  Current elevation: {current_elev:.2f}°")
        print(f"  Current azimuth: {current_azimuth:.2f}°")
        
        # Get peak elevation time using new method
        try:
            peak_time = sun_helper.get_peak_elevation_time(test_time)
            print(f"  Peak elevation time: {peak_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            
            # Calculate time until peak
            time_until_peak = peak_time - test_time
            print(f"  Time until peak: {time_until_peak}")
            
            # Get elevation at peak time
            peak_elev, peak_azimuth = sun_helper.calculate_position(peak_time)
            print(f"  Peak elevation: {peak_elev:.2f}°")
            print(f"  Peak azimuth: {peak_azimuth:.2f}°")
            
        except Exception as e:
            print(f"  Error calculating peak time: {e}")
        
        print()

def compare_transit_vs_pass():
    """Compare the old transit method with the new pass method."""
    
    print("=== Comparing Transit vs Pass Methods ===\n")
    
    # Example coordinates
    latitude = 40.7128
    longitude = -74.0060
    elevation = 10.0
    pressure = 1013.25
    temperature = 15.0
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Test time
    test_time = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    
    print(f"Test time: {test_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    
    # Setup observer
    observer = ephem.Observer()
    observer.lat = str(latitude)
    observer.lon = str(longitude)
    observer.elevation = elevation
    observer.pressure = pressure
    observer.temp = temperature
    observer.date = ephem.Date(test_time.astimezone(timezone.utc).replace(tzinfo=None))
    
    sun = ephem.Sun()
    
    # Old method: next_transit (solar noon)
    try:
        transit_time = observer.next_transit(sun)
        transit_dt = transit_time.datetime().replace(tzinfo=timezone.utc)
        transit_elev, _ = sun_helper.calculate_position(transit_dt)
        print(f"Transit time (solar noon): {transit_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Transit elevation: {transit_elev:.2f}°")
    except Exception as e:
        print(f"Error with transit method: {e}")
    
    # New method: next_pass (actual peak)
    try:
        pass_info = observer.next_pass(sun)
        max_alt_time = pass_info[2]  # Maximum altitude time
        max_alt = pass_info[3]       # Maximum altitude
        pass_dt = max_alt_time.datetime().replace(tzinfo=timezone.utc)
        pass_elev, _ = sun_helper.calculate_position(pass_dt)
        print(f"Pass time (peak elevation): {pass_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Pass elevation: {pass_elev:.2f}°")
        print(f"PyEphem max altitude: {math.degrees(max_alt):.2f}°")
    except Exception as e:
        print(f"Error with pass method: {e}")
    
    # Show difference
    if 'transit_dt' in locals() and 'pass_dt' in locals() and 'transit_elev' in locals() and 'pass_elev' in locals():
        time_diff = abs((transit_dt - pass_dt).total_seconds() / 60)  # minutes
        elev_diff = abs(transit_elev - pass_elev)
        print(f"\nDifference:")
        print(f"  Time difference: {time_diff:.1f} minutes")
        print(f"  Elevation difference: {elev_diff:.2f}°")
        
        if time_diff > 1.0:  # More than 1 minute difference
            print(f"  SIGNIFICANT: Peak elevation occurs {time_diff:.1f} minutes from solar noon!")

def test_fallback_logic_with_peak():
    """Test the fallback logic with the improved peak elevation time."""
    
    print("=== Testing Fallback Logic with Peak Elevation ===\n")
    
    # Example coordinates
    latitude = 40.7128
    longitude = -74.0060
    elevation = 10.0
    pressure = 1013.25
    temperature = 15.0
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Test time near midnight
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
        
        print(f"Next peak elevation: {next_peak.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Next solar midnight: {next_midnight.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Apply the improved fallback logic
        if direction == "setting" and test_time.hour >= 18:
            fallback_time = next_midnight
            reason = "Setting near midnight"
        else:
            fallback_time = min(next_peak, next_midnight)
            reason = "Default logic"
        
        print(f"Fallback selected: {fallback_time.strftime('%Y-%m-%d %H:%M:%S UTC')} ({reason})")
        
        # Show the difference from solar noon
        if fallback_time == next_peak:
            solar_noon = sun_helper.get_next_solar_noon(test_time)
            time_diff = abs((fallback_time - solar_noon).total_seconds() / 60)
            print(f"Peak elevation occurs {time_diff:.1f} minutes from solar noon")

if __name__ == "__main__":
    test_peak_elevation_calculation()
    print("\n" + "="*50 + "\n")
    compare_transit_vs_pass()
    print("\n" + "="*50 + "\n")
    test_fallback_logic_with_peak() 