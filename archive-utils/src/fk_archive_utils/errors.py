"""What can go wrong, and what the caller is told about it.

Every failure the ingest engine has to act on differently gets its own exit
code, because the only thing that survives an SSH command invocation is the
exit status and whatever was written to stderr. A client that had to parse
prose to tell "something is already there" apart from "the archive is full"
would be guessing.
"""


class ArchiveUtilsError(Exception):
    """Base class for every refusal these tools make deliberately."""

    exit_code = 1


class UsageError(ArchiveUtilsError):
    """The arguments do not describe an operation this tool performs.

    Covers a malformed path as well as a missing option: from the caller's
    side both mean "this invocation was wrong", and neither is retryable.
    """

    exit_code = 2


class AlreadyExists(ArchiveUtilsError):
    """Something already occupies the destination.

    Never overwritten. The published tree is exported read-only to the playout
    hosts, and a file replaced under a reader is worse than an operation that
    refuses.
    """

    exit_code = 3


class NotFound(ArchiveUtilsError):
    """The path the operation was asked to act on is not in the archive."""

    exit_code = 4


class TransferError(ArchiveUtilsError):
    """The bytes that arrived are not the bytes that were promised.

    Its own code because it is the one failure that is worth retrying
    immediately and unchanged: nothing in the archive was touched, and the
    same command may well succeed on a second attempt.
    """

    exit_code = 5


class CatalogueError(ArchiveUtilsError):
    """django-api could not be reached, or would not say what was asked.

    The two tools that compare the archive against the catalogue are the only
    operations here that are not confined to this host, so a failure that came
    from the far end is worth telling apart from one that came from the
    archive.

    `IncompleteCatalogue` is the subclass that matters: a partial answer is
    never treated as a complete one, because garbage collection reads absence
    as permission.
    """

    exit_code = 7

    #: HTTP status, when the failure came back as one. Lets a caller tell a
    #: 404 -- which for a video means "the catalogue has dropped it", an answer
    #: rather than a fault -- apart from everything else.
    status: int | None = None


class IncompleteCatalogue(CatalogueError):
    """The catalogue could not be read in full.

    Raised rather than returning what did arrive. The most destructive thing
    in this package -- deciding a video no longer exists and reclaiming its
    media -- reads absence from the catalogue as permission, so half a
    catalogue would make the archive look like garbage in exactly the
    proportion the read fell short by.
    """

    exit_code = 8


class ProfileError(ArchiveUtilsError):
    """The named archive profile is missing or unusable.

    A configuration fault on the storage host rather than anything the caller
    did, so it is worth telling apart from a usage error even though neither
    is retryable.
    """

    exit_code = 6
