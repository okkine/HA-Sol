# Sol - Sun Position & Seasonal Tracking for Home Assistant

Sol is a Home Assistant integration that tracks the sun's position, seasonal changes, and astronomical events with precision. 

---

## Overview

Sol gives you three sensors that work together to cover all your solar tracking needs. Want to know where the sun is right now? Track how seasons change? Know when it'll hit a specific angle? Sol's got you covered.

### What Makes Sol Different?

- **Smart Updates**: Sensors only update when something actually changes—no pointless polling
- **Atmospheric Compensation**: Adjusts for how the atmosphere bends sunlight based on your elevation and air pressure
- **Configurable Precision**: Set your own step sizes to balance accuracy against how often things update
- **Persistent Reversal Cache**: In tropical latitudes, the sun's compass direction can occasionally reverse at night during certain times of year—a phenomenon that's prevented other sun integrations from tracking azimuth in predictable steps. We solve this by pre-calculating and caching these reversals, letting us provide smooth, consistent azimuth updates year-round
- **Seasonal Awareness**: Track the sun's yearly journey with a normalized solstice curve

---

## Sensors

### Solar Elevation 

This tracks how high the sun sits above the horizon in degrees.

- **Range**: -90° (straight down) to +90° (directly overhead)
- **Updates**: Automatically whenever the sun crosses each elevation step
- **Default Step**: 0.5° (you can change this)

**Attributes:**
- `next_update` - When the sensor updates next
- `next_target` - The elevation that'll trigger the next update

**Example Use**: Fire off automations when the sun rises above the horizon (0°), hits golden hour elevation, or drops below a certain angle to close your blinds.

---

### Solar Azimuth

This tracks which compass direction the sun's in, measured in degrees.

- **Range**: 0° (North) → 90° (East) → 180° (South) → 270° (West) → 360° (North)
- **Updates**: Automatically when the sun reaches each azimuth step
- **Default Step**: 1.0° (configurable)

**Special Features:**
- Automatically detects and handles azimuth reversals in tropical areas (when the sun's compass direction reverses—usually happening at night when the sun is below the horizon)
- Persistent caching keeps track of up to 4 future reversals, even through reboots and power outages, enabling reliable step-based updates that other integrations can't provide
- Smart direction tracking stays accurate even when reversals happen right after midnight

**Attributes:**
- `next_update` - When the sensor updates next
- `next_target` - The azimuth that'll trigger the next update
- `reversal_time` - When the next azimuth reversal happens (tropical locations only)

**Example Use**: Know exactly where the sun is, automate window treatments when sun hits certain windows, or set up dynamic solar panel tracking. This is especially useful if you're within ±23.45° latitude where the sun can reverse direction.

---

### Declination Normalized

This tracks where we are in the annual seasonal cycle as a number between 0 and 1.

- **Range**: 0.0 (winter solstice) to 1.0 (summer solstice)
- **Updates**: Event-driven from the cache system (noon and midnight updates)
- **Hemisphere Aware**: Automatically adjusts based on whether you're north or south of the equator

**Attributes:**
- `declination` - Current solar declination in degrees

**How It Works:**
The declination normalized sensor shows where the sun is in its yearly trip between its lowest point (winter solstice) and highest point (summer solstice). This normalized value makes seasonal automations easier to set up.

- **0.0** = Winter solstice (shortest day, sun's lowest in the sky)
- **0.5** = Equinoxes (day and night are equal)
- **1.0** = Summer solstice (longest day, sun's highest in the sky)

**Example Use**: 
- Create automations that gradually change with the seasons
- Adjust heating/cooling based on how intense the sun is throughout the year
- Trigger different behaviors as seasons change without messing with complicated date logic
- Calculate optimal solar panel angles year-round

**Note**: This sensor's disabled by default—you can turn it on in entity settings if you want it.

---

## Atmospheric Compensation

Sol automatically accounts for the Earth's atmospheric refraction of sunlight, which makes the sun appear higher in the sky than its true astronomical position. This effect is most pronounced near the horizon, especially at sunrise and sunset.

### Apparent vs. True Position

**By default, Sol shows you the sun's *apparent position*** - where it looks like it is when you actually see it in the sky. This accounts for atmospheric refraction.

If you'd rather have the **true astronomical position** (the sun's geometric position without atmospheric effects), just set atmospheric pressure to `0` during setup. This turns off refraction correction completely.

**How It Works:**
- Uses your location's elevation to figure out typical atmospheric pressure
- Applies standard astronomical refraction models to calculate where the sun appears
- Fully configurable—set pressure to `0` for true position, or customize for your local conditions

**Default Settings:**
- Pressure: 1013.25 mbar (standard sea level pressure)
- Automatically adjusted based on your configured elevation
- **Result**: Shows apparent position (what you actually see)

**For True Astronomical Position:**
- Set pressure to `0` mbar during configuration
- **Result**: Shows geometric position (no atmospheric correction)

Most people should stick with the defaults—apparent position is more useful for real-world stuff like solar panel tracking, photography timing, and home automation.

---

## Azimuth Reversal Tracking

One of Sol's most sophisticated features is its handling of azimuth reversals in tropical and subtropical regions—a phenomenon that's challenged other sun tracking integrations.

### What Are Azimuth Reversals?

If you're within ±23.45° of the equator (the tropics), the sun doesn't always follow the typical east-to-west path across the sky. At certain times of year, after sunset (or before sunrise), the sun's azimuth can reverse direction—briefly moving backwards before continuing its normal path. These reversals usually happen at night when the sun's below the horizon.

### How Sol Handles This

**Persistent Cache System:**
- Keeps a rolling cache of the next 4 upcoming reversals
- Stores the cache in Home Assistant's persistent storage (survives reboots)
- Automatically recalculates starting from the previous solar noon for accuracy

**Intelligent Direction Detection:**
- Samples direction at solar noon (when no reversals happen)
- Tracks current direction by counting reversals since the last known state
- Updates cache automatically as reversals pass

**Daily/Weekly Validation:**
- Tropical locations (±23.45° latitude): Daily checks at midnight
- Non-tropical locations: Weekly validation checks
- Automatic adjustment as seasons change and the sun's declination varies

This keeps azimuth tracking accurate year-round, no matter your latitude, power outages, or system reboots.

---

## Configuration

### Initial Setup

1. Install Sol through HACS or manually
2. Go to **Settings** → **Devices & Services** → **Add Integration**
3. Search for "Sol" and select it
4. Choose your location (use Home Assistant's location or specify your own)
5. Optionally tweak elevation and atmospheric pressure settings

### Customizing Step Values

You can adjust how often the position sensors update:

- **Elevation Step**: How many degrees the sun needs to move vertically before updating (minimum 0.1)
  - Smaller values (like 0.1°) = more frequent updates, higher precision 
  - Larger values (like 2.0°) = fewer updates, less resource usage
  - Default: 0.5°

- **Azimuth Step**: How many degrees the sun needs to move horizontally before updating
  - Smaller values (like 0.1°) = more frequent updates, higher precision (might cause issues in tropical regions)
  - Larger values (like 5.0°) = fewer updates, less resource usage
  - Default: 1.0°

**Recommendation**: Start with the defaults. Only decrease step values if you need higher precision for specific automations. Smaller azimuth steps in tropical areas might result in crazy frequent updates during reversal periods.

### Optional Sensors

- **Declination Normalized**: Disabled by default—enable it in entity settings if you need seasonal tracking

---

## Technical Details

### Calculation Engine
- **Library**: PyEphem 4.1.4+ (industry-standard for astronomical calculations)
- **Precision**: Sub-degree accuracy for all position data
- **Time Handling**: All calculations respect your Home Assistant timezone

### Coordinate Systems
- **Azimuth**: 0° = North, increases clockwise (East = 90°, South = 180°, West = 270°)
- **Elevation**: 0° = horizon, 90° = straight up (zenith), -90° = straight down (nadir)

### Update Strategy
Sol uses event-driven updates instead of polling:
- Position sensors calculate when the next threshold will be hit
- Ternary search algorithm finds exact crossing times with sub-second accuracy
- Reversal cache system pre-calculates direction changes
- No wasted calculations or updates

This approach keeps CPU usage low while making sure your data's always current.

### Reversal Detection Algorithm

For tropical locations, Sol uses a two-phase approach:
1. **Linear Scan**: Checks every 5 minutes for direction changes
2. **Binary Search**: Refines reversal time to within 0.001° precision

Reversals get cached with timestamps and automatically maintained as they pass, so the azimuth sensor always knows which way the sun's moving.

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

This exposes additional sensor attributes that are useful for understanding what the integration's doing, including:
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
- **Contributing**: Pull requests welcome! Please read the contributing guidelines first.

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## Acknowledgments

- Built with [PyEphem](https://rhodesmill.org/pyephem/) for astronomical calculations
- Inspired by the Home Assistant community's need for accurate solar tracking
- Special thanks to all contributors and users who provide feedback and suggestions
