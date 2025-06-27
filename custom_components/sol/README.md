# Sol Integration

A comprehensive solar tracking integration for Home Assistant that provides detailed sun position data, elevation tracking, and seasonal solstice calculations.

## Features

- **Elevation Sensor**: Tracks sun elevation in configurable step intervals
- **Binary Elevation Sensors**: Trigger when sun reaches specific elevations
- **Seasonal Dynamic Elevations**: Automatically adjust elevation thresholds based on solstice curves
- **Solstice Curve Sensor**: Provides normalized solstice transition values (0-1)
- **Atmospheric Correction**: Accounts for pressure and temperature effects on refraction

## Configuration

### Basic Configuration

```yaml
# configuration.yaml
sol:
  pressure: 1010.0  # Atmospheric pressure in mbar (optional)
  temperature: 25.0  # Temperature in °C (optional)
  elevation_step: 5.0  # Elevation step size in degrees (optional)
  solstice_curve: true  # Enable solstice curve sensor (optional)
  
  # Binary elevation sensors
  binary_elevation_sensor:
    - name: "Sun Above 30 Degrees"
      rising_elevation: 30.0
      setting_elevation: 30.0
      
    - name: "Seasonal Sun Tracking"
      seasonally_dynamic: true
      summer_rising_elevation: 45.0
      summer_setting_elevation: 45.0
      winter_rising_elevation: 20.0
      winter_setting_elevation: 20.0
```

### Configuration Options

#### Global Options
- **pressure** (optional): Atmospheric pressure in mbar (800-1200, default: 1010.0)
- **temperature** (optional): Temperature in °C (-50 to 60, default: 25.0)
- **elevation_step** (optional): Step size for elevation sensor in degrees (0.1-90)
- **solstice_curve** (optional): Enable solstice curve sensor (default: false)

#### Binary Elevation Sensor Options
- **name** (required): Name of the sensor
- **seasonally_dynamic** (optional): Enable seasonal elevation adjustment (default: false)
- **rising_elevation** (optional): Elevation threshold for rising sun
- **setting_elevation** (optional): Elevation threshold for setting sun
- **summer_rising_elevation** (required if seasonally_dynamic): Summer rising elevation
- **summer_setting_elevation** (required if seasonally_dynamic): Summer setting elevation
- **winter_rising_elevation** (required if seasonally_dynamic): Winter rising elevation
- **winter_setting_elevation** (required if seasonally_dynamic): Winter setting elevation

## Sensors

### Elevation Sensor
- **Entity ID**: `sensor.sol_elevation`
- **State**: Current sun elevation in degrees
- **Attributes**:
  - `next_change`: Time of next elevation change
  - `direction`: Current sun direction (rising/setting)
  - `target_elevation`: Next target elevation

### Solstice Curve Sensor
- **Entity ID**: `sensor.sol_solstice_curve`
- **State**: Normalized solstice transition value (0-1)
- **Attributes**:
  - `previous_solstice`: Previous solstice date
  - `next_solstice`: Next solstice date
  - `calculation_time`: Time of last calculation

### Binary Elevation Sensors
- **Entity ID**: `binary_sensor.{name}`
- **State**: `on` when sun is above threshold, `off` when below
- **Attributes**:
  - `rising`: Rising elevation threshold time (today's or next)
  - `setting`: Setting elevation threshold time (today's or next)
  - `next_change`: Time of next state change
  - `current_rising_elevation`: Current dynamic rising elevation
  - `current_setting_elevation`: Current dynamic setting elevation
  - `solstice_curve`: Current solstice curve value
  - `seasonally_dynamic`: Whether seasonal adjustment is enabled
  - `sun_direction`: Current sun direction
  - `next_event_type`: Type of next event (rising/setting)

## Examples

### Basic Elevation Tracking
```yaml
sol:
  elevation_step: 10.0
```

### Seasonal Sun Tracking
```yaml
sol:
  binary_elevation_sensor:
    - name: "Seasonal Sun High"
      seasonally_dynamic: true
      summer_rising_elevation: 60.0
      summer_setting_elevation: 60.0
      winter_rising_elevation: 30.0
      winter_setting_elevation: 30.0
```

### Multiple Elevation Thresholds
```yaml
sol:
  binary_elevation_sensor:
    - name: "Sun Above Horizon"
      rising_elevation: 0.0
      setting_elevation: 0.0
    - name: "Sun Above 30 Degrees"
      rising_elevation: 30.0
      setting_elevation: 30.0
    - name: "Sun Above 60 Degrees"
      rising_elevation: 60.0
      setting_elevation: 60.0
```

## Technical Details

### Atmospheric Correction
The integration uses the `ephem` library to calculate accurate sun positions with atmospheric refraction correction based on:
- Atmospheric pressure (affects refraction)
- Temperature (affects air density)

### Solstice Curve Calculation
The solstice curve provides a normalized value (0-1) representing the transition between summer and winter solstices:
- 0.0 = Winter solstice
- 0.5 = Spring/fall equinox
- 1.0 = Summer solstice

### State Behavior
Binary sensors use direction-dependent state logic:
- **During rising phase**: ON when sun elevation ≥ rising threshold
- **During setting phase**: ON when sun elevation ≥ setting threshold

### Update Frequency
- Elevation sensors update at each step change
- Binary sensors update at state changes with responsive scheduling
- Solstice curve updates at local noon and midnight

## Troubleshooting

### Common Issues

1. **Import errors**: Ensure `ephem` is installed
2. **Incorrect elevations**: Check your latitude/longitude configuration
3. **Missing sensors**: Verify configuration syntax and restart Home Assistant

### Debug Logging
Enable debug logging to troubleshoot issues:
```yaml
logger:
  default: info
  logs:
    custom_components.sol: debug
```

## Requirements

- Home Assistant 2023.8.0 or later
- Python `ephem` library

## License

This integration is provided as-is for educational and personal use. 