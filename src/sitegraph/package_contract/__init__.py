from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> jsonschema.protocols.Validator:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding='utf-8'))
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema)


def validate_schema_file(schema_name: str, payload: dict) -> None:
    _validator(schema_name).validate(payload)
