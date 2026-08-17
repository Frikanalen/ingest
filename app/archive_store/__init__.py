from app.archive_store.base import ArchiveError, ArchiveSession, ArchiveStore, FileAlreadyArchived
from app.archive_store.local import LocalArchiveStore
from app.util.settings import LocalArchiveSettings

__all__ = [
    "ArchiveError",
    "ArchiveSession",
    "ArchiveStore",
    "FileAlreadyArchived",
    "LocalArchiveStore",
    "create_archive_store",
]


def create_archive_store(settings: LocalArchiveSettings) -> ArchiveStore:
    """Build the archive store the settings describe.

    Called at startup so misconfiguration shows up there rather than partway
    through the first ingest. Raises ArchiveError if no usable archive can be
    built.
    """
    return LocalArchiveStore(settings.dir)
