"""Constants for the Sol integration."""

from skyfield.api import load

DOMAIN = "sol"
NAME = "Sol"

# Load ephemeris and timescale (loaded once at module import to avoid blocking I/O)
# Note: This will block on first import, but subsequent imports reuse the cached file
eph = load('de421.bsp')
ts = load.timescale()


# Ephemeris cache configuration
CACHED_TRANSIT_ANTITRANSIT_COUNT = 2  # Number of future transit/antitransit checkpoints to maintain
REVERSAL_SCAN_BUFFER = 0.1  # Buffer in degrees (used by is_within_reversal_range; kept for future use)
PARALLACTIC_EXTREMUM_TOLERANCE = 0.1  # Dimensionless trig threshold (sign_val in [-1,+1]) for detecting the parallactic angle extremum; distinct from REVERSAL_SCAN_BUFFER which is in degrees

# Azimuth sensor step configuration
AZIMUTH_STEP_VALUE_DEFAULT = 1.0  # Default step value for azimuth updates (degrees). Will be configurable in config flow later.

# Elevation sensor step configuration
ELEVATION_STEP_VALUE_DEFAULT = 0.5  # Default step value for elevation updates (degrees). Will be configurable in config flow later.

# Threshold for scheduling updates at checkpoint events (seconds)
TRANSIT_THRESHOLD = 10  # Seconds before transit/antitransit to schedule update
REVERSAL_THRESHOLD = 10  # Seconds before reversal to schedule update

# find_discrete epsilon configuration (in days)
# Default epsilon is ~1.1574e-08 days (1 millisecond)
# We use 1 second for better performance: 1 second = 1/86400 days ≈ 1.1574074074074074e-05 days
AZIMUTH_TOLERANCE_BASE = 0.9 / 86400.0  # Base azimuth epsilon (~0.9 seconds)
AZIMUTH_TOLERANCE_MIN = 0.005/ 86400.0  # Minimum azimuth epsilon for adaptive tightening
ELEVATION_TOLERANCE = 1.0 / 86400.0  # ~1.1574e-05 days = 1 second (1000x default)

# Search window configuration (hours)
AZIMUTH_SEARCH_WINDOW_HOURS = 1.0  # Hours for iterative azimuth searches
ELEVATION_SEARCH_WINDOW_HOURS = 1.0  # Hours for iterative elevation searches
STEP_CANDIDATE_CACHE_REFILL_THRESHOLD = 6  # Refill step cache when count is <= this value
STEP_CANDIDATE_MINIMUM_COUNT = 12  # Segmented finder keeps adding non-overlapping windows until at least this many step crossings (or caps)
STEP_CANDIDATE_MAX_SEARCH_SPAN_HOURS = 24.0  # Hard stop for segmented step search from search_start
STEP_CANDIDATE_MIN_GAP_SECONDS = 10  # Minimum gap between cached step events; used for stale guard and cache pre-purge
AZIMUTH_TRANSIT_FOCUS_WINDOW_MINUTES = 12  # Focus window around transit/antitransit for adaptive azimuth refinement
AZIMUTH_TRANSIT_RATE_THRESHOLD_DEG_PER_SEC = 0.04  # Trigger adaptive azimuth refinement when local rate exceeds this threshold
AZIMUTH_SINGULARITY_RATE_THRESHOLD_DEG_PER_SEC = 0.75  # Skip scanning around transit/antitransit when local azimuth rate reaches this threshold
AZIMUTH_SINGULARITY_GUARD_WINDOW_SECONDS = 5  # Blackout half-window around transit/antitransit singularity risk periods

# Debug attributes for sensor extra_state_attributes.
# When False, debug-only attributes are not calculated or exposed.
DEBUG_ATTRIBUTES = False

# Ephemeris cache schema version.
# Bump this when persisted cache structure/semantics change.
EPHEMERIS_CACHE_VERSION = 1

# Lunar Phase angle tolerance for find_discrete (~0.9 seconds precision)
PHASE_ANGLE_TOLERANCE = 0.9 / 86400.0