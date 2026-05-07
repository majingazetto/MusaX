#!/bin/bash
# MusaX Environment Setup Script

echo "Checking MusaX dependencies..."

# Detect OS
OS="$(uname)"

# 1. System dependencies for PyAudio
if [ "$OS" == "Darwin" ]; then
    echo "MacOS detected. Checking for PortAudio (brew)..."
    if ! command -v brew &> /dev/null; then
        echo "Error: Homebrew is required for PortAudio installation."
    else
        brew install portaudio
    fi
elif [ "$OS" == "Linux" ]; then
    echo "Linux detected. Checking for PortAudio (apt)..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y python3-pyaudio portaudio19-dev
    else
        echo "Warning: Unsupported package manager. Please install portaudio and python3-pyaudio manually."
    fi
fi

# 2. Python dependencies
echo "Installing Python packages..."
if [ "$OS" == "Linux" ] && command -v apt-get &> /dev/null; then
    sudo apt-get install -y python3-flask python3-pyaudio
else
    python3 -m pip install flask pyaudio --break-system-packages
fi

# 3. Optional dependencies for better audio support
# python3 -m pip install sounddevice numpy

echo "MusaX setup complete."
