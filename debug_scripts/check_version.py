#!/usr/bin/env python3
"""
Simple script to check the TEST_VERSION from const.py
Run this to verify that the Sol integration is using the latest code.
"""

import sys
import os

# Add the parent directory to the path to find the const module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from const import TEST_VERSION
    print(f"✓ Custom components const.py TEST_VERSION: {TEST_VERSION}")
    print("\nIf you see the version '2024-01-15-v2-fixed-azimuth-and-elevation-fallback',")
    print("then the integration is using the latest code with the fixes for:")
    print("- Azimuth 360° validation")
    print("- Elevation sensor fallback logic")
    print("- Peak elevation time calculation")
except ImportError as e:
    print(f"✗ Could not import from const.py: {e}")
    print("Make sure you're running this from the debug directory.") 