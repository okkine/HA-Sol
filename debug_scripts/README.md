# Debug Scripts

This folder contains debug and testing scripts for the Sol integration.

## Scripts

### `check_version.py`
Simple script to verify that the integration is using the latest code.
```bash
python3 check_version.py
```

### `debug_azimuth.py`
Tests the azimuth validation logic, especially for 360° values.
```bash
python3 debug_azimuth.py
```

### `debug_elevation_fallback.py`
Tests the improved fallback logic for elevation sensors.
```bash
python3 debug_elevation_fallback.py
```

### `debug_peak_elevation.py`
Tests the peak elevation time calculation using `next_pass()`.
```bash
python3 debug_peak_elevation.py
```

### `debug_binary_sensor.py`
Tests binary sensor functionality.
```bash
python3 debug_binary_sensor.py
```

### `debug_elevation.py`
General elevation sensor testing.
```bash
python3 debug_elevation.py
```

## Usage

Run these scripts from the debug directory to test various aspects of the Sol integration.
These scripts are for development and testing purposes only. 