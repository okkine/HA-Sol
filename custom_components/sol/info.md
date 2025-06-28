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

1. Add this repository as a custom repository in HACS
2. Search for "Sol" in the HACS store
3. Click "Download"
4. Restart Home Assistant
5. Add the integration to your configuration

## Configuration

Add the following to your `configuration.yaml`:

```yaml
# Example configuration.yaml entry
sensor:
  - platform: sol
    elevation_step: 5.0  # Optional: Create elevation step sensor
    solstice_curve: true  # Optional: Create solstice curve sensor
    pressure: 1013.25     # Optional: Atmospheric pressure in mbar
    temperature: 15.0     # Optional: Temperature in Celsius

binary_sensor:
  - platform: sol
    elevation_threshold: 10.0  # Elevation threshold in degrees
    # OR use dynamic thresholds:
    # rising_threshold: 15.0
    # setting_threshold: 5.0
```

## Support

- [GitHub Issues](https://github.com/okkine/HA-sol/issues)
- [Community Forum](https://community.home-assistant.io/)

## Maintainer

[@okkine](https://github.com/okkine) 