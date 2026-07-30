from __future__ import annotations

import yaml
from pathlib import Path

from .model import SiteDefinition


def load_yaml(path: str | Path) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def load_site_definition(path: str | Path) -> SiteDefinition:
    return SiteDefinition.from_config(load_yaml(path))
