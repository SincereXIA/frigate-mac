"""Migration and resolution helpers for persisted media paths."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from playhouse.sqlite_ext import SqliteExtDatabase

from frigate.runtime.paths import DEFAULT_MEDIA_DIR

_PATH_COLUMNS = (
    ("recordings", "path"),
    ("previews", "path"),
    ("reviewsegment", "thumb_path"),
    ("export", "video_path"),
    ("export", "thumb_path"),
)


@dataclass(frozen=True, slots=True)
class MediaPathMigrationResult:
    """Counts returned by a persisted media path migration."""

    candidates: int = 0
    migrated: int = 0
    conflicts: int = 0
    backup_created: bool = False


def resolve_media_path(
    path: str | Path,
    media_dir: str | Path,
    legacy_media_dir: str | Path = DEFAULT_MEDIA_DIR,
) -> Path:
    """Map a persisted legacy media path to the active media directory."""
    value = Path(path)
    legacy_root = Path(legacy_media_dir)
    active_root = Path(media_dir)
    if active_root == legacy_root:
        return value

    try:
        relative_path = value.relative_to(legacy_root)
    except ValueError:
        return value
    return active_root / relative_path


def migrate_legacy_media_paths(
    database: SqliteExtDatabase,
    media_dir: str | Path,
    backup_path: str | Path | None = None,
) -> MediaPathMigrationResult:
    """Transactionally migrate known container media paths in SQLite."""
    legacy_root = DEFAULT_MEDIA_DIR.rstrip("/")
    active_root = str(Path(media_dir)).rstrip("/")
    if active_root == legacy_root:
        return MediaPathMigrationResult()

    existing_tables = {
        row[0]
        for row in database.execute_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    candidates_by_column: list[tuple[str, str, int]] = []
    for table, column in _PATH_COLUMNS:
        if table not in existing_tables:
            continue
        count = database.execute_sql(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = ? OR "{column}" LIKE ?',
            (legacy_root, f"{legacy_root}/%"),
        ).fetchone()[0]
        if count:
            candidates_by_column.append((table, column, count))

    candidates = sum(item[2] for item in candidates_by_column)
    if not candidates:
        return MediaPathMigrationResult()

    backup_created = False
    if backup_path is not None:
        resolved_backup_path = Path(backup_path)
        if not resolved_backup_path.exists():
            resolved_backup_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(resolved_backup_path) as backup_database:
                database.connection().backup(backup_database)
            backup_created = True

    migrated = 0
    with database.atomic():
        for table, column, _ in candidates_by_column:
            cursor = database.execute_sql(
                f'UPDATE OR IGNORE "{table}" '
                f'SET "{column}" = ? || substr("{column}", ?) '
                f'WHERE "{column}" = ? OR "{column}" LIKE ?',
                (
                    active_root,
                    len(legacy_root) + 1,
                    legacy_root,
                    f"{legacy_root}/%",
                ),
            )
            migrated += max(cursor.rowcount, 0)

    return MediaPathMigrationResult(
        candidates=candidates,
        migrated=migrated,
        conflicts=candidates - migrated,
        backup_created=backup_created,
    )
