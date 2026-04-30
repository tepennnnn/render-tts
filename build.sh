#!/bin/bash
set -o errexit

# Install FFmpeg
apt-get update
apt-get install -y ffmpeg

# Install Python dependencies
pip install -r requirements.txt
