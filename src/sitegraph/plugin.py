from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .model import SiteDefinition, SitePackage


@runtime_checkable
class CrawlPlugin(Protocol):
    def __call__(
        self,
        *,
        definition: SiteDefinition,
        config: dict[str, Any],
        output_path: Path,
        dry_run: bool,
        incremental: bool,
    ) -> SitePackage | None: ...


def load_crawl_plugin(reference: str) -> CrawlPlugin:
    module_name, separator, function_name = reference.partition(":")
    if not separator or not module_name or not function_name:
        raise ValueError(f"invalid crawl plugin reference: {reference}")
    plugin = getattr(importlib.import_module(module_name), function_name)
    if not callable(plugin):
        raise TypeError(f"crawl plugin is not callable: {reference}")
    return plugin
