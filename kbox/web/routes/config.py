"""
Configuration routes: read the full config with its schema, and update one
key (operator only), restarting the streaming subsystem when the key needs it.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ...config_manager import ConfigManager
from ...playback import PlaybackController, PlaybackState
from ..dependencies import check_operator, get_config_manager, get_playback_controller
from ..models import ConfigUpdateRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/config")
def get_config(config: ConfigManager = Depends(get_config_manager)):
    """
    Get all configuration with rich schema metadata.

    Returns:
        - values: Current configuration values
        - schema: Metadata for each editable key (control type, options, description)
        - groups: Group definitions for organizing the config UI
    """
    config_data = config.get_full_config()

    # Ensure current audio device is in the options list
    # (DeviceMonitor may not return busy devices, but we still want to show the current one)
    current_device = config_data["values"].get("audio_output_device")
    if current_device:
        audio_schema = config_data["schema"].get("audio_output_device")
        if audio_schema and "options" in audio_schema:
            device_values = [opt["value"] for opt in audio_schema["options"]]
            if current_device not in device_values:
                audio_schema["options"].append(
                    {
                        "value": current_device,
                        "label": f"{current_device} (current)",
                    }
                )

    return config_data


@router.patch("/api/config")
def update_config(
    request: Request,
    request_data: ConfigUpdateRequest,
    config: ConfigManager = Depends(get_config_manager),
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Update configuration (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    # Save the configuration
    config.set(request_data.key, request_data.value)

    # Check if this config change requires streaming subsystem restart
    STREAMING_CONFIG_KEYS = {
        "audio_output_device",
        "audio_output_channels",
        "overlay_qr_position",
        "overlay_qr_size_percent",
    }

    needs_restart = request_data.key in STREAMING_CONFIG_KEYS

    if needs_restart and request.app.state.streaming_controller:
        try:
            logger.info("Applying streaming config change: %s", request_data.key)

            # Stop, rebuild and show idle as one locked step, so no other
            # request can touch playback while the pipeline is gone.
            playback.apply_streaming_config()

            logger.info("Streaming configuration applied successfully")

            return {
                "status": "updated",
                "key": request_data.key,
                "value": request_data.value,
                "restarted": True,
                "message": "Configuration updated and streaming subsystem restarted",
            }
        except Exception as e:
            logger.error("Error restarting streaming subsystem: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Config saved but failed to restart streaming: {str(e)}",
            )

    # Refresh the idle screen if the theme changed and we're currently
    # showing it. If a song is playing, the next idle transition will
    # pick up the new theme automatically.
    if request_data.key == "suggestion_theme" and playback.state == PlaybackState.STOPPED:
        playback.show_idle_screen()

    return {
        "status": "updated",
        "key": request_data.key,
        "value": request_data.value,
    }
