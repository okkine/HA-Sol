> [!WARNING]
> ## Breaking Change (PyEphem → Skyfield Rewrite)
> This release is a full rewrite of the previous `Sol` integration.
>
> - The old implementation used **PyEphem** with fewer features.
> - The new implementation uses **Skyfield** and includes expanded functionality.
> - Entity IDs/sensor names have changed (e.g. `solar_azimuth` → `sun_azimuth`).
>
> The PyEphem version had known stability issues and could occasionally crash Home Assistant in edge-case sun/observer geometry. This rewrite removes that dependency entirely.

> [!NOTE]
> ## Pre-release
> This version is a **pre-release** for testing and feedback, not a final stable release. Expect possible changes before an official release. Back up your Home Assistant configuration before upgrading, and review automations and dashboards that reference Sol entity IDs.

# HA-Sol

*Humans have tracked the movements of the sun, moon, and planets since before written history — using them to mark the seasons, navigate oceans, plan harvests, and build calendars that still echo in the ones we use today. Sol — Latin for "sun", named for the Roman solar deity, and a nod to Sól, the Norse goddess who carries the sun across the sky — brings that same obsession to Home Assistant, with rather better tooling.*

HA-Sol is a high-precision celestial mechanics integration for Home Assistant. It tracks the exact position of the Sun, Moon, and planets relative to your location — using NASA JPL ephemeris data and the Python `skyfield` library to do all the math locally, with no cloud dependency and no API calls.

It's heavier than a basic sun-tracking integration, but it earns the weight. Whether you want to automate your blinds based on the exact angle of sunlight hitting a window, track the moon's parallactic angle for a dashboard widget, or just know precisely when Saturn rises — Sol has the data.

## Performance / Resource Considerations

Compute load scales with the number of configured locations and enabled bodies.

Sol uses Skyfield for high-precision ephemeris calculations. Skyfield is typically more CPU-intensive than Astral-based sun/moon trackers, though this integration has been optimized to reduce average load and avoid unnecessary updates.

During reversal-heavy periods (most commonly at tropical latitudes), short bursts of additional computation can occur.

Most installations with one or two locations and a small number of enabled bodies should run comfortably, but users on older or lower-power hardware should monitor system load when enabling many locations and bodies.

## Features

- **Fully local and offline.** All calculations run on your Home Assistant instance. No external services, no rate limits, no outages.
- **NASA JPL DE421 ephemeris.** The same data used for spacecraft navigation, now powering your automations.
- **Atmospheric refraction.** Elevation calculations account for your actual elevation, temperature, and pressure — not just a spherical cow in a vacuum.
- **Step-based updates.** Azimuth and Elevation sensors fire when the body moves by a configurable amount (down to 0.1°), not on an arbitrary timer. Your automations trigger exactly when the position changes, not a minute before or after.
- **Multi-location.** Add as many locations as you want — your house, your cabin, anywhere on Earth.
- **Multi-body.** Sun, Moon, Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune, and Pluto.

## Sensors

### Azimuth & Elevation
The core of what Sol does. Azimuth is the compass bearing (0° North, 90° East, 180° South, 270° West), elevation is the angle above or below the horizon. Both update on configurable degree steps rather than time intervals, so your automations can trigger on the exact moment the sun clears a roofline or drops below a hillside.

### Rise & Set
Today's rise and set times, with tomorrow's times and the azimuth of each event in the attributes. For the Sun and Moon, Sol accounts for the physical disk (limb, not centre) when calculating horizon crossings. Updates at midnight.

### Transit & Antitransit
Transit is when the body reaches its highest point of the day (meridian crossing); antitransit is when it's at its lowest below the horizon. Attributes include the maximum or minimum elevation reached.

### Moon Phase
The current named phase — New Moon, Waxing Crescent, First Quarter, and so on — updating at each phase boundary rather than on a schedule. Attributes include the time of the next phase change and the traditional name of the next full moon (Wolf Moon, Harvest Moon, etc.), supporting both North American and Pagan naming conventions.

### Moon Phase Angle
The exact illumination angle (0–359°), updating every degree. Useful if you want more precision than the phase name gives you.

### Moon Parallactic Angle
The rotation of the moon's disk relative to your horizon — the angle you'd need to rotate a moon image in your Lovelace dashboard to match what's actually in the sky. Updates every degree, with faster polling near the zenith where the angle changes quickly.

### Solar Declination Normalized *(Sun only)*
A single value from `-1.0` to `+1.0` representing where the sun currently sits in its seasonal cycle: `-1.0` at the December solstice, `0.0` at the equinoxes, `+1.0` at the June solstice. Useful for automations that need to scale based on how deep into summer or winter you actually are, regardless of hemisphere. Updates twice daily.

## Installation

### HACS (Recommended)
1. Open HACS → Integrations → Custom repositories.
2. Add this repository URL and select "Integration".
3. Click "Download" on the HA-Sol integration.
4. Restart Home Assistant.

### Manual
1. Download the `custom_components/sol` directory from this repository.
2. Copy it into your Home Assistant `custom_components` directory.
3. Restart Home Assistant.

## Configuration

Go to **Settings → Devices & Services → Add Integration** and search for "Sol".

You'll be prompted for:
- **Location Name** — a friendly label for the location.
- **Latitude / Longitude** — defaults to your Home Assistant location.
- **Elevation** — metres above sea level, used for atmospheric pressure estimation and refraction.
- **Temperature Mode** — how Sol handles temperature for refraction (estimate, manual, or from a sensor entity).
- **Bodies to Track** — pick whichever you want.

Once added, click "Configure" to choose which sensors to enable per body and tune the azimuth/elevation step values.

## Advanced: Debug Attributes

Set `DEBUG_ATTRIBUTES = True` in `custom_components/sol/const.py` to expose internal calculation data on sensor attributes — atmospheric pressure used, search iteration counts, cache state, declination, elongation, and more. Useful for diagnosing unexpected behaviour or just satisfying curiosity.

## Credits

Powered by the excellent [Skyfield](https://rhodesmill.org/skyfield/) library.