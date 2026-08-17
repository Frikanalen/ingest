from app.archive_store.base import ArchiveError, ArchiveSession, ArchiveStore, FileAlreadyArchived
from app.archive_store.local import LocalArchiveStore
from app.archive_store.ssh import SshArchiveStore
from app.util.settings import LocalArchiveSettings, SshArchiveSettings

__all__ = [
    "ArchiveError",
    "ArchiveSession",
    "ArchiveStore",
    "FileAlreadyArchived",
    "LocalArchiveStore",
    "SshArchiveStore",
    "create_archive_store",
]


def create_archive_store(settings: LocalArchiveSettings | SshArchiveSettings) -> ArchiveStore:
    """Build the archive store the settings describe.

    Called at startup so misconfiguration shows up there rather than partway
    through the first ingest. Raises ArchiveError if no usable archive can be
    built.
    """
    if isinstance(settings, SshArchiveSettings):
        return SshArchiveStore(settings)
    return LocalArchiveStore(settings.dir)
