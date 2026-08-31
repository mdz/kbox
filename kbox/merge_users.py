"""
Operator CLI for coalescing duplicate guest identities.

kbox never merges identities automatically (see
docs/development/guest-identity.md) — a mis-tap or "I'm new" guess on a
name collision permanently adds a ghost identity. This is the tool an
operator uses to fold one back into another, e.g. after confirming with a
regular that two records are really them.

Usage:
    uv run python -m kbox.merge_users --list "Vlad"
    uv run python -m kbox.merge_users <keep_id> <merge_id>
"""

import argparse
import sys

from .database import Database
from .user import UserManager


def _list_candidates(user_mgr: UserManager, name: str) -> None:
    candidates = user_mgr.lookup_candidates(name)
    if not candidates:
        print(f"No users found matching {name!r}")
        return
    for user in candidates:
        last_seen = user.last_seen_at.isoformat() if user.last_seen_at else "never"
        print(f"{user.id}  {user.display_name!r:20}  last seen {last_seen}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Path to kbox database (defaults to ~/.kbox/kbox.db)")
    parser.add_argument("--list", metavar="NAME", help="List user IDs matching a name")
    parser.add_argument("keep_id", nargs="?", help="UUID of the identity to keep")
    parser.add_argument("merge_id", nargs="?", help="UUID of the identity to fold in and remove")
    args = parser.parse_args(argv)

    database = Database(args.db)
    user_mgr = UserManager(database)

    if args.list:
        _list_candidates(user_mgr, args.list)
        return 0

    if not args.keep_id or not args.merge_id:
        parser.error("keep_id and merge_id are required unless --list is given")

    keep = user_mgr.get_user(args.keep_id)
    merge = user_mgr.get_user(args.merge_id)
    if not keep or not merge:
        print("Both keep_id and merge_id must be existing user IDs.", file=sys.stderr)
        return 1

    print(f"Merging {merge.display_name!r} ({merge.id}) into {keep.display_name!r} ({keep.id})")
    confirm = input("Proceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return 1

    user_mgr.merge_users(keep.id, merge.id)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
