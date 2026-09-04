from logging import getLogger

from app.archive_store.base import (
    ArchiveEntry,
    ArchiveError,
    ArchiveReader,
    ArchiveSession,
    ArchiveStore,
    FileAlreadyArchived,
)
from app.archive_store.local import LocalArchiveStore
from app.archive_store.ssh import SshArchiveStore
from app.util.settings import LocalArchiveSettings, SshArchiveSettings

__all__ = [
    "ArchiveEntry",
    "ArchiveError",
    "ArchiveReader",
    "ArchiveSession",
    "ArchiveStore",
    "FileAlreadyArchived",
    "LocalArchiveStore",
    "SshArchiveStore",
    "create_archive_store",
]

logger = getLogger(__name__)


def create_archive_store(settings: LocalArchiveSettings | SshArchiveSettings) -> ArchiveStore:
    """Build the archive store the settings describe.

    Called at startup so misconfiguration shows up there rather than partway
    through the first ingest. Raises ArchiveError if no usable archive can be
    built.
    """
    if isinstance(settings, LocalArchiveSettings):
        return LocalArchiveStore(settings.dir)

    reason = settings.unusable_reason()
    if reason is None:
        return SshArchiveStore(settings)

    # SSH credentials are optional so that a developer can run ingest without
    # any, but somewhere that expects to archive over SSH, quietly writing to a
    # local directory instead would lose files.
    if settings.required:
        raise ArchiveError(f"Archive host {settings.host} is configured but unusable: {reason}")

    logger.warning(
        "Not archiving to %s: %s. Falling back to the local directory %s.",
        settings.host,
        reason,
        settings.fallback_dir,
    )
    return LocalArchiveStore(settings.fallback_dir)
