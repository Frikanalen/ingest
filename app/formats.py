"""What the archive is supposed to hold for every video.

Kept apart from the upload handler because it is a statement about the
catalogue rather than about ingest: a fresh upload and a backfill of something
archived years ago both converge on this list, and a list that differed between
the two paths is how the archive drifted in the first place.
"""

from functools import lru_cache

from app.django_client.service import FormatEnum
from app.media.comand_template import TemplatedCommandGenerator

#: Produced for every video, from its original.
DESIRED_FORMATS = (
    FormatEnum.LARGE_THUMB,
    FormatEnum.MED_THUMB,
    FormatEnum.SMALL_THUMB,
    FormatEnum.DASH,
)

#: What a file registered before profiles carried revisions reads as. No
#: template may claim it -- ProfileMetadata numbers revisions from 1 -- so it
#: means "produced before we recorded this", and therefore "rebuild".
UNTRACKED_REVISION = 0


@lru_cache
def current_revision(file_format: FormatEnum) -> int:
    """Which revision of `file_format` the shipped template currently is.

    Cached because it reads the template off disk, and a catalogue-wide plan
    asks the same handful of questions once per video.
    """
    return TemplatedCommandGenerator(str(file_format)).metadata.revision
