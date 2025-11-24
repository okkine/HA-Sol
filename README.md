# Sol - Sun Position & Seasonal Tracking for Home Assistant

Sol is a comprehensive Home Assistant integration that provides precise tracking of the sun's position, seasonal progression, and related astronomical events. Built on the robust PyEphem library, it automatically compensates for atmospheric refraction and intelligently schedules updates to provide accurate data exactly when you need it.

---

## Overview

Sol gives you three specialized sensors that work together to provide complete solar tracking capabilities. Whether you're interested in the sun's current position in the sky, tracking seasonal changes, or when it will reach a specific elevation, Sol has you covered.

### What Makes Sol Different?

- **Smart Updates**: Sensors update only when meaningful changes occur, not on arbitrary schedules
- **Atmospheric Compensation**: Automatically adjusts for atmospheric refraction based on your location's elevation and pressure
- **Configurable Precision**: Choose your own step sizes for position tracking to balance accuracy with update frequency
- **Persistent Reversal Cache**: Advanced azimuth tracking with intelligent reversal detection that survives reboots
- **Seasonal Awareness**: Track the sun's annual cycle with a normalized solstice curve

---

## Sensors

### Solar Elevation 

Tracks how high the sun is above the horizon, measured in degrees.

- **Range**: -90° (directly below) to +90° (directly overhead)
- **Updates**: Automatically when the sun reaches each elevation step
- **Default Step**: 0.5° (configurable)

**Attributes:**
- `next_update` - When the sensor will next update
- `next_target` - The elevation value that will trigger the next update

**Example Use**: Trigger automations when the sun rises above your horizon (0°), reaches golden hour elevation, or sets below a specific angle for closing blinds.

---

### Solar Azimuth

Tracks the sun's compass direction, measured in degrees.

- **Range**: 0° (North) → 90° (East) → 180° (South) → 270° (West) → 360° (North)
- **Updates**: Automatically when the sun reaches each azimuth step
- **Default Step**: 1.0° (configurable)

**Special Features:**
- Automatically detects and handles azimuth reversals in tropical regions (when the sun changes direction)
- Persistent caching system maintains up to 4 future reversals, surviving reboots and power outages
- Intelligent direction tracking ensures accurate positioning even when reversals occur shortly after midnight

**Attributes:**
- `next_update` - When the sensor will next update
- `next_target` - The azimuth value that will trigger the next update
- `reversal_time` - When the next azimuth reversal will occur (tropical locations only)

**Example Use**: Know exactly where the sun is in the sky, trigger window treatments when sun hits specific windows, or create dynamic solar tracking for panels. Essential for locations within ±23.45° latitude where the sun can reverse direction.

---

### Solstice Curve

Tracks the sun's position in the annual seasonal cycle as a normalized value.

- **Range**: 0.0 (winter solstice) to 1.0 (summer solstice)
- **Updates**: Event-driven from cache system (noon and midnight updates)
- **Hemisphere Aware**: Automatically adjusts for northern/southern hemisphere

**Attributes:**
- `declination` - Current solar declination in degrees

**How It Works:**
The solstice curve represents where the sun is in its annual journey between minimum elevation (winter solstice) and maximum elevation (summer solstice). This normalized value makes it easy to create seasonal automations.

- **0.0** = Winter solstice (shortest day, lowest sun elevation)
- **0.5** = Equinoxes (equal day/night)
- **1.0** = Summer solstice (longest day, highest sun elevation)

**Example Use**: 
- Create gradually changing automations that follow the seasons
- Adjust heating/cooling schedules based on seasonal sun intensity
- Trigger different behaviors as the year progresses without complex date logic
- Calculate optimal solar panel angles throughout the year

**Note**: This sensor is disabled by default and can be enabled in the entity settings if needed.

---

## Atmospheric Compensation

Sol automatically accounts for how Earth's atmosphere bends light from the sun, making it appear higher in the sky than its true astronomical position. This effect is most noticeable near the horizon, especially at sunrise and sunset.

### Apparent vs. True Position

**By default, Sol shows the sun's *apparent position*** - where it appears to be in the sky when you look at it. This is what you'll actually see with your eyes, accounting for atmospheric refraction.

If you prefer the **true astronomical position** (the sun's geometric position without atmospheric effects), you can set the atmospheric pressure to `0` during configuration. This disables refraction correction entirely.

**How It Works:**
- Uses your location's elevation to calculate typical atmospheric pressure
- Applies standard astronomical refraction models to calculate apparent position
- Fully configurable - set pressure to `0` for true position, or customize for your local conditions

**Default Settings:**
- Pressure: 1013.25 mbar (standard sea level pressure)
- Automatically adjusted based on your configured elevation
- **Result**: Shows apparent position (what you see in the sky)

**For True Astronomical Position:**
- Set pressure to `0` mbar during configuration
- **Result**: Shows geometric position (no atmospheric correction)

Most users should use the default settings, as the apparent position is more useful for practical applications like solar panel tracking, photography timing, and home automation.

---

## Azimuth Reversal Tracking

One of Sol's most sophisticated features is its handling of azimuth reversals in tropical and subtropical regions.

### What Are Azimuth Reversals?

At latitudes within ±23.45° of the equator (the tropics), the sun doesn't always follow the typical east-to-west pattern across the sky. During certain times of the year, after the sun sets (or before it rises), its azimuth can reverse direction - briefly moving in the opposite direction, before continuing its normal path. These reversals typically occur at night when the sun is below the horizon.

### How Sol Handles This

**Persistent Cache System:**
- Maintains a rolling cache of the next 4 upcoming reversals
- Stores cache in Home Assistant's persistent storage (survives reboots)
- Automatically recalculates starting from previous solar noon for accuracy

**Intelligent Direction Detection:**
- Samples direction at solar noon (when no reversals occur)
- Tracks current direction by counting reversals since last known state
- Updates cache automatically when reversals pass

**Daily/Weekly Validation:**
- Tropical locations (±23.45° latitude): Daily checks at midnight
- Non-tropical locations: Weekly validation checks
- Automatic adjustment as seasons change and sun's declination varies

This ensures accurate azimuth tracking year-round, regardless of latitude, power outages, or system reboots.

---

## Configuration

### Initial Setup

1. Install Sol through HACS or manually
2. Go to **Settings** → **Devices & Services** → **Add Integration**
3. Search for "Sol" and select it
4. Choose your location (use Home Assistant's location or specify a custom one)
5. Optionally adjust elevation and atmospheric pressure settings

### Customizing Step Values

You can adjust how frequently the position sensors update:

- **Elevation Step**: How many degrees the sun must move vertically before updating (minimum 0.1)
  - Smaller values (e.g., 0.1°) = more frequent updates, higher precision 
  - Larger values (e.g., 2.0°) = fewer updates, lower resource usage
  - Default: 0.5°

- **Azimuth Step**: How many degrees the sun must move horizontally before updating
  - Smaller values (e.g., 0.1°) = more frequent updates, higher precision (May cause problems in tropical regions)
  - Larger values (e.g., 5.0°) = fewer updates, lower resource usage
  - Default: 1.0°

**Recommendation**: Start with the defaults. Only decrease step values if you need higher precision for specific automations. Smaller azimuth steps in tropical regions may result in very frequent updates during reversal periods.

### Optional Sensors

- **Solstice Curve**: Disabled by default, can be enabled in entity settings if you need seasonal tracking

---

## Technical Details

### Calculation Engine
- **Library**: PyEphem 4.1.4+ (industry-standard astronomical calculations)
- **Precision**: Sub-degree accuracy for all position data
- **Time Handling**: All calculations respect your Home Assistant timezone

### Coordinate Systems
- **Azimuth**: 0° = North, increasing clockwise (East = 90°, South = 180°, West = 270°)
- **Elevation**: 0° = horizon, 90° = directly overhead (zenith), -90° = directly below (nadir)

### Update Strategy
Sol uses event-driven updates rather than polling:
- Position sensors calculate when the next threshold will be reached
- Ternary search algorithm finds exact crossing times with sub-second accuracy
- Reversal cache system pre-calculates direction changes
- No unnecessary calculations or updates

This approach minimizes CPU usage while ensuring data is always current.

### Reversal Detection Algorithm

For tropical locations, Sol uses a sophisticated two-phase approach:
1. **Linear Scan**: Checks every 5 minutes for direction changes
2. **Binary Search**: Refines reversal time to within 0.001° precision

Reversals are cached with timestamps and automatically maintained as they pass, ensuring the azimuth sensor always knows which direction the sun is moving.

---

## Installation

### Via HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots in the top right and select "Custom repositories"
3. Add `https://github.com/Okkine/HA-Sol` as an Integration
4. Click "Install"
5. Restart Home Assistant
6. Add the Sol integration through the UI

### Manual Installation

1. Download the latest release from GitHub
2. Copy the `custom_components/sol` folder to your Home Assistant `custom_components` directory
3. Restart Home Assistant
4. Add the Sol integration through the UI

---

## Requirements

- **Home Assistant**: 2023.8.0 or later
- **Python Packages**: Automatically installed
  - `ephem` >= 4.1.4
  - `python-slugify`

---

## Advanced Configuration

### Debug Mode

For development or troubleshooting, you can enable detailed debug attributes by editing `const.py`:

```python
DEBUG_ATTRIBUTES = True
```

This exposes additional sensor attributes useful for understanding the integration's behavior, including:
- Reversal cache details and future reversal times
- Search performance metrics
- Solar event cache information
- Detailed position data

### Checkpoint Cache Settings

You can customize the checkpoint cache behavior in `const.py`:

```python
AZIMUTH_REVERSAL_CACHE_LENGTH = 4  # Number of checkpoints to cache (reversals + solar noons)
TROPICAL_LATITUDE_THRESHOLD = 23.45  # Latitude threshold for tropical behavior
```

---

## Support & Contributing

- **Issues**: Report bugs or request features on [GitHub Issues](https://github.com/Okkine/HA-Sol/issues)
- **Discussions**: Ask questions or share ideas on [GitHub Discussions](https://github.com/Okkine/HA-Sol/discussions)
- **Contributing**: Pull requests are welcome! Please read the contributing guidelines first.

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## Acknowledgments

- Built with [PyEphem](https://rhodesmill.org/pyephem/) for astronomical calculations
- Inspired by the Home Assistant community's need for accurate solar tracking
- Special thanks to all contributors and users who provide feedback and suggestions
