# kbox Cloud Service Proposal

## Problem Statement

Currently, client/server communication in kbox relies on local network connectivity:
- Guests must be on the same WiFi network as the kbox server
- The kbox operator must configure an `external_url` setting
- A QR code is displayed on screen for guests to scan

This approach has several limitations:
1. **WiFi networks that block client-to-client traffic** - Many public/enterprise networks isolate clients
2. **Multiple WiFi networks** - kbox on wired ethernet, guests on WiFi that can't reach it
3. **Guests not on WiFi** - Cellular-only users can't connect
4. **Configuration complexity** - Operators must figure out the correct URL to use

## Solution: kbox Cloud Service

A cloud-hosted web application that kbox instances connect to, allowing guests to reach them from any network. Designed as a proper application that can grow to support additional features over time.

### Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                    kbox Cloud Service                          │
│                    (Fly.io + PostgreSQL)                       │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │                   Future Features                         │ │
│  │  • Identity (user accounts, history sync)                 │ │
│  │  • Sharing (session links, song lists)                    │ │
│  │  • Cloud Processing (audio analysis, vocal removal)       │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │              MVP: Connectivity Layer                      │ │
│  │                                                           │ │
│  │  • Authenticated kbox instances connect via WebSocket     │ │
│  │  • Guests access kbox through cloud-assigned room URLs    │ │
│  │  • All requests proxied to local kbox instance            │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
│   wss://cloud.kbox.app/api/v1/connect   https://cloud.kbox.app │
│              ▲                                 ▲               │
└──────────────┼─────────────────────────────────┼───────────────┘
               │                                 │
               │ WebSocket                       │ HTTPS
               │ (persistent)                    │ (requests)
               │                                 │
┌──────────────┴──────────────┐     ┌────────────┴───────────────┐
│      kbox Server (Pi)       │     │      Guest's Phone         │
│  - Authenticates with key   │     │  - Scans QR code           │
│  - Gets room ID: "abc123"   │     │  - Visits cloud URL        │
│  - QR shows cloud URL       │     │  - Normal web UI works     │
│  - Handles proxied requests │     │                            │
└─────────────────────────────┘     └────────────────────────────┘
```

### Flow

1. **One-time setup**: Set `CLOUD_API_KEY` environment variable on Fly.io
2. **Device startup**: Opens WebSocket to cloud with API key authentication
3. **Room assignment**: Cloud assigns random room ID (e.g., `abc123`)
4. **QR code generation**: Device displays QR for `https://cloud.kbox.app/r/abc123`
5. **Guest connects**: Visits cloud URL, cloud proxies everything to device
6. **API calls**: All requests forwarded over WebSocket to device and back

### Why This Approach?

This is the same pattern used by:
- **Spotify Connect** - Phone commands go through Spotify's cloud to speakers
- **Zoom Rooms Companion Mode** - Devices communicate via Zoom's cloud

The cloud service enables "just works" connectivity across any network, while providing a foundation for future features.

## Alternatives Considered

| Alternative | Pros | Cons |
|-------------|------|------|
| **ngrok** | Already built, they handle abuse | Requires their account, interstitial warning on free tier |
| **localtunnel** | No account needed, free | Unreliable, frequent 502 errors |
| **Cloudflare Tunnel** | Production-ready, free tier | Requires owning a domain, complex setup |
| **Cloudflare Workers** | Serverless, cheap | Limited for future features, cramped architecture |
| **WebRTC Data Channels** | Peer-to-peer after signaling | Major architecture change, still needs signaling |
| **mDNS/Bonjour** | Works locally | Doesn't solve cross-network problem |

A proper hosted application provides:
- Full control over the experience
- Room to grow (identity, sharing, cloud processing)
- No third-party friction for operators or guests

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Hosting** | Fly.io | Docker-based, global edge, reasonable pricing |
| **Language** | Python | Same as kbox, shared knowledge |
| **Framework** | FastAPI | Already used in kbox, async, WebSocket support |
| **Database** | None (MVP) | In-memory state only, add Postgres later if needed |
| **Static assets** | Proxy through kbox | Simpler, UI always matches kbox version |
| **Real-time updates** | Design for WebSocket, implement later | Future-proof the protocol |
| **Authentication** | Fixed API key in env var | Hardcoded for single user, add DB later |
| **Room lifetime** | 12 hours max | Balance between usability and abuse resistance |

## MVP Scope: Minimal Relay

For the first iteration:
- **No database** - In-memory state only (rooms, connections)
- **Fixed API key** - Hardcoded in environment variable
- **Single user** - Just one device (yours) uses the service
- **Basic rate limiting** - Simple in-memory counters to prevent abuse
- **Test under real conditions** - Validate the concept at actual karaoke sessions

This is the absolute minimum:
- No database setup
- No registration API
- No email verification
- No persistence (rooms lost on restart, but that's fine for testing)
- Just the core relay functionality with basic security

## Threat Model

### Threats to kbox Instances

| Threat | Risk | Mitigation |
|--------|------|------------|
| Room ID guessing | Low | Cryptographically random IDs (62^8 = 218 trillion) |
| Unauthorized access | Low | Room IDs unguessable, rooms expire quickly |

### Threats to the Cloud Service (Abuse)

The bigger concern is bad actors abusing the relay for purposes unrelated to kbox:

| Threat | Description | Mitigation |
|--------|-------------|------------|
| **C2 Channels** | Malware uses relay as command & control | Instance authentication (API keys), rate limiting |
| **Data Exfiltration** | Tunneling data through the relay | Size limits, room expiry |
| **Anonymization** | Using relay to hide traffic sources | Authenticated instances, audit logging |
| **Resource Exhaustion** | DoS or budget exhaustion attacks | Rate limiting, Fly.io's built-in protections |

### Why Instance Authentication is Required

An anonymous relay is attractive for abuse. Requiring operators to register:
- Creates accountability (email on file)
- Allows revoking bad actors
- Makes abuse less convenient than alternatives (Tor, free VPSes, etc.)

The friction is on operators (one-time signup), not guests (zero friction).

### Rate Limiting Strategy

```
Per-IP limits:
  - New WebSocket connections: 5/minute/IP
  - HTTP requests to a room: 60/minute/IP

Per-room limits:
  - Total requests: 300/minute/room
  - Max concurrent guests: 50 per room

Per-instance limits:
  - Max active rooms: 5 per instance

Global limits:
  - Max active rooms: 10,000
```

### Room ID Strategy

Room IDs must be **stable across cloud restarts** so guests don't need to re-scan the QR code.

**Approach**: Device-derived room ID
- Device generates a stable room ID (e.g., first 8 chars of SHA256(API_KEY))
- Device sends desired room ID when connecting
- Cloud accepts the requested room ID (if authenticated)
- Same device always gets the same room ID

**Security:**
- **Format**: 8 characters, base62 (a-zA-Z0-9)
- **Keyspace**: 62^8 = 218 trillion combinations
- **Not enumerable**: Room ID is essentially unguessable
- **Tied to API key**: Only the device with the matching API key can claim a room ID

### Payload Limits

```
Max request body:  100KB
Max response body: 5MB
Max WebSocket message: 1MB
```

## Technology Stack

### Hosting: Fly.io

- Docker-based deployment
- Global edge network (low latency)
- Managed PostgreSQL included
- Pay-as-you-go pricing (~$5-10/month for small scale)
- WebSocket support with persistent connections

### Project Structure (within existing kbox repo)

```
kbox/
├── device/                       # Existing: runs on Pi
│   ├── main.py
│   ├── playback.py
│   ├── streaming.py
│   ├── web/
│   │   └── server.py
│   ├── relay_client.py          # NEW: connects to cloud service
│   └── ...
│
├── cloud/                       # NEW: cloud service (runs on Fly.io)
│   ├── __init__.py
│   ├── main.py                  # FastAPI app, lifespan events
│   ├── config.py                # Settings from environment (API key)
│   ├── auth.py                  # API key validation
│   ├── rooms.py                 # Room manager (in-memory), connection tracking
│   ├── rate_limit.py            # Simple in-memory rate limiting
│   ├── proxy.py                 # HTTP ↔ WebSocket proxy logic
│   └── routes/
│       ├── connect.py           # WebSocket endpoint for device
│       └── relay.py             # Guest proxy endpoints
│
├── shared/                      # NEW: shared between device and cloud
│   ├── __init__.py
│   └── protocol.py              # WebSocket message types/schemas
│
├── Dockerfile                   # Existing: device on Pi
├── Dockerfile.cloud             # NEW: cloud service for Fly.io
├── fly.toml                     # NEW: Fly.io configuration
└── ...
```

### Shared Protocol Module

The `shared/protocol.py` module defines message types used by both sides:

```python
# shared/protocol.py
from pydantic import BaseModel
from typing import Literal, Optional, Dict, Any

class RoomAssigned(BaseModel):
    type: Literal["room_assigned"] = "room_assigned"
    room_id: str
    room_url: str

class HttpRequest(BaseModel):
    type: Literal["request"] = "request"
    id: str                          # Request ID for matching response
    method: str                      # GET, POST, etc.
    path: str                        # /api/queue, etc.
    headers: Dict[str, str]
    body: Optional[bytes] = None

class HttpResponse(BaseModel):
    type: Literal["response"] = "response"
    id: str                          # Matches request ID
    status: int
    headers: Dict[str, str]
    body: Optional[bytes] = None

# ... etc
```

This ensures the kbox client and cloud service speak the same protocol.

### Data Model (MVP - In-Memory)

```python
# In-memory state (no database for MVP)

# Active rooms
rooms: Dict[str, Room] = {}

class Room:
    id: str                    # "abc123"
    websocket: WebSocket        # Connection to device
    created_at: datetime
    expires_at: datetime        # 12h from creation
    request_counter: Dict[str, int]  # Per-IP rate limiting

# Rate limit counters (per IP)
rate_limits: Dict[str, RateLimit] = {}

class RateLimit:
    ip: str
    requests: int
    last_reset: datetime
```

**API Key**: Stored in environment variable `CLOUD_API_KEY`, validated on WebSocket connect.

**No persistence**: Rooms are lost on restart, but that's fine for testing. Can add Postgres later if needed.

### API

```
Device Connection (WebSocket):
  WS /api/v1/connect
    Headers: Authorization: Bearer <api_key>

    # Device sends its desired room ID (derived from API key)
    Client sends: { "type": "connect", "room_id": "abc12345" }
    Server sends: { "type": "connected", "room_url": "https://cloud.kbox.app/r/abc12345" }

    # Cloud forwards guest requests to device
    Server sends: { "type": "request", "id": "...", "method": "GET", "path": "/api/queue", ... }
    Client sends: { "type": "response", "id": "...", "status": 200, "body": "..." }

Guest Access (proxied to device):
  GET  /r/{room_id}              → device UI
  GET  /r/{room_id}/api/*        → device API
  POST /r/{room_id}/api/*        → device API
  (all HTTP methods supported)

Health/Status:
  GET /health                    → { "status": "ok" }

Future (when opening to others):
  POST /api/v1/instances/register  → Registration flow
```

## Device Integration

### New Module: device/relay_client.py

```python
from shared.protocol import RoomAssigned, HttpRequest, HttpResponse

class RelayClient:
    """Connects to cloud service, handles request/response proxying.

    Automatically reconnects if connection drops (e.g., cloud service restarts).
    Room ID is stable (derived from API key), so QR code URL never changes.
    """

    def __init__(self, api_key: str, cloud_url: str, local_app: FastAPI):
        self.api_key = api_key
        self.cloud_url = cloud_url
        self.local_app = local_app
        self.room_id = self._derive_room_id(api_key)
        self.room_url = f"https://cloud.kbox.app/r/{self.room_id}"
        self.should_reconnect = True

    def _derive_room_id(self, api_key: str) -> str:
        """Generate stable room ID from API key."""
        import hashlib
        return hashlib.sha256(api_key.encode()).hexdigest()[:8]

    async def connect(self):
        """Connect to cloud, request our stable room ID."""
        # Send: {"type": "connect", "room_id": "abc12345"}
        # Cloud validates API key, accepts room ID
        ...

    async def handle_requests(self):
        """Receive requests from cloud, forward to local app, return responses."""
        ...

    async def run_with_reconnect(self):
        """Main loop: connect, handle requests, reconnect on disconnect.

        Room URL is stable - guests never need to re-scan QR code.
        """
        backoff = 1
        max_backoff = 30

        while self.should_reconnect:
            try:
                await self.connect()
                backoff = 1  # Reset on successful connect
                await self.handle_requests()

            except Exception as e:
                logger.warning(f"Cloud connection lost: {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
```

### Configuration

**Cloud Service (Fly.io):**
```bash
# Set in Fly.io secrets
fly secrets set CLOUD_API_KEY=kb_...              # Fixed API key for MVP
```

**Device:**
```python
# Environment variables or config
KBOX_CLOUD_ENABLED=true                           # Enable cloud connection
KBOX_CLOUD_URL=wss://cloud.kbox.app/api/v1/connect
KBOX_CLOUD_API_KEY=kb_...                         # Same key as cloud service

# In config_manager.py editable_keys (optional)
"cloud_api_key"                                   # Can also be set via web UI
```

### Integration Points

- `device/main.py` - Initialize RelayClient as background task on startup
- `device/overlay.py` - Generate QR code with cloud URL
  - Room URL is derived from API key at startup, **never changes**
  - No dynamic updates needed
- `device/config_manager.py` - New config key for API key storage
- `shared/protocol.py` - Shared message types

### Handling Cloud Service Restarts

**What happens when Fly.io restarts the cloud service:**

1. Device WebSocket connection drops
2. Guests' HTTP requests to room URL get 503/504 (cloud is restarting)
3. Device automatically reconnects with exponential backoff
4. Device requests its **stable room ID** (derived from API key)
5. Cloud accepts the room ID (device is authenticated)
6. Room is re-established with same URL
7. Guests can retry their requests - QR code URL is unchanged

**Guest experience during restart:**
- Requests may fail briefly (seconds to ~1 minute)
- No need to re-scan QR code
- Retry/refresh works once cloud is back

**No persistence needed:**
- Room ID is derived from device, not stored in cloud
- Cloud only tracks currently-connected rooms in memory
- Room is recreated on reconnect with same ID

## Future Features (Not MVP)

The architecture supports adding:

### Persistence (When Needed)
- Add PostgreSQL for room/instance persistence
- Survive restarts without losing active rooms
- Track connection history

### Multi-User (When Opening to Others)
- Registration API
- Email verification
- Multiple instances/API keys
- Abuse monitoring

### Identity (Later)
- User accounts (OAuth: Google, GitHub)
- Persistent user IDs across sessions
- History sync to cloud

### Sharing (Later)
- Shareable session links
- "What did we sing last time?"
- Public/private song lists

### Cloud Processing (Later)
- Audio analysis (key detection, BPM)
- Vocal removal / karaoke track generation
- Features impractical on Raspberry Pi

## Implementation Plan

### Phase 1: Shared Protocol & Cloud Service

1. Create `shared/protocol.py` with message types
2. Create `cloud/` directory with FastAPI app
3. Set up Fly.io project (no database needed)
4. Implement WebSocket connection handler with fixed API key auth
5. Implement in-memory room management
6. Implement simple rate limiting
7. Implement HTTP proxy endpoints
8. Deploy and test with simple WebSocket client

**Estimated effort**: 2-3 days

### Phase 2: Device Integration

1. Create `device/relay_client.py` using shared protocol
   - Derive stable room ID from API key
   - Implement reconnection logic with exponential backoff
2. Integrate with `device/main.py` startup (background task)
3. Update `device/overlay.py` to use cloud room URL
   - Room URL is stable (derived from API key), set once at startup
4. Add configuration options (API key, enable/disable)
5. Test end-to-end locally
6. Test reconnection scenarios (restart cloud service, simulate network issues)

**Estimated effort**: 2-3 days

### Phase 3: Real-World Testing

1. Use at actual karaoke sessions
2. Fix issues that emerge
3. Tune reconnection logic, error handling
4. Validate latency is acceptable

**Estimated effort**: Ongoing over several sessions

### Future: Multi-User (if/when needed)

1. Add registration API in `cloud/routes/instances.py`
2. Add email verification
3. Add abuse monitoring
4. Open to other users

**Total MVP**: ~1 week for working single-user implementation

## Success Criteria (MVP)

1. Guest can scan QR code and access device from any network (cellular, different WiFi, etc.)
2. Works reliably through a full karaoke session (3-4 hours)
3. Latency is acceptable for the use case (<500ms for API calls)
4. **Cloud service restarts are invisible to guests** - Same room URL works after reconnect
5. **Device reconnects automatically** - Exponential backoff, stable room ID
6. Architecture supports future features without major refactoring

### Cloud Service Reliability Expectations

Fly.io may restart the service for:
- Deployments
- Resource management
- Platform maintenance

**Device behavior:**
- Automatic reconnection with exponential backoff
- Requests same room ID on reconnect (derived from API key)
- QR code URL is stable, never changes

**Guest behavior during restart:**
- Requests may fail briefly (503/504)
- Retry/refresh works once cloud is back
- No need to re-scan QR code
