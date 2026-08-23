"""What the archive is supposed to hold for every video.

Kept apart from the upload handler because it is a statement about the
catalogue rather than about ingest: a fresh upload and a backfill of something
archived years ago both converge on this list, and a list that differed between
the two paths is how the archive drifted in the first place.
"""

from app.django_client.service import FormatEnum

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

