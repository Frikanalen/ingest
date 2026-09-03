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


class ProfileError(ArchiveUtilsError):
    """The named archive profile is missing or unusable.

    A configuration fault on the storage host rather than anything the caller
    did, so it is worth telling apart from a usage error even though neither
    is retryable.
    """

    exit_code = 6
