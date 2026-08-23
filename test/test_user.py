"""
Unit tests for UserManager.
"""

import os
import tempfile

import pytest

from kbox.database import Database
from kbox.user import UserManager


@pytest.fixture
def temp_db():
    """Create a temporary database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(db_path=path)
    yield db
    db.close()
    os.unlink(path)


@pytest.fixture
def user_manager(temp_db):
    """Create a UserManager instance."""
    return UserManager(temp_db)


class TestUserManager:
    """Tests for UserManager."""

    def test_create_new_user(self, user_manager):
        """get_or_create_user creates a new user when not exists."""
        user = user_manager.get_or_create_user("user-123", "Alice")

        assert user.id == "user-123"
        assert user.display_name == "Alice"
        assert user.created_at is not None

    def test_get_existing_user(self, user_manager):
        """get_or_create_user returns existing user."""
        user1 = user_manager.get_or_create_user("user-123", "Alice")
        user2 = user_manager.get_or_create_user("user-123", "Alice")

        assert user1.id == user2.id
        assert user1.display_name == user2.display_name

    def test_update_display_name(self, user_manager):
        """get_or_create_user updates display_name when it changes."""
        user1 = user_manager.get_or_create_user("user-123", "Alice")
        assert user1.display_name == "Alice"

        user2 = user_manager.get_or_create_user("user-123", "Alice Smith")
        assert user2.display_name == "Alice Smith"
        assert user2.id == user1.id

    def test_get_user_exists(self, user_manager):
        """get_user returns existing user."""
        user_manager.get_or_create_user("user-123", "Alice")

        user = user_manager.get_user("user-123")
        assert user is not None
        assert user.id == "user-123"
        assert user.display_name == "Alice"

    def test_get_user_not_exists(self, user_manager):
        """get_user returns None for non-existent user."""
        user = user_manager.get_user("nonexistent-user")
        assert user is None

    def test_multiple_users(self, user_manager):
        """Multiple users can be created and retrieved."""
        alice = user_manager.get_or_create_user("alice-id", "Alice")
        bob = user_manager.get_or_create_user("bob-id", "Bob")
        charlie = user_manager.get_or_create_user("charlie-id", "Charlie")

        assert alice.display_name == "Alice"
        assert bob.display_name == "Bob"
        assert charlie.display_name == "Charlie"

        # Verify all can be retrieved
        assert user_manager.get_user("alice-id").display_name == "Alice"
        assert user_manager.get_user("bob-id").display_name == "Bob"
        assert user_manager.get_user("charlie-id").display_name == "Charlie"

    def test_user_persistence(self, temp_db):
        """Users persist across UserManager instances."""
        # Create user with first manager
        manager1 = UserManager(temp_db)
        manager1.get_or_create_user("user-123", "Alice")

        # Retrieve with new manager instance
        manager2 = UserManager(temp_db)
        user = manager2.get_user("user-123")

        assert user is not None
        assert user.display_name == "Alice"


class TestLookupCandidates:
    """Tests for name-keyed identity lookup (recognition flow)."""

    def test_no_candidates_for_unknown_name(self, user_manager):
        assert user_manager.lookup_candidates("Nobody") == []

    def test_finds_exact_match(self, user_manager):
        user_manager.get_or_create_user("user-123", "Vlad")

        candidates = user_manager.lookup_candidates("Vlad")

        assert [c.id for c in candidates] == ["user-123"]

    def test_matches_case_and_whitespace_insensitively(self, user_manager):
        """'Matt', 'matt', and 'Matt ' all resolve to the same identity."""
        user_manager.get_or_create_user("user-123", "Matt")

        for typed in ("matt", " Matt", "MATT", "Matt  "):
            candidates = user_manager.lookup_candidates(typed)
            assert [c.id for c in candidates] == ["user-123"], typed

    def test_returns_all_collisions(self, user_manager):
        """Two different people can share a name — both are real candidates."""
        user_manager.get_or_create_user("mike-1", "Mike")
        user_manager.get_or_create_user("mike-2", "Mike")

        candidates = user_manager.lookup_candidates("Mike")

        assert {c.id for c in candidates} == {"mike-1", "mike-2"}

    def test_does_not_match_a_different_name(self, user_manager):
        user_manager.get_or_create_user("user-123", "Vlad")
        assert user_manager.lookup_candidates("Lessa") == []

    def test_ordered_most_recently_seen_first(self, user_manager):
        user_manager.get_or_create_user("mike-1", "Mike")
        user_manager.get_or_create_user("mike-2", "Mike")

        # mike-1 is seen again, so should sort first despite being created first
        user_manager.touch_last_seen("mike-1")

        candidates = user_manager.lookup_candidates("Mike")
        assert candidates[0].id == "mike-1"

    def test_created_user_has_normalized_name_and_default_avatar(self, user_manager):
        user = user_manager.get_or_create_user("user-123", "  Vlad  ")

        assert user.normalized_name == "vlad"
        assert user.icon
        assert user.color

    def test_avatar_is_deterministic_for_same_id(self, user_manager, temp_db):
        user1 = user_manager.get_or_create_user("same-id", "Vlad")
        # Recreate against a fresh in-memory record of the same id to confirm
        # the avatar is derived from the id, not randomly assigned.
        fetched = UserManager(temp_db).get_user("same-id")

        assert fetched.icon == user1.icon
        assert fetched.color == user1.color


class TestTouchLastSeen:
    def test_sets_last_seen_at(self, user_manager):
        user = user_manager.get_or_create_user("user-123", "Vlad")
        assert user.last_seen_at is not None  # set at creation time too

        user_manager.touch_last_seen("user-123")
        refreshed = user_manager.get_user("user-123")
        assert refreshed.last_seen_at is not None


class TestMergeUsers:
    """Tests for the one-time operator-run ghost-identity merge (kbox/merge_users.py)."""

    def test_merge_deletes_the_merged_user(self, user_manager):
        user_manager.get_or_create_user("keep", "Vlad")
        user_manager.get_or_create_user("merge", "Vlad")

        user_manager.merge_users("keep", "merge")

        assert user_manager.get_user("keep") is not None
        assert user_manager.get_user("merge") is None

    def test_merge_moves_history_and_favorites(self, user_manager, temp_db):
        from kbox.database import FavoriteRepository, HistoryRepository, QueueRepository
        from kbox.models import SongMetadata, SongSettings

        user_manager.get_or_create_user("keep", "Vlad")
        merge_user = user_manager.get_or_create_user("merge", "Vlad")

        history_repo = HistoryRepository(temp_db)
        history_repo.record(
            user_id="merge",
            user_name="Vlad",
            video_id="youtube:abc",
            metadata=SongMetadata(title="Take On Me"),
            settings=SongSettings(),
            performance={},
        )
        favorites_repo = FavoriteRepository(temp_db)
        favorites_repo.add("merge", "youtube:xyz", SongMetadata(title="Africa"))

        queue_repo = QueueRepository(temp_db)
        queue_repo.add(
            user=merge_user,
            video_id="youtube:qqq",
            metadata=SongMetadata(title="Africa"),
            settings=SongSettings(),
        )

        user_manager.merge_users("keep", "merge")

        assert [h.user_id for h in history_repo.get_user_history("keep")] == ["keep"]
        assert [f.video_id for f in favorites_repo.get_user_favorites("keep")] == ["youtube:xyz"]
        assert [q.user_id for q in queue_repo.get_all()] == ["keep"]
        assert history_repo.get_user_history("merge") == []
        assert favorites_repo.get_user_favorites("merge") == []

    def test_merge_drops_duplicate_favorite_instead_of_failing(self, user_manager, temp_db):
        """Same song favorited under both identities: keep wins, no crash."""
        from kbox.database import FavoriteRepository
        from kbox.models import SongMetadata

        user_manager.get_or_create_user("keep", "Vlad")
        user_manager.get_or_create_user("merge", "Vlad")

        favorites_repo = FavoriteRepository(temp_db)
        favorites_repo.add("keep", "youtube:xyz", SongMetadata(title="Africa (keep's copy)"))
        favorites_repo.add("merge", "youtube:xyz", SongMetadata(title="Africa (merge's copy)"))

        user_manager.merge_users("keep", "merge")

        favorites = favorites_repo.get_user_favorites("keep")
        assert [f.video_id for f in favorites] == ["youtube:xyz"]
        assert favorites[0].metadata.title == "Africa (keep's copy)"

    def test_merge_rejects_same_id(self, user_manager):
        user_manager.get_or_create_user("user-123", "Vlad")
        with pytest.raises(ValueError):
            user_manager.merge_users("user-123", "user-123")

    def test_merge_rejects_unknown_keep_id(self, user_manager):
        user_manager.get_or_create_user("merge", "Vlad")
        with pytest.raises(ValueError):
            user_manager.merge_users("ghost", "merge")

    def test_merge_rejects_unknown_merge_id(self, user_manager):
        user_manager.get_or_create_user("keep", "Vlad")
        with pytest.raises(ValueError):
            user_manager.merge_users("keep", "ghost")
