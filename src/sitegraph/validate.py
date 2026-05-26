from __future__ import annotations

import json
from pathlib import Path
import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parents[2] / 'schemas'


def validate_schema_file(schema_name: str, payload: dict) -> None:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding='utf-8'))
    jsonschema.validate(payload, schema)


def validate_jsonl(schema_name: str, path: Path) -> int:
    count = 0
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        validate_schema_file(schema_name, json.loads(line))
        count += 1
    return count
