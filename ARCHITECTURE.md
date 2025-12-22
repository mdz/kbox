# kbox Technical Architecture

## Overview

kbox is a self-contained, open-source karaoke system that integrates YouTube video playback with real-time audio processing (pitch shifting, mixing, effects) and queue management via a web interface.

## System Components

### 1. Core Components

#### 1.1 `PlaybackController`
**Purpose**: Orchestrates playback, manages state transitions, handles error recovery

**Responsibilities**:
- Manages playback state (idle, playing, paused, error)
- Coordinates between queue, YouTube client, and streaming controller
- Handles song transitions (auto-advance, pitch reset)
- Implements error recovery (retry on transient errors, skip on fatal)
- Manages download status and playback readiness

**Key Methods**:
- `play()` - Start/resume playback
- `pause()` - Pause playback
- `skip()` - Skip to next song
- `previous()` - Go to previous song
- `load_next_song()` - Prepare next song for playback
- `handle_error()` - Error recovery logic

#### 1.2 `StreamingController` (refactored)
**Purpose**: Manages GStreamer pipeline for audio/video playback

**Responsibilities**:
- Supports two modes: passthrough and YouTube playback
- Manages pipeline lifecycle (create, start, stop, reconfigure)
- Handles pitch shifting (per-song, resettable)
- Phase 2: Audio mixing (mic + YouTube) with reverb
- Low-latency audio processing

**Pipeline Modes**:

**Mode 1: Passthrough** (current functionality)
```
Audio: alsasrc → audioconvert → pitch_shift → audioconvert → alsasink
Video: v4l2src → capsfilter → jpegdec → kmssink
```

**Mode 2: YouTube Playback** (Phase 1)
```
Audio: filesrc (downloaded file) → decodebin → audioconvert → pitch_shift → audioconvert → alsasink
Video: filesrc (downloaded file) → decodebin → videoconvert → videoscale → video/x-raw → kmssink
```

**Note**: All files are pre-downloaded before playback. Downloads start immediately when songs are added to the queue.

**Mode 3: YouTube + Mic Mixing** (Phase 2)
```
Mic: alsasrc → audioconvert → volume → reverb → [mixer sink pad 0]
YouTube: filesrc → decodebin → audioconvert → volume → pitch_shift → [mixer sink pad 1]
Mixer: audiomixer → audioconvert → alsasink

Video: filesrc → decodebin → videoconvert → videoscale → video/x-raw → kmssink
```

**Reverb Implementation**:
- Use LADSPA plugin system (same as pitch shifting)
- Plugin name configurable in database (easy to swap)
- Start with simplest available reverb plugin
- Abstract reverb element creation to allow plugin swapping
- Common options: `ladspa-reverb`, `freeverb`, or simple delay-based effects

**Key Methods**:
- `set_mode(mode)` - Switch between passthrough/YouTube modes
- `load_file(filepath)` - Load YouTube video file
- `set_pitch_shift(semitones)` - Adjust pitch (per-song)
- `set_mic_volume(level)` - Adjust mic input level (Phase 2)
- `set_youtube_volume(level)` - Adjust YouTube audio level (Phase 2)
- `set_reverb(amount)` - Adjust reverb effect (Phase 2)

#### 1.3 `QueueManager`
**Purpose**: Manages song queue with persistence

**Responsibilities**:
- CRUD operations on queue items
- Queue persistence (SQLite)
- Queue reordering
- Queue state management (downloaded, ready, playing, error)
- Queue statistics (total items, estimated wait time)

**Database Schema**:
```sql
CREATE TABLE queue_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position INTEGER NOT NULL,
    user_name TEXT NOT NULL,
    youtube_video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    duration_seconds INTEGER,
    thumbnail_url TEXT,
    pitch_semitones INTEGER DEFAULT 0,
    download_status TEXT DEFAULT 'pending',  -- pending, downloading, ready, error
    download_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    played_at TIMESTAMP,
    error_message TEXT
);

CREATE TABLE config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_queue_position ON queue_items(position);
CREATE INDEX idx_queue_status ON queue_items(download_status);
```

**Key Methods**:
- `add_song(user_name, youtube_video_id, title, duration, thumbnail, pitch=0)` - Add to end
- `remove_song(item_id)` - Remove by ID
- `reorder_song(item_id, new_position)` - Move to new position
- `get_queue()` - Get all items ordered by position
- `get_next_song()` - Get next ready song
- `clear_queue()` - Remove all items
- `update_download_status(item_id, status, path=None, error=None)` - Update download state
- `mark_played(item_id)` - Mark as played

#### 1.4 `YouTubeClient`
**Purpose**: YouTube search and video download management

**Responsibilities**:
- Search YouTube using Data API v3
- Automatically append "karaoke" to search queries
- Download videos using yt-dlp (pre-download strategy)
- Manage download queue and status
- Cache downloaded videos for reuse

**Key Methods**:
- `search(query, max_results=10)` - Search with auto-"karaoke" keyword
- `get_video_info(video_id)` - Get metadata (title, duration, thumbnail)
- `download_video(video_id, queue_item_id)` - Start background download, update queue status
- `get_download_path(video_id)` - Get cached file path if exists
- `is_downloaded(video_id)` - Check if already cached
- `get_download_status(video_id)` - Get current download status (pending, downloading, ready, error)

**Download Strategy**:
- **Pre-download**: All songs download immediately when added to queue
- Downloads happen in background threads (non-blocking)
- Queue items show download status: pending → downloading → ready (or error)
- Web UI displays status icons for each queue item
- Playback only starts when download is complete and ready
- If download fails, user can retry via API/web UI

**Implementation Notes**:
- Use yt-dlp with `--format bestvideo+bestaudio/best` for quality
- Store downloads in `~/.kbox/cache/` or configurable path
- Filename: `{video_id}.{ext}` for easy lookup
- Download progress tracked and reported via queue status
- Retry failed downloads automatically (transient errors)

#### 1.5 `WebServer`
**Purpose**: REST API and web interface for queue management

**Responsibilities**:
- REST API endpoints for queue operations
- Web UI for mobile devices
- Queue controls with safety (disabled by default)
- PIN-based operator authentication
- Local network hosting (no cloud dependency)

**Network Architecture**:
- Web server runs on Raspberry Pi 5
- Serves on local network (WiFi/Ethernet)
- Users connect via phones/tablets on same network
- No cloud service or external dependencies required
- IP address discovery: Display on startup, or use mDNS (`.local` hostname)

**Authentication**:
- **No user authentication**: Users identify themselves by typing their name
- **Operator privileges**: Simple PIN code system
  - Default: Queue controls disabled
  - User enters PIN to enable operator mode
  - PIN stored in configuration (default: simple 4-digit, changeable via UI)
  - Operator mode enables: play/pause/skip, queue reordering, volume/pitch controls
  - Anyone can add songs to queue (no PIN required)

**API Endpoints**:

```
GET  /api/queue                    - Get current queue
POST /api/queue                    - Add song to queue
DELETE /api/queue/{id}             - Remove song from queue
PATCH /api/queue/{id}/position     - Reorder song
POST /api/queue/clear              - Clear entire queue

GET  /api/youtube/search?q={query} - Search YouTube
GET  /api/youtube/video/{id}        - Get video info

GET  /api/playback/status          - Get playback state
POST /api/playback/play            - Start/resume playback
POST /api/playback/pause           - Pause playback
POST /api/playback/skip            - Skip to next
POST /api/playback/previous        - Go to previous
POST /api/playback/pitch           - Set pitch for current song
POST /api/playback/volume           - Set volume levels (Phase 2)

POST /api/auth/operator             - Authenticate as operator (PIN)
POST /api/auth/logout               - Exit operator mode

GET  /api/config                   - Get configuration
PATCH /api/config                  - Update configuration

GET  /                              - Web UI
```

**API Response Format**:
- All endpoints return JSON
- Standard error responses: `{"error": "message", "code": "ERROR_CODE"}`
- Queue items include download status: `{"status": "downloading|ready|error", ...}`

**Web UI Features**:
- Mobile-optimized interface
- Search YouTube with results (thumbnails, titles)
- Queue display (by user name, position, wait time, download status icons)
- PIN entry for operator mode
- Queue controls (disabled by default, require PIN)
- Playback controls (play/pause/skip) - operator only
- Pitch adjustment per song (operator only)
- Volume/mixing controls (Phase 2, operator only)
- Download status indicators (pending/downloading/ready/error)

**Security Considerations**:
- Queue controls disabled by default (prevent accidental actions)
- PIN-based operator authentication (simple, no complex auth system)
- Input validation and sanitization
- Local network only (no external exposure by default)

#### 1.6 `MidiController` (enhanced)
**Purpose**: MIDI input for pitch adjustment

**Responsibilities**:
- Listen for MIDI note events
- Map notes to semitone offsets
- Update pitch for current playing song only
- Reset pitch at song transitions

**Key Methods**:
- `handle_note_on(msg)` - Process MIDI note, update current song pitch
- `reset_pitch()` - Reset to song's default pitch

### 2. Data Flow

#### Phase 1: YouTube Playback Flow

1. **User adds song**:
   - Web UI: User searches → selects video → enters name → submits
   - API: `POST /api/queue` → QueueManager adds item → YouTubeClient starts **immediate background download**
   - Download status: pending → downloading → ready (or error)
   - Web UI shows status icon for each queue item

2. **Playback**:
   - PlaybackController checks queue for **ready** songs (download complete)
   - Only plays songs with status="ready"
   - StreamingController loads file, sets pitch, starts pipeline
   - On EOS: PlaybackController loads next **ready** song, resets pitch
   - On error: Retry logic or skip to next
   - If next song not ready: Pause and wait, or show message

3. **Pitch adjustment**:
   - User adjusts via web UI or MIDI keyboard
   - Updates current song's pitch in queue
   - StreamingController applies pitch shift

#### Phase 2: Mixing Flow

1. **Audio mixing**:
   - Mic audio and YouTube audio both feed into audiomixer
   - Separate volume controls for each source
   - Reverb applied to mic only
   - Mixed output to speakers

2. **Level adjustment**:
   - Web UI or API controls mic/youtube volume levels
   - StreamingController updates volume elements

### 3. Error Handling & Recovery

#### Download Errors
- Transient errors: Retry with exponential backoff
- Fatal errors: Mark queue item as error, skip to next
- User can retry failed downloads via API

#### Playback Errors
- Network errors during download: Retry download
- File corruption: Re-download, skip current song
- Pipeline errors: Log, attempt recovery, skip if fatal
- Auto-resume: Attempt to continue from error state

#### State Recovery
- On startup: Load queue from database
- Resume downloads in progress
- Continue from last played position if interrupted

### 4. Configuration

**Configuration Keys** (stored in SQLite `config` table):
- `audio_input_device` - ALSA device for mic input
- `audio_output_device` - ALSA device for output
- `video_input_device` - V4L2 device for passthrough
- `midi_input_name` - MIDI device name
- `youtube_api_key` - YouTube Data API key
- `cache_directory` - Path for downloaded videos
- `operator_pin` - PIN code for operator privileges (default: "1234")
- `default_mic_volume` - Default mic level (0.0-1.0)
- `default_youtube_volume` - Default YouTube level (0.0-1.0)
- `default_reverb_amount` - Default reverb (0.0-1.0)
- `reverb_plugin` - LADSPA plugin name for reverb (configurable, swappable)

**Configuration Management**:
- Load from database on startup
- Update via API/web UI
- Fallback to defaults if not set
- Platform-specific defaults (macOS vs Linux)

### 5. Testing Strategy

#### Unit Tests
- QueueManager: CRUD operations, persistence, reordering
- YouTubeClient: Search parsing, download path management
- PlaybackController: State transitions, error handling
- Configuration: Loading, defaults, updates

### 6. Technology Stack

**Core**:
- Python 3.9+
- GStreamer 1.0 (via gst-python)
- SQLite3
- FastAPI (for web server and REST API)

**YouTube Integration**:
- Google YouTube Data API v3 (search)
- yt-dlp (download/playback)

**Audio Processing**:
- LADSPA plugins (rubberband for pitch, reverb plugin)
- GStreamer audiomixer

**MIDI**:
- python-rtmidi (via mido)

**Dependencies**:
- mido[ports-rtmidi]
- pytest
- fastapi
- uvicorn (ASGI server)
- google-api-python-client (YouTube API)
- yt-dlp

### 7. File Structure

```
kbox/
├── __init__.py
├── main.py                 # Entry point
├── config.py               # Configuration loading (temporary, migrate to DB)
├── server.py               # Main server orchestration
├── playback.py             # PlaybackController
├── streaming.py            # StreamingController (refactored)
├── queue.py                # QueueManager
├── youtube.py              # YouTubeClient
├── web/
│   ├── __init__.py
│   ├── server.py           # WebServer (Flask/FastAPI app)
│   ├── api.py              # API route handlers
│   └── templates/          # Web UI templates
│       └── index.html
├── midi.py                 # MidiController (enhanced)

test/
├── test_queue.py
├── test_youtube.py
├── test_playback.py
├── test_streaming.py
└── fixtures/

data/
└── kbox.db                 # SQLite database (queue + config)
```

### 8. Implementation Phases

#### Phase 1: YouTube Integration + Queue + Basic Playback
1. Database schema and QueueManager
2. YouTubeClient (search + download)
3. WebServer with API and basic UI
4. PlaybackController orchestration
5. StreamingController YouTube mode
6. Queue persistence and recovery
7. Error handling and retry logic

#### Phase 2: Software Mixing + Reverb
1. StreamingController mixing mode
2. Volume controls (mic + YouTube)
3. Reverb effect integration
4. Web UI for mixing controls

#### Phase 3: Polish & Optimization
1. Performance optimization
2. UI/UX improvements
3. Advanced features (queue overlay, etc.)
4. Documentation

### 9. Platform Considerations

**macOS Development**:
- Use `osxaudiosrc`/`osxaudiosink` for audio
- No V4L2, use test video files or mock sources
- Same GStreamer pipeline architecture

**Linux Deployment (Raspberry Pi 5)**:
- ALSA for audio I/O
- V4L2 for video passthrough
- KMS for video output
- Performance monitoring and optimization

**Cross-platform Compatibility**:
- Abstract device selection in Config
- Platform-specific GStreamer element selection
- Shared core logic (queue, YouTube, playback)

### 10. Performance Considerations

**Low Latency Requirements**:
- Use GStreamer's low-latency pipeline flags
- Minimize buffering in audio pipeline
- Direct hardware access (ALSA) for audio I/O
- Test and measure actual latency

**Raspberry Pi 5 Constraints**:
- Monitor CPU usage during playback + mixing
- Optimize video decoding (hardware acceleration if available)
- Consider reducing video quality if needed
- Profile and optimize hot paths

**Caching Strategy**:
- Cache downloaded videos indefinitely
- Check cache before downloading
- Background download of next song in queue
- Disk space management (configurable cache size limit)

## Next Steps

1. Review and refine this architecture
2. Create detailed implementation plan for Phase 1
3. Set up project structure and dependencies
4. Begin implementation with database schema and QueueManager

