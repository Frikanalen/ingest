"""End-to-end ingest against a real SSH archive.

Ingest is two halves now: the hook archives the original and queues a job, and
a worker drains it. Most of these drive both, because what matters to a member
is the state the pair leaves behind, and a test that stopped at the hook would
be blind to a seam that is exactly where things can go wrong. The ones that
name `archived` are about the hook alone.

The point they have always made still holds: ffmpeg reads a file from local
disk and writes to local scratch, and only finished files travel to the
archive.
"""

import re
import shutil
import subprocess
from pathlib import PurePosixPath

import pytest
import pytest_asyncio
from frikanalen_django_api_client.models import IngestKindEnum, VideoFileVariantEnum

from app.api.hooks.metadata import MetadataExtractor
from app.archive_store import FileAlreadyArchived, SshArchiveStore
from app.formats import current_revision
from app.ingest import Ingester
from app.media.loudness.measure import measure_loudness
from app.util.settings import SshArchiveSettings
from tests.utils.catalogue import recording_django_api, registered_file
from tests.utils.drain import drain_one
from tests.utils.ssh_server import run_ssh_server

VIDEO_ID = "12345"

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


@pytest.fixture
def archive_root(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def work_dir(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return work


@pytest.fixture
def upload_dir(tmp_path):
    upload = tmp_path / "upload"
    upload.mkdir()
    return upload


@pytest.fixture
def uploaded_file(upload_dir, color_bars_video):
    """The upload as tusd would have left it, under FK_TUSD_DIR."""
    destination = upload_dir / "example_video.mp4"
    shutil.copy(color_bars_video, destination)
    return destination


@pytest_asyncio.fixture
async def ssh_server(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()

    async with run_ssh_server(keys) as server:
        yield server


@pytest.fixture
def archive(ssh_server, archive_root) -> SshArchiveStore:
    return SshArchiveStore(
        SshArchiveSettings(
            host=ssh_server.host,
            port=ssh_server.port,
            username=ssh_server.username,
            dir=PurePosixPath(archive_root),
            private_key_file=ssh_server.client_key_file,
            known_hosts_file=ssh_server.known_hosts_file,
        )
    )


@pytest.fixture
def django_api():
    return recording_django_api(VIDEO_ID)


@pytest.fixture
def django_api_with_dash():
    """A catalogue that already has this video's DASH, at the current revision."""
    return recording_django_api(
        VIDEO_ID,
        files=[
            registered_file(
                1,
                VIDEO_ID,
                VideoFileVariantEnum.DASH,
                f"{VIDEO_ID}/dash/manifest.mpd",
                revision=current_revision(VideoFileVariantEnum.DASH),
            )
        ],
    )


@pytest_asyncio.fixture
async def metadata(uploaded_file):
    return await MetadataExtractor().do_probe(uploaded_file)


@pytest_asyncio.fixture
async def archived(archive, django_api, work_dir, uploaded_file, metadata):
    """The hook's half: the original archived and registered, a job queued."""
    await Ingester(archive=archive, django_api=django_api).ingest(VIDEO_ID, uploaded_file, metadata)


@pytest_asyncio.fixture
async def ingested(archived, archive, django_api, work_dir):
    """Both halves: the hook, then a worker draining what it queued."""
    await drain_one(archive, django_api, work_dir)


@pytest.fixture
def uploaded_file_with_tone(upload_dir, color_bars_video_with_tone):
    destination = upload_dir / "with_audio.mp4"
    shutil.copy(color_bars_video_with_tone, destination)
    return destination


@pytest_asyncio.fixture
async def ingested_with_tone(archive, django_api, work_dir, uploaded_file_with_tone):
    metadata = await MetadataExtractor().do_probe(uploaded_file_with_tone)
    await Ingester(archive=archive, django_api=django_api).ingest(
        VIDEO_ID, uploaded_file_with_tone, metadata
    )
    await drain_one(archive, django_api, work_dir)


def _created_file(django_api, file_format: VideoFileVariantEnum):
    for call in django_api.create_video_file.await_args_list:
        if call.kwargs["file_format"] == file_format:
            return call.kwargs
    raise AssertionError(f"no videofile was created for {file_format}")


@pytest.mark.asyncio
async def test_records_the_originals_loudness_against_the_original(ingested_with_tone, django_api):
    """The stored figure is what playout levels to -23 LUFS from, so it has to
    describe the file as uploaded -- not the DASH output, which ingest has
    already normalized to a different target.

    Measured by the worker now, against the archived original, and written to
    the row the hook created. The hook does not measure: the worker fetches
    that file anyway, and two passes over the same audio for the same number
    is one too many."""
    django_api.set_video_file_loudness.assert_awaited_once()
    _, loudness = django_api.set_video_file_loudness.await_args.args

    assert loudness is not None
    assert loudness.truepeak_lufs is not None
    # The fixture's tone is deliberately quiet, so a figure anywhere near
    # the -16 LUFS the DASH output is normalized to would mean we had
    # measured the wrong file.
    assert loudness.integrated_lufs == pytest.approx(-39.8, abs=1.0)


@pytest.mark.asyncio
async def test_records_no_loudness_for_a_file_with_no_audio(ingested, django_api):
    django_api.set_video_file_loudness.assert_not_awaited()


@pytest.mark.asyncio
async def test_normalizes_the_dash_audio_to_the_web_target(ingested_with_tone, archive_root):
    """The whole point of measuring: the browser gets audio at a level that
    sits alongside everything else in the tab."""
    dash = archive_root / VIDEO_ID / "dash"
    audio = [f for f in dash.iterdir() if f.suffix == ".mp4" and _has_audio(f)]
    assert audio, f"no audio representation in {list(dash.iterdir())}"

    measured = await measure_loudness(audio[0])

    assert measured is not None
    assert measured.integrated_lufs == pytest.approx(-16, abs=1.5)


def _has_audio(path) -> bool:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv", str(path)],
        capture_output=True,
        text=True,
    )
    return bool(probe.stdout.strip())


@pytest.mark.asyncio
async def test_archives_the_original_and_every_derivative(ingested, archive_root):
    assert (archive_root / VIDEO_ID / "original" / "example_video.mp4").is_file()
    assert (archive_root / VIDEO_ID / "large_thumb" / "example_video.jpg").is_file()
    assert (archive_root / VIDEO_ID / "med_thumb" / "example_video.jpg").is_file()
    assert (archive_root / VIDEO_ID / "small_thumb" / "example_video.jpg").is_file()
    assert (archive_root / VIDEO_ID / "dash" / "manifest.mpd").is_file()


@pytest.mark.asyncio
async def test_archives_every_file_the_dash_manifest_references(ingested, archive_root):
    """A format is a directory of files now, not one file, and the manifest is
    only playable if the media it names travelled with it."""
    dash = archive_root / VIDEO_ID / "dash"
    referenced = set(re.findall(r"<BaseURL>([^<]+)</BaseURL>", (dash / "manifest.mpd").read_text()))

    assert referenced, "manifest references no media at all"
    assert all((dash / name).is_file() for name in referenced)


@pytest.mark.asyncio
async def test_archives_nothing_but_the_finished_files(ingested, archive_root):
    """ffmpeg scratch, the two-pass log and the transfer spool must all stay out."""
    archived = sorted(str(p.relative_to(archive_root)) for p in archive_root.rglob("*") if p.is_file())

    dash_media = sorted(f"{VIDEO_ID}/dash/manifest-stream{n}.mp4" for n in range(3))

    assert archived == sorted(
        [
            f"{VIDEO_ID}/dash/manifest.mpd",
            *dash_media,
            f"{VIDEO_ID}/large_thumb/example_video.jpg",
            f"{VIDEO_ID}/med_thumb/example_video.jpg",
            f"{VIDEO_ID}/small_thumb/example_video.jpg",
            f"{VIDEO_ID}/original/example_video.mp4",
        ]
    )


@pytest.mark.asyncio
async def test_leaves_no_spool_behind(ingested, archive_root):
    """The staging tree is swept as transfers finish, not left to accumulate."""
    assert sorted(str(p.relative_to(archive_root)) for p in archive_root.iterdir()) == [VIDEO_ID]


@pytest.mark.asyncio
async def test_removes_the_upload_once_it_is_safely_archived(archived, uploaded_file, upload_dir):
    """The archive holds the same bytes and the queued job reads from there, so
    keeping the tusd copy past this point would only fill the upload volume."""
    assert not uploaded_file.exists()
    assert list(upload_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_leaves_the_work_directory_clean(ingested, work_dir):
    assert list(work_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_registers_archive_relative_paths_with_django(ingested, django_api):
    registered = {
        call.kwargs["file_format"]: call.kwargs["filename"] for call in django_api.create_video_file.call_args_list
    }

    assert registered == {
        VideoFileVariantEnum.ORIGINAL: f"{VIDEO_ID}/original/example_video.mp4",
        VideoFileVariantEnum.LARGE_THUMB: f"{VIDEO_ID}/large_thumb/example_video.jpg",
        VideoFileVariantEnum.MED_THUMB: f"{VIDEO_ID}/med_thumb/example_video.jpg",
        VideoFileVariantEnum.SMALL_THUMB: f"{VIDEO_ID}/small_thumb/example_video.jpg",
        # Only the manifest: the media it names is reached through it, never
        # on its own.
        VideoFileVariantEnum.DASH: f"{VIDEO_ID}/dash/manifest.mpd",
    }


@pytest.mark.asyncio
async def test_marks_the_import_complete(ingested, django_api):
    """The worker's job, and only on an upload: it is the one that knows every
    format landed."""
    django_api.set_video_proper_import.assert_awaited_once_with(VIDEO_ID, True)


@pytest.mark.asyncio
async def test_the_hook_does_not_mark_the_import_complete(archived, django_api):
    """Between the hook returning and a worker finishing, the video is archived
    but not yet everything it is supposed to be."""
    django_api.set_video_proper_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_queues_the_rest_of_the_work(archived, django_api):
    django_api.enqueue_ingest_job.assert_awaited_once()

    video_id = django_api.enqueue_ingest_job.await_args.args[0]
    assert video_id == VIDEO_ID
    assert django_api.enqueue_ingest_job.await_args.kwargs["kind"] == IngestKindEnum.UPLOAD
    # Above the backfill's 0, so a member's upload is claimed before a
    # catalogue-wide re-encode that is already waiting.
    assert django_api.enqueue_ingest_job.await_args.kwargs["priority"] > 0


@pytest.mark.asyncio
async def test_the_hook_archives_only_the_original(archived, archive_root, django_api):
    """Everything else needs the original in place first, which is exactly what
    makes it a worker's to do."""
    assert (archive_root / VIDEO_ID / "original" / "example_video.mp4").is_file()
    assert sorted(p.name for p in (archive_root / VIDEO_ID).iterdir()) == ["original"]

    registered = [call.kwargs["file_format"] for call in django_api.create_video_file.await_args_list]
    assert registered == [VideoFileVariantEnum.ORIGINAL]


@pytest.mark.asyncio
async def test_records_the_frame_rate_it_worked_out_anyway(ingested, django_api):
    """Ingest has to know the exact rate -- DASH segments fall on whole frames
    -- and until this path planned its work like a backfill it had nowhere to
    put it. A loop over DESIRED_FORMATS could not have written this, so it is
    also what proves the plan is what drives the upload now."""
    django_api.set_video_framerate.assert_awaited_once()

    video_id, framerate_milli = django_api.set_video_framerate.await_args.args
    assert video_id == VIDEO_ID
    # The fixture is 25fps, recorded in thousandths of a frame per second.
    assert framerate_milli == 25000


@pytest.mark.asyncio
async def test_builds_only_what_is_missing(archive, django_api_with_dash, work_dir, uploaded_file, metadata):
    """The point of planning rather than looping: a format the catalogue
    already has at the current revision is not built a second time, so a video
    that comes back through here does not collide with its own output --
    put() refuses to overwrite, by design."""
    await Ingester(archive=archive, django_api=django_api_with_dash).ingest(
        VIDEO_ID, uploaded_file, metadata
    )
    await drain_one(archive, django_api_with_dash, work_dir)

    built = [call.kwargs["file_format"] for call in django_api_with_dash.create_video_file.await_args_list]

    assert VideoFileVariantEnum.DASH not in built
    assert VideoFileVariantEnum.LARGE_THUMB in built


@pytest.mark.asyncio
async def test_keeps_the_upload_when_the_archive_rejects_it(
    archive, archive_root, django_api, work_dir, uploaded_file, metadata
):
    """A failed ingest must leave the upload behind so it can be retried."""
    occupied = archive_root / VIDEO_ID / "original" / "example_video.mp4"
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"already archived")

    with pytest.raises(FileAlreadyArchived):
        await Ingester(archive=archive, django_api=django_api).ingest(
            VIDEO_ID, uploaded_file, metadata
        )

    assert uploaded_file.exists()
    assert occupied.read_bytes() == b"already archived"
    django_api.enqueue_ingest_job.assert_not_awaited()
