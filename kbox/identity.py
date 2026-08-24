"""
Pure helpers for name-keyed guest identity.

Name normalization (for matching a typed name against existing identities)
and the deterministic default icon/color (for telling same-named guests
apart in a recognition list). No dependencies on the rest of the app so it
can be imported from database.py, user.py, and server.py without creating
import cycles.

See ldocs/GUEST_IDENTITY_CONTINUITY.md (product/UX design) and
ldocs/GUEST_IDENTITY_TECHNICAL_DESIGN.md (this implementation) for context.
"""

import hashlib
from typing import Tuple


def normalize_name(display_name: str) -> str:
    """Trim, collapse internal whitespace, and case-fold a display name.

    Used as the lookup key so "Matt", "matt", and "Matt " all resolve to the
    same identity. The name a guest actually typed is still what's stored
    and displayed in `display_name` — normalization only affects matching.
    """
    return " ".join(display_name.split()).casefold()


# A guest never picks from this directly today (no UI for it yet); it's the
# deterministic default assigned at identity-creation time, and the pool a
# future self-chosen picker would offer. Order is arbitrary but should stay
# stable — reordering changes everyone's default avatar.
AVATAR_PALETTE: Tuple[Tuple[str, str], ...] = (
    ("🦊", "#e67e22"),
    ("🐼", "#2ecc71"),
    ("🐙", "#9b59b6"),
    ("🦉", "#3498db"),
    ("🐨", "#95a5a6"),
    ("🦋", "#e84393"),
    ("🐳", "#0984e3"),
    ("🦁", "#f1c40f"),
    ("🐢", "#16a085"),
    ("🐧", "#2c3e50"),
    ("🦄", "#d980fa"),
    ("🐝", "#f9ca24"),
)


def pick_avatar(user_id: str) -> Tuple[str, str]:
    """Deterministic default (icon, color) pair, derived from the user's UUID.

    Every identity gets one at creation time, not only ones that turn out to
    collide with another name — this keeps recognition-list rendering simple
    (never a blank slot) and makes the one-time migration of existing rows
    trivial (same function, no special-casing "created before this existed").
    """
    idx = int(hashlib.sha1(user_id.encode()).hexdigest(), 16) % len(AVATAR_PALETTE)
    return AVATAR_PALETTE[idx]
