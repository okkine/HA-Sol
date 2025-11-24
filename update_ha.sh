#!/bin/bash
# Script to copy Sol integration files to Home Assistant

echo "Copying Sol integration files to Home Assistant..."

# Check if HA-Config directory exists
if [ ! -d ~/HA-Config ]; then
    echo "Creating HA-Config directory..."
    mkdir -p ~/HA-Config/custom_components
fi

# Copy the sol integration files
cp -r custom_components/sol/ ~/HA-Config/custom_components/

echo "Files copied successfully!"
echo "Remember to restart Home Assistant or reload the integration." 