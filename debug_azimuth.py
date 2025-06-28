#!/usr/bin/env python3
"""
Debug script to test the azimuth validation fix.
This script tests various azimuth values to ensure 360° is properly handled.
"""

import sys
import os
from datetime import datetime, timezone, timedelta
import math

# Add the current directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helper import SunHelper

def test_azimuth_validation():
    """Test the azimuth validation logic with various values."""
    
    print("=== Testing Azimuth Validation ===\n")
    
    # Example coordinates
    latitude = 40.7128  # New York
    longitude = -74.0060
    elevation = 10.0
    pressure = 1013.25
    temperature = 15.0
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Test times throughout the day to catch different azimuth values
    test_times = [
        datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0),   # Morning
        datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0),   # Mid-morning
        datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0),  # Noon
        datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0),  # Afternoon
        datetime.now(timezone.utc).replace(hour=18, minute=0, second=0, microsecond=0),  # Evening
        datetime.now(timezone.utc).replace(hour=21, minute=0, second=0, microsecond=0),  # Night
    ]
    
    for test_time in test_times:
        print(f"Testing at: {test_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        try:
            # Get current elevation and azimuth
            current_elev, current_azimuth = sun_helper.calculate_position(test_time)
            print(f"  Elevation: {current_elev:.2f}°")
            print(f"  Azimuth: {current_azimuth:.2f}°")
            
            # Check if azimuth is in valid range
            if 0 <= current_azimuth <= 360:
                print(f"  ✓ Azimuth is in valid range [0, 360]")
            else:
                print(f"  ✗ Azimuth is outside valid range!")
                
        except Exception as e:
            print(f"  Error: {e}")
        
        print()

def test_edge_cases():
    """Test edge cases for azimuth values."""
    
    print("=== Testing Azimuth Edge Cases ===\n")
    
    # Example coordinates
    latitude = 40.7128
    longitude = -74.0060
    elevation = 10.0
    pressure = 1013.25
    temperature = 15.0
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Test specific times that might produce edge case azimuths
    # These times are chosen to potentially produce azimuths near 0°/360°
    test_times = [
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0),   # Midnight
        datetime.now(timezone.utc).replace(hour=23, minute=59, second=0, microsecond=0), # Just before midnight
        datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0),  # Solar noon
    ]
    
    for test_time in test_times:
        print(f"Testing edge case at: {test_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        try:
            # Get current elevation and azimuth
            current_elev, current_azimuth = sun_helper.calculate_position(test_time)
            print(f"  Elevation: {current_elev:.2f}°")
            print(f"  Azimuth: {current_azimuth:.2f}°")
            
            # Check for specific edge cases
            if abs(current_azimuth - 360.0) < 0.01:
                print(f"  ⚠️  Azimuth is very close to 360° - should be normalized to 0°")
            elif abs(current_azimuth - 0.0) < 0.01:
                print(f"  ✓ Azimuth is near 0° (correct)")
            elif current_azimuth < 0:
                print(f"  ✗ Azimuth is negative: {current_azimuth:.2f}°")
            elif current_azimuth > 360:
                print(f"  ✗ Azimuth is greater than 360°: {current_azimuth:.2f}°")
            else:
                print(f"  ✓ Azimuth is in normal range")
                
        except Exception as e:
            print(f"  Error: {e}")
        
        print()

def test_manual_azimuth_values():
    """Test the validation logic with manually set azimuth values."""
    
    print("=== Testing Manual Azimuth Values ===\n")
    
    # Example coordinates
    latitude = 40.7128
    longitude = -74.0060
    elevation = 10.0
    pressure = 1013.25
    temperature = 15.0
    
    sun_helper = SunHelper(latitude, longitude, elevation, pressure, temperature)
    
    # Test various azimuth values that might cause issues
    test_azimuths = [
        0.0,      # North
        90.0,     # East
        180.0,    # South
        270.0,    # West
        359.9,    # Just before North
        360.0,    # North (should be normalized to 0°)
        360.1,    # Just after North (should be normalized)
        -1.0,     # Negative (should be normalized)
        361.0,    # Greater than 360° (should be normalized)
    ]
    
    for test_azimuth in test_azimuths:
        print(f"Testing azimuth: {test_azimuth:.1f}°")
        
        # Simulate the validation logic
        azimuth = test_azimuth
        
        if azimuth < 0:
            print(f"  ⚠️  Negative azimuth detected, normalizing...")
            azimuth = azimuth % 360
        elif azimuth > 360:
            print(f"  ⚠️  Azimuth > 360° detected, normalizing...")
            azimuth = azimuth % 360
        elif azimuth == 360:
            print(f"  ✓ Azimuth is exactly 360°, normalizing to 0°")
            azimuth = 0.0
        else:
            print(f"  ✓ Azimuth is in valid range")
        
        print(f"  Final azimuth: {azimuth:.2f}°")
        print()

if __name__ == "__main__":
    test_azimuth_validation()
    print("\n" + "="*50 + "\n")
    test_edge_cases()
    print("\n" + "="*50 + "\n")
    test_manual_azimuth_values() 