# const.py
"""Constants for the Sol integration."""

DOMAIN = "sol"
NAME = "Sol"

# Update version to reflect sensor update fix
TEST_VERSION = "2025-06-29 14:41 - Fixed get_time_at_elevation to not advance to next day when max_days=0"

CONF_BINARY_ELEVATION_SENSOR = "binary_elevation_sensor"
CONF_RISING_ELEVATION = "rising_elevation"
CONF_SETTING_ELEVATION = "setting_elevation"
CONF_SEASONALLY_DYNAMIC = "seasonally_dynamic"
CONF_SUMMER_RISING_ELEVATION = "summer_rising_elevation"
CONF_SUMMER_SETTING_ELEVATION = "summer_setting_elevation"
CONF_WINTER_RISING_ELEVATION = "winter_rising_elevation"
CONF_WINTER_SETTING_ELEVATION = "winter_setting_elevation"
CONF_PRESSURE = "pressure"
CONF_TEMPERATURE = "temperature"
DEFAULT_PRESSURE = 1010.0  # hPa
DEFAULT_TEMPERATURE = 25.0  # °C
