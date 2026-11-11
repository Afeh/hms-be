"""Versioned, forward-only MongoDB migrations.

The runner is exposed as `mgm`:

    uv run mgm new <name>
    uv run mgm migrate

Each migration is a module with ``def upgrade(db) -> None``. There is no
downgrade: a correction ships as a new revision.
"""
