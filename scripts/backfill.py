#!/usr/bin/env python3
"""Queue the videos whose derived formats are missing or built by an old profile.

What a video is supposed to have is declared by `DESIRED_FORMATS` and by the
revision each template in `app/templates/` carries, and a videofile row records
which revision produced it. So "this video has DASH" and "this video has
*current* DASH" are different questions, and this asks the second one of every
video in the catalogue.

Changing a profile is then: edit the template, bump its revision, run this.
Nothing keeps a list of what that invalidated.

Neither this nor the worker deletes anything. Rebuilding swaps the old
directory into `.trash/`, which is a rename, and purging it is a separate act
on the storage host.
"""

import sys
from pathlib import Path

# Run from a checkout: this reads `app.formats` for what a video should have,
# and the chores for what that means, rather than keeping a second copy of
# either. Run it from a checkout at the revision the workers are deployed at --
# a template this repository has moved past is one the pool cannot yet build,
# and the job would be claimed and found to have nothing in it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fk_queue.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], prog="backfill.py", description=__doc__, chore="formats"))
