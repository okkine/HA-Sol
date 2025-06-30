# Sol Integration for Home Assistant

This is a custom integration for Home Assistant that provides integration with Sol devices/services.

## Features

- Configurable through the Home Assistant UI
- Sensor platform for monitoring Sol status
- Easy setup and configuration

## Installation

1. Copy the `sol` folder to your `custom_components` directory in your Home Assistant configuration
2. Restart Home Assistant
3. Go to **Settings** > **Devices & Services** > **Integrations**
4. Click the **+ ADD INTEGRATION** button
5. Search for "Sol" and select it
6. Follow the configuration wizard

## Configuration

The integration requires the following configuration:

- **Name**: A friendly name for your Sol integration
- **Host**: The hostname or IP address of your Sol device/service
- **Port**: The port number for your Sol device/service (default: 8080)

## Entities

### Sensors

- **Status**: Shows the current status of your Sol device/service

## Development

This integration is structured following Home Assistant best practices:

- `__init__.py`: Main integration setup
- `const.py`: Constants and configuration keys
- `manifest.json`: Integration metadata
- `config_flow.py`: Configuration flow for UI setup
- `strings.json`: UI strings and translations
- `translations/`: Internationalization support
- `sensor.py`: Sensor platform implementation

## Customization

To customize this integration for your specific Sol device or service:

1. Modify the `config_flow.py` to add proper connection validation
2. Update the sensor implementation in `sensor.py` to fetch real data
3. Add additional platforms (switches, lights, etc.) as needed
4. Update the `manifest.json` with your specific requirements

## Support

For issues and feature requests, please create an issue in the repository. 