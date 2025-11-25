"""Constants for the Sol integration."""

DOMAIN = "sol"
NAME = "Sol"

elevation_step = 0.5
azimuth_step = 1

DEBUG_ATTRIBUTES = False
# Debug flags for sensors
DEBUG_ELEVATION_SENSOR = False
DEBUG_AZIMUTH_SENSOR = False
# Add more as needed for other sensors

ELEVATION_TOLERANCE = 1  # seconds

# Azimuth search tolerance
AZIMUTH_DEGREE_TOLERANCE = 0.001 # degrees - azimuth precision tolerance

# Azimuth reversal detection
AZIMUTH_REVERSAL_SEARCH_MAX_ITERATIONS = 5000

# Azimuth ternary search iteration limit
AZIMUTH_TERNARY_SEARCH_MAX_ITERATIONS = 5000  # Maximum iterations for binary search refinement

# Azimuth checkpoint cache configuration
AZIMUTH_REVERSAL_CACHE_LENGTH = 4  # Number of checkpoints to cache (reversals + solar noons)

# Tropical latitude threshold (reversals only occur within ±23.44° of equator)
# Using 23.45° to account for potential ephem calculation tolerances
TROPICAL_LATITUDE_THRESHOLD = 23.45  # degrees
