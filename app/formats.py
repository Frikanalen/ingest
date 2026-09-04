"""What the archive is supposed to hold for every video.

Kept apart from the upload handler because it is a statement about the
catalogue rather than about ingest: a fresh upload and a backfill of something
archived years ago both converge on this list, and a list that differed between
the two paths is how the archive drifted in the first place.
"""

from functools import lru_cache

from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.media.comand_template import TemplatedCommandGenerator

#: Produced for every video, from its original.
DESIRED_FORMATS = (
    VideoFileVariantEnum.LARGE_THUMB,
    VideoFileVariantEnum.MED_THUMB,
    VideoFileVariantEnum.SMALL_THUMB,
    VideoFileVariantEnum.DASH,
)

#: The stand-in published while the real ladder encodes: one low rendition and
#: the audio, encoded at many times realtime, so a member can watch their
#: upload within minutes of it arriving rather than hours. Deleted once `dash`
#: is registered -- see `app.converge.chores.produce_formats`.
#:
#: Deliberately **not** in DESIRED_FORMATS. That tuple says what a *converged*
#: video has, and is published at `/ingest-api/formats` so the queue side can
#: ask what is missing. A converged video has no preview, by construction, so
#: listing it there would make every finished video in the catalogue read as
#: incomplete forever.
#:
#: A plain string rather than a VideoFileVariantEnum member because django-api
#: has no such member yet. Everything downstream takes a str -- the template
#: lookup, the archive path -- and VideoFileVariantEnum is a str enum, so a
#: comparison against a registered row's variant is true either way. When the
#: schema gains it, this becomes VideoFileVariantEnum.DASH_PREVIEW and nothing
#: else here changes.
DASH_PREVIEW = "dash_preview"

#: What a file registered before profiles carried revisions reads as. No
#: template may claim it -- ProfileMetadata numbers revisions from 1 -- so it
#: means "produced before we recorded this", and therefore "rebuild".
UNTRACKED_REVISION = 0


@lru_cache
def current_revision(file_format: VideoFileVariantEnum) -> int:
    """Which revision of `file_format` the shipped template currently is.

    Cached because it reads the template off disk, and a catalogue-wide plan
    asks the same handful of questions once per video.
    """
    return TemplatedCommandGenerator(str(file_format)).metadata.revision
