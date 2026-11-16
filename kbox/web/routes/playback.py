"""
Playback routes: status, transport controls, pitch, restart and seek.

Every control except status is operator-only, checked per handler via
``check_operator``. Handlers get their collaborators through the providers
in :mod:`kbox.web.dependencies`, so this router can be imported and
inspected without building an app.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ...playback import PlaybackController, SeekOutcome
from ..dependencies import check_operator, get_current_user_id, get_playback_controller
from ..models import PitchRequest, SeekRequest
from ..serializers import serialize_queue_item

router = APIRouter()


@router.get("/api/playback/status")
def get_playback_status(
    playback: PlaybackController = Depends(get_playback_controller),
):
    """Get current playback status."""
    status = playback.get_status()
    return {
        "state": status.state.value,
        "current_song": (
            serialize_queue_item(status.current_song) if status.current_song else None
        ),
        "position_seconds": status.position_seconds,
    }


@router.post("/api/playback/play")
def play(
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Start/resume playback (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    if playback.play():
        return {"status": "playing"}
    else:
        raise HTTPException(status_code=400, detail="Failed to start playback")


@router.post("/api/playback/pause")
def pause(
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Pause playback (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    if playback.pause():
        return {"status": "paused"}
    else:
        raise HTTPException(status_code=400, detail="Failed to pause")


@router.post("/api/playback/stop")
def stop(
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Stop playback and return to idle (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    # Stopping when already stopped/idle is not an error - the desired
    # end state is already achieved, so just report success.
    playback.stop_playback()
    return {"status": "stopped"}


@router.post("/api/playback/skip")
def skip(
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Skip to next song (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    if playback.skip():
        return {"status": "skipped"}
    else:
        # Return 200 with warning instead of error - no next song available
        return {"status": "no_next_song", "message": "No next song in queue to skip to"}


@router.post("/api/playback/previous")
def previous(
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Go to previous song (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    if playback.previous():
        return {"status": "previous"}
    else:
        # Return 200 with warning instead of error - no previous song available
        return {"status": "no_previous_song", "message": "Previous song not available"}


@router.post("/api/playback/jump/{item_id}")
def jump_to_song(
    item_id: int,
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Jump to and play a song immediately at its current queue position (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    if playback.jump_to_song(item_id):
        return {"status": "playing_now", "item_id": item_id}
    else:
        raise HTTPException(status_code=400, detail="Failed to play song now")


@router.post("/api/playback/pitch")
def set_pitch(
    request_data: PitchRequest,
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """
    Set pitch adjustment for current song.
    Allowed if: user owns the song OR user is an operator.
    User identity is determined from session — self-declared and unverified
    by design, so this gates which UUID the current session is acting as,
    not proof of who that UUID belongs to.
    """
    status = playback.get_status()
    current_song = status.current_song

    if not current_song:
        raise HTTPException(status_code=400, detail="No song is currently playing")

    # Check authorization: user's own song OR operator
    is_own_song = current_song.user_id == current_user_id

    if not is_own_song and not is_operator:
        raise HTTPException(status_code=403, detail="You can only adjust pitch for your own song")

    if playback.set_pitch(request_data.semitones, changed_by=current_user_id):
        return {"status": "updated", "pitch": request_data.semitones}
    else:
        raise HTTPException(status_code=400, detail="Failed to update pitch")


@router.post("/api/playback/restart")
def restart(
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Restart current song from the beginning (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    result = playback.restart()
    if result.outcome is SeekOutcome.FAILED:
        raise HTTPException(status_code=400, detail="Failed to restart song")
    if result.outcome is SeekOutcome.NOTHING_PLAYING:
        # Not an error: the operator controls are visible whatever the
        # state, so the frontend treats this as a silent no-op.
        return {"status": "nothing_playing"}
    return {"status": "restarted"}


@router.post("/api/playback/seek")
def seek(
    request_data: SeekRequest,
    playback: PlaybackController = Depends(get_playback_controller),
    is_operator: bool = Depends(check_operator),
):
    """Seek forward or backward in current song (operator only)."""
    if not is_operator:
        raise HTTPException(status_code=403, detail="Operator authentication required")

    result = playback.seek_relative(request_data.delta_seconds)
    if result.outcome is SeekOutcome.FAILED:
        raise HTTPException(status_code=400, detail="Failed to seek")
    if result.outcome is SeekOutcome.NOTHING_PLAYING:
        # See the note in restart(): an expected no-op, not a failure.
        return {"status": "nothing_playing"}
    return {"status": "seeked", "position": result.position}
