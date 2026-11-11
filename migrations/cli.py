"""`mgm` command line: create a migration or apply pending ones."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from pymongo import MongoClient

from app.core.config import get_settings
from app.core.logging import configure_logging
from migrations.runner import MigrationError, apply_migrations, create_migration


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mgm", description="Versioned MongoDB migrations")
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="create the next migration file")
    new.add_argument("name", help="short description, e.g. add_tenants_index")
    commands.add_parser("migrate", help="apply every pending migration")

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    if args.command == "new":
        try:
            path = create_migration(args.name)
        except MigrationError as exc:
            print(f"mgm: {exc}", file=sys.stderr)
            return 1
        print(f"Created {path}")
        return 0

    client: MongoClient[dict[str, object]] = MongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
        tz_aware=True,
    )
    try:
        applied = apply_migrations(client[settings.mongodb_database])
    except MigrationError as exc:
        print(f"mgm: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()

    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("No pending migrations")
    return 0
