import asyncio
import logging
from pathlib import Path

from pydantic import BaseModel
from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver as Observer

from app.api.debug.watch_folder.server_sent_event import ServerSentEvent

_change_event = asyncio.Event()


class ChangeHandler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.loop = loop
        self.logger = logging.getLogger(__name__)

    def on_any_event(self, event):
        self.loop.call_soon_threadsafe(_change_event.set)


_observer = Observer(timeout=1.0)


def start_watchfolder(watchdir: Path | None = None):
    print(f"Starting directory watcher for {watchdir}")
    _observer.schedule(ChangeHandler(asyncio.get_running_loop()), path=watchdir, recursive=True)
    _observer.start()


def stop_watch_folder():
    """
    Stop the directory watcher observer cleanly.
    """
    print("Stopping directory watcher")
    _observer.stop()
    _observer.join()


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


async def watch_directory(directory: Path) -> ServerSentEvent:
    yield ServerSentEvent(event="path", data=str(directory.absolute())).encode()
    yield ServerSentEvent(event="status", data="Watching directory...").encode()
    print(f"Watching directory: {directory}")
    files = _list_directory_recursive(directory).model_dump_json()
    yield ServerSentEvent(event="directoryUpdate", data=files).encode()

    while True:
        await _change_event.wait()
        _change_event.clear()
        # "When in doubt, use brute force" - Ken Thompson
        files = _list_directory_recursive(directory).model_dump_json()
        yield ServerSentEvent(event="directoryUpdate", data=files).encode()
