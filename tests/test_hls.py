"""HLS playlists translated from the DASH manifest.

The playlists carry no media of their own: every line that matters is a byte
range into the ladder's files. So what is tested is that the ranges and
durations come through exactly, that the master lists what an Apple device
can start with first, and -- against a real encode -- that a player following
the playlists decodes the whole programme.
"""

import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest
from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.media.comand_template import ProfileTemplateArguments, TemplatedCommandGenerator
from app.media.hls import MASTER_PLAYLIST, UnsupportedManifest, parse_manifest, playlists_from_manifest
from app.media.ladder import rungs_for
from app.media.segmentation import segmentation_for
from tests.test_dash_manifest import BROADCAST_RATE, _make_source, _probe

#: The shape `-f dash -single_file 1` writes: one SegmentList per
#: representation, every segment named by byte range. 14.5 s at 6 s segments,
#: so the last one is the 2.5 s left over.
MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT14.5S">
  <Period id="0" start="PT0.0S">
    <AdaptationSet id="0" contentType="video" frameRate="25/1">
      <Representation id="0" mimeType="video/mp4" codecs="av01.0.08M.08" bandwidth="2000000" width="1280" height="720">
        <BaseURL>manifest-stream0.mp4</BaseURL>
        <SegmentList timescale="1000000" duration="6000000" startNumber="1">
          <Initialization range="0-899" />
          <SegmentURL mediaRange="900-1499899" />
          <SegmentURL mediaRange="1499900-2999899" />
          <SegmentURL mediaRange="2999900-3299899" />
        </SegmentList>
      </Representation>
    </AdaptationSet>
    <AdaptationSet id="1" contentType="video" frameRate="25/1">
      <Representation id="1" mimeType="video/mp4" codecs="avc1.64001f" bandwidth="3000000" width="1280" height="720">
        <BaseURL>manifest-stream1.mp4</BaseURL>
        <SegmentList timescale="1000000" duration="6000000" startNumber="1">
          <Initialization range="0-799" />
          <SegmentURL mediaRange="800-2250799" />
          <SegmentURL mediaRange="2250800-4500799" />
          <SegmentURL mediaRange="4500800-4950799" />
        </SegmentList>
      </Representation>
    </AdaptationSet>
    <AdaptationSet id="2" contentType="audio">
      <Representation id="2" mimeType="audio/mp4" codecs="mp4a.40.2" bandwidth="128000">
        <BaseURL>manifest-stream2.mp4</BaseURL>
        <SegmentList timescale="1000000" duration="6000000" startNumber="1">
          <Initialization range="0-699" />
          <SegmentURL mediaRange="700-96699" />
          <SegmentURL mediaRange="96700-192699" />
          <SegmentURL mediaRange="192700-232699" />
        </SegmentList>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


def test_every_segment_keeps_its_byte_range_and_the_last_its_remainder():
    h264 = playlists_from_manifest(MANIFEST)["manifest-stream1.m3u8"]

    assert '#EXT-X-MAP:URI="../dash/manifest-stream1.mp4",BYTERANGE="800@0"' in h264
    assert "#EXT-X-BYTERANGE:2250000@800\n../dash/manifest-stream1.mp4" in h264
    assert "#EXT-X-BYTERANGE:450000@4500800\n../dash/manifest-stream1.mp4" in h264
    assert h264.count("#EXTINF:6.000000,") == 2
    assert "#EXTINF:2.500000," in h264
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in h264
    assert h264.rstrip().endswith("#EXT-X-ENDLIST")


def test_a_frame_exact_segment_length_comes_through_exactly():
    """6.006 s at 59.94 fps: the segmentation's whole point is that this is not 6."""
    manifest = MANIFEST.replace('duration="6000000"', 'duration="6006000"').replace("PT14.5S", "PT14.512S")

    rep = parse_manifest(manifest)[0]

    assert [segment.duration for segment in rep.segments] == [Fraction(6006, 1000)] * 2 + [Fraction(2500, 1000)]
    assert "#EXT-X-TARGETDURATION:7" in playlists_from_manifest(manifest)["manifest-stream0.m3u8"]


def test_the_master_starts_apple_devices_on_h264_and_pairs_every_variant_with_the_audio():
    master = playlists_from_manifest(MANIFEST)[MASTER_PLAYLIST]
    variants = [line for line in master.splitlines() if line.startswith("#EXT-X-STREAM-INF")]

    assert 'URI="manifest-stream2.m3u8"' in master
    assert 'CODECS="avc1.64001f,mp4a.40.2"' in variants[0]
    assert 'CODECS="av01.0.08M.08,mp4a.40.2"' in variants[1]
    assert all('AUDIO="audio"' in variant and "RESOLUTION=1280x720" in variant for variant in variants)
    assert "FRAME-RATE=25.000" in variants[0]


def test_bandwidth_is_the_busiest_segment_not_the_average():
    """HLS defines BANDWIDTH as the peak; a player that trusts an average stalls on the peaks."""
    master = playlists_from_manifest(MANIFEST)[MASTER_PLAYLIST]
    h264 = next(line for line in master.splitlines() if "avc1" in line)

    # 2 250 000 bytes over 6 s is 3 Mbit/s; the audio peaks at 128 kbit/s.
    assert "BANDWIDTH=3128000," in h264
    assert "AVERAGE-BANDWIDTH=" in h264


@pytest.mark.parametrize(
    "broken",
    [
        MANIFEST.replace('type="static"', 'type="dynamic"'),
        MANIFEST.replace("<BaseURL>manifest-stream0.mp4</BaseURL>", "<BaseURL>../elsewhere.mp4</BaseURL>"),
        MANIFEST.replace(' duration="6000000"', ""),
        MANIFEST.replace('<Initialization range="0-899" />', ""),
        "not xml",
    ],
)
def test_a_manifest_of_any_other_shape_is_refused(broken):
    with pytest.raises(UnsupportedManifest):
        playlists_from_manifest(broken)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
def test_a_player_following_the_playlists_decodes_the_whole_real_ladder(tmp_path: Path):
    """Against the shipped DASH template, at the awkward 59.94 fps."""
    source = _make_source(tmp_path / "source.mp4", BROADCAST_RATE, 20)
    probe = _probe(source)
    segmentation = segmentation_for(probe)
    dash = tmp_path / "video" / "dash"
    dash.mkdir(parents=True)
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(
        ProfileTemplateArguments(
            input_file=source,
            output_file=dash / "manifest.mpd",
            output_dir=dash,
            scratch_dir=tmp_path,
            seek_s=1,
            has_audio=False,
            loudness=None,
            frame_rate=segmentation.frame_rate_arg,
            gop_frames=segmentation.gop_frames,
            segment_duration_s=segmentation.segment_duration_arg,
            rungs=rungs_for(probe, segmentation.frame_rate),
        )
    )
    subprocess.run(command, shell=True, check=True, capture_output=True)

    hls = tmp_path / "video" / "hls"
    hls.mkdir()
    for name, text in playlists_from_manifest((dash / "manifest.mpd").read_text()).items():
        (hls / name).write_text(text)

    for playlist in sorted(hls.glob("manifest-stream*.m3u8")):
        frames = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "csv=p=0",
                str(playlist),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()[0]
        # 20 s at 60000/1001 is 1199 frames, give or take the encoder's last one.
        assert abs(int(frames) - 1199) <= 1, f"{playlist.name} decoded {frames} frames"
