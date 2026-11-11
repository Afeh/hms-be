"""Discovery and application of versioned migrations.

Migrations are applied in revision order and recorded in one tracking
collection. A revision is recorded only after ``upgrade`` returns, so a failing
migration never leaves a partially applied revision behind. Every migration runs
inside `run_as_system` and logs both the revision and the reason.
"""

from __future__ import annotations

import importlib.util
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from pymongo.database import Database

from app.db.tenancy import run_as_system

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent
TRACKING_COLLECTION = "_migrations"
REVISION_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.py$")
_MIGRATION_TEMPLATE = '''"""{description}."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database


def upgrade(db: Database[dict[str, Any]]) -> None:
    """Apply the migration. Forward-only: a correction is a new revision."""
'''


class MigrationError(RuntimeError):
    """Raised when migrations cannot be discovered or applied."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One migration module on disk."""

    revision: str
    name: str
    path: Path

    @property
    def label(self) -> str:
        return f"{self.revision}_{self.name}"


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Return the migrations in `directory`, ordered by revision."""
    migrations: list[Migration] = []
    for path in sorted(directory.iterdir()):
        match = REVISION_PATTERN.match(path.name)
        if match:
            migrations.append(Migration(match.group(1), match.group(2), path))

    revisions = [migration.revision for migration in migrations]
    duplicates = {revision for revision in revisions if revisions.count(revision) > 1}
    if duplicates:
        raise MigrationError(f"duplicate migration revisions: {', '.join(sorted(duplicates))}")
    return sorted(migrations, key=lambda migration: migration.revision)


def applied_revisions(db: Database[dict[str, Any]]) -> set[str]:
    """Revisions the database has already recorded."""
    documents = db[TRACKING_COLLECTION].find({}, {"revision": 1})
    return {str(document["revision"]) for document in documents if "revision" in document}


def pending(
    db: Database[dict[str, Any]], migrations: Sequence[Migration] | None = None
) -> list[Migration]:
    """Migrations that are on disk but not recorded, oldest first."""
    migrations = list(migrations) if migrations is not None else discover()
    known = {migration.revision for migration in migrations}
    unknown = applied_revisions(db) - known
    if unknown:
        raise MigrationError(
            "database has migrations missing from this tree: " + ", ".join(sorted(unknown))
        )
    recorded = applied_revisions(db)
    return [migration for migration in migrations if migration.revision not in recorded]


def apply_migrations(
    db: Database[dict[str, Any]],
    migrations: Sequence[Migration] | None = None,
    *,
    reason: str = "mgm migrate",
) -> list[str]:
    """Apply every pending migration; return the revisions applied now."""
    applied_now: list[str] = []
    for migration in pending(db, migrations):
        with run_as_system(f"{reason}: {migration.label}"):
            logger.info(
                "applying migration",
                extra={"revision": migration.label, "reason": reason},
            )
            try:
                _load(migration).upgrade(db)
            except Exception as exc:
                logger.error(
                    "migration failed",
                    extra={"revision": migration.label, "reason": reason},
                    exc_info=exc,
                )
                raise MigrationError(f"migration {migration.label} failed: {exc}") from exc
            db[TRACKING_COLLECTION].insert_one(
                {
                    "revision": migration.revision,
                    "name": migration.name,
                    "applied_at": datetime.now(UTC),
                    "reason": reason,
                }
            )
        logger.info(
            "migration applied",
            extra={"revision": migration.label, "reason": reason},
        )
        applied_now.append(migration.revision)
    return applied_now


def create_migration(name: str, directory: Path = MIGRATIONS_DIR) -> Path:
    """Create the next revision's file from the template and return its path."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    if not slug:
        raise MigrationError("a migration needs a name made of letters or digits")

    revisions = [int(migration.revision) for migration in discover(directory)]
    revision = max(revisions, default=0) + 1
    path = directory / f"{revision:04d}_{slug}.py"
    if path.exists():
        raise MigrationError(f"migration already exists: {path.name}")

    path.write_text(_MIGRATION_TEMPLATE.format(description=name.strip()), encoding="utf-8")
    return path


def _load(migration: Migration) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"migrations.{migration.label}", migration.path)
    if spec is None or spec.loader is None:
        raise MigrationError(f"cannot load migration {migration.label}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "upgrade", None)):
        raise MigrationError(f"migration {migration.label} has no upgrade(db) function")
    return module
