#!/usr/bin/env python3
"""
Quick script to configure YouTube API key.

Usage: uv run python configure_api_key.py
"""

import sys

from kbox.config_manager import ConfigManager
from kbox.database import Database


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python configure_api_key.py <YOUR_API_KEY>")
        print("\nTo get an API key:")
        print("1. Go to https://console.cloud.google.com/")
        print("2. Create a project and enable YouTube Data API v3")
        print("3. Create credentials (API key)")
        print("4. Copy the key and use it here")
        sys.exit(1)

    api_key = sys.argv[1]

    print("Configuring YouTube API key...")
    db = Database()
    config = ConfigManager(db)
    config.set("youtube_api_key", api_key)

    # Verify
    saved_key = config.get("youtube_api_key")
    if saved_key == api_key:
        print("✓ API key configured successfully!")
        print(f"  Key: {api_key[:10]}...{api_key[-4:]}")
    else:
        print("✗ Error: API key was not saved correctly")
        sys.exit(1)

    db.close()


if __name__ == "__main__":
    main()
