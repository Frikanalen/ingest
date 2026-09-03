#!/usr/bin/env python3
"""Queue the videos whose duration, frame rate or loudness the catalogue lacks.

All three fall out of the original, and anything uploaded before ingest knew to
record them has them empty -- frame rate everywhere, because until recently
nothing could write it. A worker re-derives them from the archived source.

Loudness alone is not a reason to queue anything, and this will not do it. A
silent track has no measurement to record, so `integrated_lufs` stays NULL
however many times it is measured, and a run keyed on that column would fetch
and decode the same originals for ever and write nothing each time. It is
measured where the original is being fetched anyway, and reported otherwise.
"""

import sys
from pathlib import Path

# Run from a checkout: the rule for what counts as missing metadata is the
# chore the worker runs, read from here rather than copied.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fk_queue.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], prog="scan-metadata.py", description=__doc__, chore="metadata"))
