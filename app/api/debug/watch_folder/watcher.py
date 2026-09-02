import asyncio
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from pydantic import BaseModel
from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver as Observer

from app.api.debug.watch_folder.server_sent_event import ServerSentEvent

logger = logging.getLogger(__name__)

_change_event = asyncio.Event()

# One observer for the process, created on start rather than at import so that
# nothing is scheduled unless FK_DEBUG asked for it.
_observer: Observer | None = None


class ChangeHandler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.loop = loop
        self.logger = logging.getLogger(__name__)

    def on_any_event(self, event):
        self.loop.call_soon_threadsafe(_change_event.set)


def start_watchfolder(watchdir: Path) -> None:
    """Watch `watchdir` recursively, waking `watch_directory` on any change.

    This is a debug facility and is expensive enough that it must stay one.
    `PollingObserver` does not use inotify -- it walks the tree and stats every
    entry, and `timeout=1.0` means it does that once a second, forever, in a
    thread. Recursively, over what in production is a 200 GiB volume shared
    with tusd, inside the pod serving tusd's hooks. Only `FK_DEBUG` turns it on.
    """
    global _observer
    if _observer is not None:
        return

    logger.info("Starting directory watcher for %s", watchdir)
    observer = Observer(timeout=1.0)
    observer.schedule(ChangeHandler(asyncio.get_running_loop()), path=watchdir, recursive=True)
    observer.start()
    _observer = observer


def stop_watch_folder() -> None:
    """Stop the directory watcher, if one was ever started."""
    global _observer
    if _observer is None:
        return

    logger.info("Stopping directory watcher")
    _observer.stop()
    _observer.join()
    _observer = None


class DirectoryEntry(BaseModel):
    name: str
    size: int


class DirectoryEntryList(BaseModel):
    entries: list[DirectoryEntry] = []


def _list_directory_recursive(path: Path) -> DirectoryEntryList:
    """
    List all files in the given directory recursively.
    Returns a list of dictionaries with file names and sizes.
    """
    files = []
    for entry in path.rglob("*"):
        if entry.is_file():
            files.append(DirectoryEntry(name=str(entry.relative_to(path)), size=entry.stat().st_size))
    return DirectoryEntryList(entries=files)


async def watch_directory(directory: Path) -> AsyncGenerator[str]:
    yield ServerSentEvent(event="path", data=str(directory.absolute())).encode()
    yield ServerSentEvent(event="status", data="Watching directory...").encode()
    logger.debug("Watching directory: %s", directory)
    files = _list_directory_recursive(directory).model_dump_json()
    yield ServerSentEvent(event="directoryUpdate", data=files).encode()

    while True:
        await _change_event.wait()
        _change_event.clear()
        # "When in doubt, use brute force" - Ken Thompson
        files = _list_directory_recursive(directory).model_dump_json()
        yield ServerSentEvent(event="directoryUpdate", data=files).encode()
