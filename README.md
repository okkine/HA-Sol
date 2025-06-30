# HA-Sol - Sun Position Integration for Home Assistant

> ⚠️ **DEVELOPMENT WARNING**: This integration is currently under active development and is not yet ready for production use. Features may be incomplete, and the API may change. Use at your own risk.

A custom Home Assistant integration for accurate sun position calculations using astronomical algorithms.

## Features

- **Accurate Sun Position**: Calculate azimuth and elevation using ephem library
- **Automatic Pressure Calculation**: Uses pyatmos to calculate atmospheric pressure from elevation
- **User Configurable**: Customize location, elevation, temperature, and atmospheric conditions
- **Multiple Sensors**: Extensible sensor platform for various sun-related measurements
- **Consistent Naming**: Automatic naming conventions for all sensors and input entities

## Installation

### Method 1: HACS (Recommended)
1. Add this repository to HACS as a custom repository
2. Search for "Sol" in the HACS integrations
3. Click "Download" and restart Home Assistant
4. Go to **Settings** > **Devices & Services** > **Integrations**
5. Click **+ ADD INTEGRATION** and search for "Sol"

### Method 2: Manual Installation
1. Download this repository
2. Copy the `custom_components/sol` folder to your Home Assistant `custom_components` directory
3. Restart Home Assistant
4. Go to **Settings** > **Devices & Services** > **Integrations**
5. Click **+ ADD INTEGRATION** and search for "Sol"

## Configuration

The integration requires minimal configuration:

- **Latitude/Longitude**: Optional - uses Home Assistant system defaults if not provided
- **Elevation**: Your location's elevation in meters (used for pressure calculation)
- **Pressure Mode**: 
  - **Auto**: Automatically calculated from elevation (recommended)
  - **Manual**: Set your own pressure value
- **Temperature**: Local temperature in Celsius (affects atmospheric calculations)
- **Horizon**: Horizon offset in degrees (default: 0°)

## Sensors

### Current Sensors
- **Sol - Status**: Basic status sensor (template)
- **Sol - Elevation**: Current sun elevation in degrees

### Future Sensors (Planned)
- Sun azimuth angle
- Sunrise/sunset times
- Solar noon
- Daylight hours
- Solar power calculations

## Dependencies

- **ephem**: Astronomical calculations
- **pyatmos**: Atmospheric pressure calculations
- **python-slugify**: Clean entity naming

## Development

This integration is built with:
- **Consistent naming conventions** for all entities
- **Modular design** with shared calculation utilities
- **User-friendly configuration** with sensible defaults
- **Robust error handling** with fallback calculations

## Support

For issues and feature requests, please create an issue in the [GitHub repository](https://github.com/okkine/HA-Sol).

## License

This project is licensed under the MIT License. 