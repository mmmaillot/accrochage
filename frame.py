#!/usr/bin/env python3
"""Tools for the Accrochage agent: research, prepare, pair and upload."""

import argparse
import json
from pathlib import Path
import sys

import artwork
import sources
import television


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    search = commands.add_parser("search", help="Find candidates in a public collection API")
    search.add_argument("provider", choices=["cleveland", "commons"])
    search.add_argument("query")
    search.add_argument("--limit", type=int, choices=range(1, 26), default=12, metavar="1..25")

    check = commands.add_parser("check-recipe", help="Validate rights fields and references")
    check.add_argument("recipe", type=Path)

    prepare = commands.add_parser("prepare", help="Export a recipe to a new work directory")
    prepare.add_argument("recipe", type=Path)
    prepare.add_argument("--out", required=True, type=Path)

    pair = commands.add_parser("pair", help="Remember the local TV after confirming its Art API")
    pair.add_argument("--host", required=True)

    push = commands.add_parser("push", help="Add a prepared collection to the paired TV")
    push.add_argument("folder", type=Path)
    resolution = push.add_mutually_exclusive_group()
    resolution.add_argument("--resolve-content-id", help="Record a verified TV ID for an earlier uncertain upload")
    resolution.add_argument("--retry-pending", action="store_true", help="Retry after verifying the earlier upload did not land")

    run = parser.parse_args()
    try:
        if run.command == "search":
            lookup = {"cleveland": sources.cleveland, "commons": sources.commons}[run.provider]
            sys.stdout.write(artwork.json_text(list(lookup(run.query, run.limit))))
        elif run.command == "check-recipe":
            recipe = json.loads(run.recipe.read_text(encoding="utf-8"))
            artwork.validated_recipe(recipe)
            print(f"Recipe valid: {recipe['title']} ({len(recipe['artworks'])} works)")
        elif run.command == "prepare":
            artwork.prepare(run.recipe.resolve(), run.out.resolve())
        elif run.command == "pair":
            television.pair(run.host)
        else:
            television.push(run.folder.resolve(), run.resolve_content_id, run.retry_pending)
    except (OSError, ValueError) as exc:
        print(f"Accrochage: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Accrochage: interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Device-library failures can include pairing data in the exception.
        print(f"Accrochage: {type(exc).__name__}. Check the TV, Art Mode and the local network if pairing or uploading.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
