#!/bin/bash
# install.sh – Abhängigkeiten installieren auf Pi 3B (Raspberry Pi OS)

echo "=== Robot Installer ==="

sudo apt-get update -qq
sudo apt-get install -y python3-pip python3-dev portaudio19-dev libatlas-base-dev

pip3 install --break-system-packages \
    openai \
    sounddevice \
    scipy \
    RPi.GPIO

echo ""
echo "=== Fertig! ==="
echo "API Key setzen:"
echo "  echo 'export OPENAI_API_KEY=sk-...' >> ~/.bashrc"
echo "  source ~/.bashrc"
echo ""
echo "Starten mit:"
echo "  python3 robot.py"
