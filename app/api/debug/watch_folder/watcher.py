import asyncio
import json
import logging
import os

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.util.settings import settings

_change_event = asyncio.Event()


class ChangeHandler(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)

    def on_any_event(self, event):
        self.logger.debug("Received event", extra={"event": event})
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(_change_event.set)


_observer = Observer()
_observer.schedule(ChangeHandler(), path=settings.debug.watchdir, recursive=False)
_observer.start()


async def watch_directory():
    yield 'event: status\ndata: "Watching directory..."\n\n'

    while True:
        await _change_event.wait()
        _change_event.clear()

        files = []
        for entry in os.scandir(settings.debug.watchdir):
            if entry.is_file():
                files.append({"name": entry.name, "size": entry.stat().st_size})

        data = json.dumps(files)
        yield f"event: directoryUpdate\ndata: {data}\n\n"


def stop_observer():
    """
    Stop the directory watcher observer cleanly.
    """
    _observer.stop()
    _observer.join()
