"""Custom exceptions for the Sol integration."""

class SolError(Exception):
    """Base exception for Sol integration."""
    pass

class DateTimeError(SolError):
    """Exception raised for datetime-related errors."""
    pass

class TimezoneError(DateTimeError):
    """Exception raised for timezone-related errors."""
    pass

class SolarCalculationError(SolError):
    """Exception raised for errors in solar calculations."""
    pass

class DirectionError(SolarCalculationError):
    """Exception raised for errors in sun direction calculations."""
    pass

class ElevationError(SolarCalculationError):
    """Exception raised for errors in elevation calculations."""
    pass

class AzimuthError(SolarCalculationError):
    """Exception raised for errors in azimuth calculations."""
    pass

class SolsticeError(SolarCalculationError):
    """Exception raised for errors in solstice calculations."""
    pass 