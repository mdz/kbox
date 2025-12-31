#!/bin/bash
# Set YouTube API key in kbox database (Docker)
#
# Usage: ./setup_api_key.sh <your-api-key>
# Example: ./setup_api_key.sh AIzaSyABC123xyz...

set -e

API_KEY="${1:-}"

if [ -z "$API_KEY" ]; then
    echo "Usage: $0 <your-youtube-api-key>"
    echo ""
    echo "This script sets the YouTube API key in the kbox database."
    echo "The kbox container must be built first (docker-compose build)."
    exit 1
fi

echo "Setting YouTube API key in kbox database..."

# Run configure_api_key.py script in a one-off container
docker-compose run --rm kbox python3 configure_api_key.py "$API_KEY"

echo ""
echo "✓ API key configured! You can now start kbox with: docker-compose up"



