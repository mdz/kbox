#!/usr/bin/env python3
"""
Quick script to configure audio input source for kbox.

Usage: 
  uv run python configure_audio_input.py
  uv run python configure_audio_input.py osxaudiosrc
  uv run python configure_audio_input.py osxaudiosrc "device_name"
"""

import sys
from kbox.database import Database
from kbox.config_manager import ConfigManager

def main():
    # Default to osxaudiosrc on macOS, alsasrc on Linux
    import platform
    if platform.system() == 'Darwin':
        default_source = 'osxaudiosrc'
    elif platform.system() == 'Linux':
        default_source = 'alsasrc'
    else:
        default_source = None
    
    if len(sys.argv) > 1:
        audio_source = sys.argv[1]
    else:
        audio_source = default_source
    
    if len(sys.argv) > 2:
        audio_device = sys.argv[2]
    else:
        audio_device = None
    
    if not audio_source:
        print("Error: Please specify an audio source")
        print("\nCommon options:")
        print("  - osxaudiosrc (macOS)")
        print("  - alsasrc (Linux)")
        print("  - pulsesrc (Linux with PulseAudio)")
        print("\nUsage:")
        print("  uv run python configure_audio_input.py <source> [device]")
        sys.exit(1)
    
    print(f"Configuring audio input source: {audio_source}")
    if audio_device:
        print(f"  Device: {audio_device}")
    else:
        print("  Device: (default/auto-detect)")
    
    db = Database()
    config = ConfigManager(db)
    
    config.set('audio_input_source', audio_source)
    if audio_device:
        config.set('audio_input_source_device', audio_device)
    else:
        # Clear device if not specified (use default)
        config.set('audio_input_source_device', '')
    
    # Verify
    saved_source = config.get('audio_input_source')
    saved_device = config.get('audio_input_source_device')
    
    if saved_source == audio_source:
        print("✓ Audio input source configured successfully!")
        print(f"  Source: {saved_source}")
        if saved_device:
            print(f"  Device: {saved_device}")
        print("\nNote: You may need to restart kbox for changes to take effect.")
    else:
        print("✗ Error: Audio input source was not saved correctly")
        sys.exit(1)
    
    db.close()

if __name__ == '__main__':
    main()

