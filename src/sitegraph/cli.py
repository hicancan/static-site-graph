from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_site_definition
from .crawl import crawl
from .package import write_site_package
from .extract import extract_nav_tree_from_homepage
from .fetch import fetch_html
from .util import now_iso, write_json


def validate_config(args: argparse.Namespace) -> None:
    try:
        definition = load_site_definition(args.config)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(f"OK config: {definition.id} {definition.base_url}")


def crawl_site(args: argparse.Namespace) -> None:
    definition = load_site_definition(args.config)
    output_path = Path(args.out or f".data/sites/{definition.id}/index")
    requested_incremental = bool(getattr(args, "incremental", False))
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "site_id": definition.id,
                    "base_url": definition.base_url,
                    "sections": len(definition.config.get("sections", [])),
                    "incremental": requested_incremental,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    package = crawl(
        definition,
        output_path=output_path,
        incremental=requested_incremental,
        incremental_known_page_stop=max(
            1,
            int(getattr(args, "incremental_known_page_stop", 1)),
        ),
        incremental_refresh_frontier=max(
            0,
            int(getattr(args, "incremental_refresh_frontier", 3)),
        ),
        fetch_html_fn=fetch_html,
    )
    totals = write_site_package(
        package,
        output_path,
        incremental=requested_incremental and output_path.exists(),
    )
    print(json.dumps(totals, ensure_ascii=False, indent=2))


def discover_homepage(args: argparse.Namespace) -> None:
    definition = load_site_definition(args.config)
    result = fetch_html(definition.base_url)
    if result.error or (
        result.status_code is not None and result.status_code >= 400
    ):
        raise SystemExit(result.error or f"HTTP {result.status_code}")
    nodes = extract_nav_tree_from_homepage(
        result.text,
        definition.base_url,
        definition.base_url,
        definition.id,
    )
    output = Path(
        args.out
        or f".data/sites/{definition.id}/index/nav_tree.json"
    )
    write_json(
        output,
        {
            "site_id": definition.id,
            "generated_at": now_iso(),
            "nodes": nodes,
        },
    )
    print(f"wrote {output} nodes={len(nodes)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sitegraph")
    commands = parser.add_subparsers(dest="cmd", required=True)

    command = commands.add_parser("validate-config")
    command.add_argument("config")
    command.set_defaults(func=validate_config)

    command = commands.add_parser("crawl-site")
    command.add_argument("config")
    command.add_argument("--out")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--incremental", action="store_true")
    command.add_argument("--incremental-known-page-stop", type=int, default=1)
    command.add_argument("--incremental-refresh-frontier", type=int, default=3)
    command.set_defaults(func=crawl_site)

    command = commands.add_parser("discover-homepage")
    command.add_argument("config")
    command.add_argument("--out")
    command.set_defaults(func=discover_homepage)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
