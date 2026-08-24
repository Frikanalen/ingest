#!/usr/bin/env python3
"""Drop the paths ingest has no business calling from a schema snapshot.

openapi-python-client cannot represent a response body it has no way to parse,
and django-api serves the TV-Anytime feed as application/xml. It warns once per
status code per endpoint, and generate-client.sh runs with --fail-on-warning,
so leaving them in the snapshot fails the client generation that CI does before
building the image.

Pruning rather than relaxing --fail-on-warning: the flag is there to catch the
schema drifting away from something we can generate a client for, and that is
worth keeping for the endpoints ingest actually calls. If ingest ever needs one
of these, the fix is to make the endpoint describable upstream rather than to
add it back here.
"""

import sys
from pathlib import Path

import yaml

#: Prefixes of paths to leave out. Ingest talks to videos, videofiles and the
#: ingest queue; everything else in django-api belongs to the frontend or to
#: EPG consumers.
UNGENERATABLE_PREFIXES = ("/api/tvanytime",)


def prune(schema: dict) -> list[str]:
    dropped = [path for path in schema.get("paths", {}) if path.startswith(UNGENERATABLE_PREFIXES)]
    for path in dropped:
        del schema["paths"][path]
    return dropped


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else "schema.yaml")
    schema = yaml.safe_load(target.read_text())

    dropped = prune(schema)
    if dropped:
        target.write_text(yaml.safe_dump(schema, sort_keys=False, allow_unicode=True))

    for path in dropped:
        print(f"Pruned {path}: not generatable, and not ingest's to call")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
