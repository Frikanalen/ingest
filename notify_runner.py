import logging
import os
import shutil

from inotify import constants
from inotify.adapters import Inotify

from fk_exceptions import SkippableError
from interactive import get_metadata, _handle_file

from measure_loudness import measure_loudness


def run_inotify(watch_dir, move_to_dir):
    logging.info(
        "Starting move_and_process, watch: %s, move_to: %s", watch_dir, move_to_dir
    )
    i = Inotify(block_duration_s=300)
    i.add_watch(watch_dir, constants.IN_MOVED_TO)
    for evt in i.event_gen():
        if evt is None:
            # Call every block_duration_s seconds when there is nothing else to do
            run_periodic(watch_dir, move_to_dir)
            continue
        (_header, type_names, _path, fn) = evt
        if "IN_ISDIR" not in type_names or not fn.isdigit():
            logging.info("Skipped %s" % fn)
            continue
        logging.info("Found %s" % fn)
        handle_file(watch_dir, move_to_dir, int(fn))


def run_periodic(watch_dir, move_to_dir):
    """Called periodially when there is nothing else to do for this process."""

    logging.debug("processing backlog")
    measure_loudness(watch_dir, move_to_dir)
    return


def handle_file(watch_dir, move_to_dir, id):
    logging.info(
        "Handling file id: %d - moving from %s to %s", id, watch_dir, move_to_dir
    )
    str_id = str(id)
    from_dir = os.path.join(watch_dir, str_id)
    try:
        fn = os.listdir(from_dir)[0]
    except IndexError:
        raise SkippableError("Found no file in %s" % from_dir)
    metadata = get_metadata(os.path.join(from_dir, fn))
    to_dir = os.path.join(move_to_dir, str_id)

    new_filepath = copy_original(from_dir, to_dir, metadata, fn)
    _handle_file(id, new_filepath or str_id, metadata)
    shutil.rmtree(from_dir)


def copy_original(from_dir, to_dir, metadata, fn):
    folder = "broadcast" if direct_playable(metadata) else "original"
    os.makedirs(os.path.join(to_dir, folder), exist_ok=True)
    new_filepath = os.path.join(to_dir, folder, fn)
    shutil.copy2(os.path.join(from_dir, fn), new_filepath)
    return new_filepath


def direct_playable(metadata):
    def is_pal(s):
        return (
            s.get("codec_name") == "dvvideo"
            and s.get("codec_time_base") == "1/25"
            and s.get("width") == 720
        )

    return any(is_pal(s) for s in metadata["streams"])
