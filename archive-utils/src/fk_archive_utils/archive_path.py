"""The shape of a path these tools will accept.

This is the whole point of the package. The ingest engine hands over strings
it built from a video id, a format name and a filename that a member chose,
and the storage host has to decide whether they describe somewhere it is
willing to write. Everything downstream of here traverses the archive with
openat(2) and O_NOFOLLOW, so this module is not the only thing standing
between a hostile string and the filesystem -- but it is the one that says
what the archive's namespace actually is, and a path that does not fit it is
refused before anything is opened at all.

The namespace, in full:

    <video-id>/original/<file>      the source file, exactly one per video
    <video-id>/images/<file>        editorial stills, registered separately
    <video-id>/<variant>/<file>     everything derived from the source
    .spool/                         where a publish stages, never published to
    .trash/<stamp>/<path>           where a removal goes, never published to

Depth is fixed at three for anything that names a file, which is what makes
"a filename, not a path" enforceable rather than aspirational: there is no
valid destination with four components, so a caller cannot describe one.
"""

import re
from dataclasses import dataclass

from fk_archive_utils.errors import UsageError

#: Where a publish stages its bytes before it links them into the published
#: tree. Inside the archive root because linking cannot cross a filesystem
#: boundary, and named with a leading dot so the component rules below exclude
#: it from every path a caller can name.
SPOOL_DIR = ".spool"

#: Where anything taken out of the published tree is parked. A rename rather
#: than a delete: an hour of deciding the rule was wrong costs a rename back
#: rather than a restore from backup.
TRASH_DIR = ".trash"

#: Directories the archive keeps for itself. Both start with a dot, so
#: `_check_component` already refuses them; listed anyway because the rule
#: that protects them should be visible where they are named.
RESERVED = frozenset({SPOOL_DIR, TRASH_DIR})

#: A video id as django-api issues them, and as ingest checks them before it
#: builds a path. Leading zeros are refused rather than normalised: `007` and
#: `7` would otherwise be two directories for one video, and nothing that
#: walks the archive would know they were the same.
VIDEO_ID = re.compile(r"\A(?:0|[1-9][0-9]{0,17})\Z")

#: The longest a single path component may be. ext4 and ZFS both stop at 255
#: bytes; checked here so the refusal names the problem rather than arriving
#: as ENAMETOOLONG from somewhere in the middle of an operation.
MAX_COMPONENT_BYTES = 255


def _check_component(component: str, *, what: str) -> str:
    """Refuse anything that must not become one component of an archive path.

    Deliberately not a charset allowlist. The filenames already in the archive
    were written by a system that predates every rule ingest has, and include
    spaces and Norwegian letters; a rule strict enough to be comfortable would
    refuse to migrate or rebuild exactly those videos. What is refused instead
    is everything that makes a component mean something other than a name:
    separators, the two directory aliases, control characters, and a leading
    dot -- which is also what keeps `.spool` and `.trash` unnameable.
    """
    if not component:
        raise UsageError(f"{what} must not be empty")
    if len(component.encode("utf-8")) > MAX_COMPONENT_BYTES:
        raise UsageError(f"{what} is longer than {MAX_COMPONENT_BYTES} bytes: {component!r}")
    if "/" in component:
        raise UsageError(f"{what} must be a single name, not a path: {component!r}")
    if component in (".", ".."):
        raise UsageError(f"{what} must name something, not a directory alias: {component!r}")
    if component.startswith("."):
        raise UsageError(f"{what} must not start with a dot: {component!r}")
    if any(character < " " or character == "\x7f" for character in component):
        raise UsageError(f"{what} contains a control character: {component!r}")
    if component != component.strip():
        raise UsageError(f"{what} must not begin or end with whitespace: {component!r}")
    if component in RESERVED:
        raise UsageError(f"{what} is reserved by the archive: {component!r}")
    return component


@dataclass(frozen=True)
class ArchivePath:
    """An archive-relative path that has been through the grammar above.

    Constructed only by the parse functions, so a value of this type having
    reached an operation means the string it came from was checked -- rather
    than every operation having to remember to check it again.
    """

    parts: tuple[str, ...]

    @property
    def video_id(self) -> str:
        return self.parts[0]

    @property
    def parent(self) -> tuple[str, ...]:
        return self.parts[:-1]

    @property
    def name(self) -> str:
        return self.parts[-1]

    def __str__(self) -> str:
        return "/".join(self.parts)


def _split(raw: str, *, what: str) -> tuple[str, ...]:
    if not raw:
        raise UsageError(f"{what} must not be empty")
    if raw.startswith("/"):
        raise UsageError(f"{what} must be relative to the archive root: {raw!r}")
    if "\\" in raw:
        # Not a separator here, but a backslash in a path this system built
        # means something upstream escaped a string it should have passed on
        # whole, and publishing under the mangled name would hide that.
        raise UsageError(f"{what} must not contain a backslash: {raw!r}")

    parts = tuple(raw.split("/"))
    if not VIDEO_ID.match(parts[0]):
        raise UsageError(f"{what} must start with a video id: {parts[0]!r}")
    for part in parts[1:]:
        _check_component(part, what=what)
    return parts


def parse_file_path(raw: str, *, what: str = "path") -> ArchivePath:
    """Parse `<video-id>/<category>/<filename>`, and nothing else.

    The one shape a file may occupy. Used for both ends of a publish and both
    ends of a move, which is what makes "the archive holds files three deep"
    a property of the tools rather than of the callers.
    """
    parts = _split(raw, what=what)
    if len(parts) != 3:
        raise UsageError(f"{what} must be <video-id>/<category>/<filename>, got {raw!r}")
    return ArchivePath(parts)


def parse_removable_path(raw: str, *, what: str = "path") -> ArchivePath:
    """Parse what may be trashed: a whole video, or one directory inside one.

    Those are the only two things anything asks to remove. A video is
    collected whole when the catalogue no longer has a row for it; a directory
    goes when an upload supersedes the media under it or a format is rebuilt.
    Nothing has ever needed to trash a single file, so nothing may.
    """
    parts = _split(raw, what=what)
    if len(parts) not in (1, 2):
        raise UsageError(f"{what} must be <video-id> or <video-id>/<category>, got {raw!r}")
    return ArchivePath(parts)


def parse_variant_path(video_id: str, variant: str) -> ArchivePath:
    """Parse `<video-id>/<variant>` from two separate arguments.

    Taking two components rather than one path makes "exactly one variant of
    exactly one video" structural: the caller has no syntax with which to name
    a whole video, a file inside a variant, or anything outside that shape.
    This is grammar only; operations decide which valid variants are deletable.
    """
    if not VIDEO_ID.match(video_id):
        raise UsageError(f"video id is malformed: {video_id!r}")
    _check_component(variant, what="variant")
    return ArchivePath((video_id, variant))
