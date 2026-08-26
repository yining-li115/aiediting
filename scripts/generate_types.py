"""Emit TypeScript types from the Pydantic specs.

The specs are the contract between every part of this system, and the frontend
is no exception. Hand-written TypeScript mirroring them drifts silently: a
renamed field breaks at runtime in the browser, weeks after the rename, with
an error that points at the wrong place.

Two sources, deliberately:

* the **domain specs** (EditPlanSpec, TimelineSpec, MaterialSegment ...) are
  the real contract and change more often than the endpoints;
* the **OpenAPI schema** covers request and response shapes for the HTTP
  surface, which is thin today and will grow.

Run after changing anything in packages/aive/specs/:

    uv run python scripts/generate_types.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from aive.specs import (  # noqa: E402
    Asset,
    EditOperation,
    EditPlanSpec,
    EditScope,
    MaterialSegment,
    ProjectVersion,
    ReferenceAnalysis,
    TimelineSpec,
)

OUT_DIR = Path("apps/web/src/lib/api")
SCHEMA_PATH = OUT_DIR / "schema.json"
TYPES_PATH = OUT_DIR / "types.ts"

# Everything the frontend is allowed to know about. Adding a model here is a
# deliberate act: a type the UI can see is a type the UI will start depending
# on, and the ones left out are internal for a reason.
EXPORTED = [
    EditPlanSpec,
    TimelineSpec,
    MaterialSegment,
    Asset,
    ReferenceAnalysis,
    EditOperation,
    EditScope,
    ProjectVersion,
]


def build_schema() -> dict:
    """One JSON Schema document with every exported model under $defs."""
    definitions: dict = {}
    roots: dict = {}

    for model in EXPORTED:
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
        definitions.update(schema.pop("$defs", {}))
        roots[model.__name__] = schema

    definitions.update(roots)
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "AiveSpecs",
        "type": "object",
        "properties": {name: {"$ref": f"#/$defs/{name}"} for name in roots},
        "$defs": definitions,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(
        json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {SCHEMA_PATH}")

    banner = (
        "/* Generated from packages/aive/specs by scripts/generate_types.py.\n"
        " * Do not edit: run `npm run types` after changing a spec. */\n\n"
    )
    result = subprocess.run(
        [
            "npx", "--yes", "json-schema-to-typescript@15",
            str(SCHEMA_PATH),
            "--no-additionalProperties",
            "--unreachableDefinitions",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr[:2000], file=sys.stderr)
        print(
            "\ntype generation failed. The JSON schema was still written, so "
            "this is a tooling problem rather than a spec problem.",
            file=sys.stderr,
        )
        return 1

    TYPES_PATH.write_text(banner + result.stdout, encoding="utf-8")
    print(f"wrote {TYPES_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
