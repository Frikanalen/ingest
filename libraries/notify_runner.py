import logging
import os
import shutil

from inotify import constants
from inotify.adapters import Inotify

from .fk_exceptions import SkippableError
from .interactive import _handle_file, get_metadata
from .measure_loudness import measure_loudness


def run_inotify(watch_dir, move_to_dir):
    logging.info("Starting ingress daemon in inotify mode...")
    logging.info("watch: %s, move_to: %s", watch_dir, move_to_dir)

    i = Inotify(block_duration_s=300)
    i.add_watch(watch_dir, constants.IN_MOVED_TO)
    for evt in i.event_gen():
        if evt is None:
            # Call every block_duration_s seconds when there is nothing else to do
            run_periodic(watch_dir, move_to_dir)
            continue
        (_header, type_names, _path, file_name) = evt
        if "IN_ISDIR" not in type_names or not file_name.isdigit():
            logging.info("Skipped %s" % file_name)
            continue
        logging.info("Found %s" % file_name)
        handle_file(watch_dir, move_to_dir, file_name)


def run_periodic(watch_dir, move_to_dir):
    """Called periodially when there is nothing else to do for this process."""

    logging.debug("processing backlog")
    measure_loudness(watch_dir, move_to_dir)
    return


def handle_file(watch_dir, move_to_dir, video_id: str):
    logging.info("Handling file id: %d - moving from %s to %s", video_id, watch_dir, move_to_dir)
    from_dir = os.path.join(watch_dir, video_id)

    try:
        [file_name] = os.listdir(from_dir)
    except ValueError:
        raise SkippableError("Found no file in %s" % from_dir)
    metadata = get_metadata(os.path.join(from_dir, file_name))
    to_dir = os.path.join(move_to_dir, video_id)

    new_filepath = copy_original(from_dir, to_dir, file_name)
    _handle_file(id, new_filepath or video_id, metadata)
    shutil.rmtree(from_dir)


def copy_original(from_dir, to_dir, file_name: str):
    folder = "original"
    os.makedirs(os.path.join(to_dir, folder), exist_ok=True)
    new_filepath = os.path.join(to_dir, folder, file_name)
    shutil.copy2(os.path.join(from_dir, file_name), new_filepath)
    return new_filepath
