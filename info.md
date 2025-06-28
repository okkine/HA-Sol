# Sol Integration for Home Assistant

> **⚠️ This integration is currently in active development. It is not yet stable and breaking changes may occur. Use at your own risk!**

A comprehensive solar tracking integration for Home Assistant that provides detailed sun position data, elevation tracking, and seasonal solstice calculations.

## Features

- **Elevation Sensor**: Tracks sun elevation in configurable step intervals
- **Binary Elevation Sensors**: Trigger when sun reaches specific elevations
- **Seasonal Dynamic Elevations**: Automatically adjust elevation thresholds based on solstice curves
- **Solstice Curve Sensor**: Provides normalized solstice transition values (0-1)
- **Atmospheric Correction**: Accounts for pressure and temperature effects on refraction

## Installation

1. Install via HACS (recommended)
2. Add the integration to your configuration
3. Configure your sensors

## Requirements

- Home Assistant 2023.8.0 or later
- Python `ephem` library (automatically installed) 