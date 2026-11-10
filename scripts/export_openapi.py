"""Stable export of the OpenAPI contract.

Invoked from the project root by CI:

    uv run python scripts/export_openapi.py

Imports `create_app()` from `app.main`, calls `.openapi()`, and writes the
resulting schema to `openapi.json` at the repository root. The dump is
sorted, indented and newline-terminated, so the committed file is stable
across runs and diffable with `git diff --exit-code openapi.json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the repository root importable regardless of the current working
# directory (e.g. when `uv run python scripts/export_openapi.py` is used).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.main import create_app  # noqa: E402

ROOT = _ROOT
OUTPUT = ROOT / "openapi.json"


def main() -> int:
    app = create_app()
    schema = app.openapi()
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
