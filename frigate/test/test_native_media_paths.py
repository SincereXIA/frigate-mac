"""Tests for persisted media path migration."""

import tempfile
import unittest
from pathlib import Path

from playhouse.sqlite_ext import SqliteExtDatabase

from frigate.runtime.media_paths import (
    migrate_legacy_media_paths,
    resolve_media_path,
)


class TestNativeMediaPaths(unittest.TestCase):
    """Validate legacy container path handling for the native runtime."""

    def test_resolve_media_path_maps_only_the_legacy_root(self) -> None:
        self.assertEqual(
            resolve_media_path(
                "/media/frigate/recordings/example.mp4", "/Volumes/camera"
            ),
            Path("/Volumes/camera/recordings/example.mp4"),
        )
        self.assertEqual(
            resolve_media_path("/other/example.mp4", "/Volumes/camera"),
            Path("/other/example.mp4"),
        )

    def test_migration_is_transactional_and_preserves_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = SqliteExtDatabase(root / "frigate.db")
            database.execute_sql(
                "CREATE TABLE recordings (id TEXT PRIMARY KEY, path TEXT UNIQUE)"
            )
            database.execute_sql(
                "INSERT INTO recordings VALUES (?, ?)",
                ("legacy", "/media/frigate/recordings/legacy.mp4"),
            )
            database.execute_sql(
                "INSERT INTO recordings VALUES (?, ?)",
                ("conflict", "/media/frigate/recordings/conflict.mp4"),
            )
            database.execute_sql(
                "INSERT INTO recordings VALUES (?, ?)",
                ("native", "/Volumes/camera/recordings/conflict.mp4"),
            )

            backup_path = root / "backup.db"
            result = migrate_legacy_media_paths(
                database,
                "/Volumes/camera",
                backup_path,
            )

            self.assertEqual(result.candidates, 2)
            self.assertEqual(result.migrated, 1)
            self.assertEqual(result.conflicts, 1)
            self.assertTrue(result.backup_created)
            self.assertEqual(
                database.execute_sql(
                    "SELECT path FROM recordings WHERE id = 'legacy'"
                ).fetchone()[0],
                "/Volumes/camera/recordings/legacy.mp4",
            )
            self.assertEqual(
                database.execute_sql(
                    "SELECT path FROM recordings WHERE id = 'conflict'"
                ).fetchone()[0],
                "/media/frigate/recordings/conflict.mp4",
            )

            backup_database = SqliteExtDatabase(backup_path)
            self.assertEqual(
                backup_database.execute_sql(
                    "SELECT path FROM recordings WHERE id = 'legacy'"
                ).fetchone()[0],
                "/media/frigate/recordings/legacy.mp4",
            )
            backup_database.close()
            database.close()
