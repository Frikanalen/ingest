import subprocess
import sys
from pathlib import Path

import pytest
from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.media.comand_template import (
    ENCODERS,
    TEMPLATE_DIR,
    ProfileMetadata,
    ProfileTemplateArguments,
    TemplatedCommandGenerator,
    TemplateNotFound,
    backends_of,
)
from app.media.ladder import LADDER, Rung
from app.media.loudness.loudness_measurement import LoudnessMeasurement

MEASURED = LoudnessMeasurement(
    integrated_lufs=-27.85,
    truepeak_lufs=-9.61,
    loudness_range=5.2,
    threshold_lufs=-38.2,
    target_offset=0.15,
)


#: The ladder at its full size, as a 4K source gets it.
RUNGS = [Rung(spec.codec, round(spec.height * 16 / 9 / 2) * 2, spec.height, spec.bitrate_k) for spec in LADDER]


def template_args(**overrides) -> ProfileTemplateArguments:
    return ProfileTemplateArguments(
        **{
            "input_file": Path("./hello"),
            "output_file": Path("./there"),
            "output_dir": Path("."),
            "scratch_dir": Path("./scratch"),
            "seek_s": 0.2,
            "has_audio": True,
            "loudness": None,
            "frame_rate": "25/1",
            "gop_frames": 150,
            "segment_duration_s": "6.000000",
            "rungs": RUNGS,
            **overrides,
        }
    )


def test_large_thumb_command_looks_as_expected():
    template = TemplatedCommandGenerator(VideoFileVariantEnum.LARGE_THUMB)
    command = template.render(template_args(output_file=Path("./it would be weird for this to be a file huh")))
    expected_command = (
        'ffmpeg -nostats -ss 0.2 -i "hello" -y -vf scale=720:-1 -aspect 16:9 '
        '-frames:v 1 "it would be weird for this to be a file huh"'
    )
    assert command == expected_command, f"Expected: {expected_command}, but got: {command}"


def test_thumbnails_seek_before_they_decode():
    """`-ss` after `-i` is an output option: ffmpeg decodes and discards every
    frame up to the seek point, so three thumbnails a quarter of the way in
    cost most of a decode pass. Before `-i` it is an input seek instead."""
    for thumb in (VideoFileVariantEnum.LARGE_THUMB, VideoFileVariantEnum.MED_THUMB, VideoFileVariantEnum.SMALL_THUMB):
        command = TemplatedCommandGenerator(thumb).render(template_args())

        assert command.index("-ss ") < command.index("-i "), command


def test_large_thumb_is_a_single_pass_by_default():
    assert TemplatedCommandGenerator(VideoFileVariantEnum.LARGE_THUMB).metadata.passes == 1


def test_med_thumb_is_narrower_than_large_thumb():
    template = TemplatedCommandGenerator(VideoFileVariantEnum.MED_THUMB)
    command = template.render(template_args(output_file=Path("./out.jpg")))
    expected_command = 'ffmpeg -nostats -ss 0.2 -i "hello" -y -vf scale=320:-1 -aspect 16:9 -frames:v 1 "out.jpg"'
    assert command == expected_command, f"Expected: {expected_command}, but got: {command}"


def test_small_thumb_is_narrower_than_med_thumb():
    template = TemplatedCommandGenerator(VideoFileVariantEnum.SMALL_THUMB)
    command = template.render(template_args(output_file=Path("./out.jpg")))
    expected_command = 'ffmpeg -nostats -ss 0.2 -i "hello" -y -vf scale=120:-1 -aspect 16:9 -frames:v 1 "out.jpg"'
    assert command == expected_command, f"Expected: {expected_command}, but got: {command}"


def dash(encoder="cpu", **overrides) -> str:
    return TemplatedCommandGenerator(VideoFileVariantEnum.DASH, encoder).render(template_args(**overrides))


every_encoder = pytest.mark.parametrize("encoder", ENCODERS)


def test_h264_med_is_retired():
    """The DASH ladder's H.264 rungs cover what the 720p MP4 was for."""
    with pytest.raises(TemplateNotFound):
        TemplatedCommandGenerator("h264_med")


@every_encoder
def test_every_encoder_has_its_own_dash_template(encoder):
    assert (TEMPLATE_DIR / encoder / "dash.j2").is_file()


def test_every_backend_builds_the_same_revision_of_the_ladder():
    """A video built by one backend must not read as stale to another, or the
    pools would rebuild each other's output forever."""
    revisions = {
        encoder: TemplatedCommandGenerator(VideoFileVariantEnum.DASH, encoder).metadata.revision
        for encoder in backends_of("dash")
    }

    assert set(revisions) == set(ENCODERS)
    assert len(set(revisions.values())) == 1, revisions


def test_a_format_with_one_template_serves_every_encoder():
    """Thumbnails and the preview do not depend on the encoder."""
    for file_format in (VideoFileVariantEnum.LARGE_THUMB, VideoFileVariantEnum.DASH_PREVIEW):
        commands = {TemplatedCommandGenerator(file_format, encoder).render(template_args()) for encoder in ENCODERS}
        assert len(commands) == 1, commands


@pytest.mark.parametrize(
    ("encoder", "av1", "h264", "scaler"),
    [
        ("cpu", "libsvtav1", "libx264", "scale="),
        ("qsv", "av1_qsv", "h264_qsv", "vpp_qsv="),
        ("nvenc", "av1_nvenc", "h264_nvenc", "scale_cuda="),
    ],
)
def test_each_backend_uses_its_own_encoders_and_scaler(encoder, av1, h264, scaler):
    command = dash(encoder)

    assert command.count(f" {av1}") == sum(rung.codec == "av1" for rung in RUNGS), command
    assert command.count(f" {h264}") == sum(rung.codec == "h264" for rung in RUNGS), command
    assert command.count(scaler) == len(RUNGS), command


@every_encoder
def test_every_rung_is_scaled_to_its_exact_size(encoder):
    command = dash(encoder)

    for rung in RUNGS:
        assert f"{rung.width}" in command and f"{rung.height}" in command


@every_encoder
def test_every_rung_is_capped(encoder):
    """DASH advertises a bandwidth per representation and players switch on it,
    so no rung may be left to a quality target alone."""
    command = dash(encoder)

    for i, rung in enumerate(RUNGS):
        assert f"-maxrate:v:{i} {rung.maxrate_k}k" in command
        assert f"-bufsize:v:{i} {rung.bufsize_k}k" in command


@every_encoder
def test_h264_rungs_are_high_profile(encoder):
    command = dash(encoder)

    for i, rung in enumerate(RUNGS):
        if rung.codec == "h264":
            assert f"-profile:v:{i} high" in command


@every_encoder
def test_dash_deinterlaces_what_the_decoder_says_is_interlaced(encoder):
    """bwdif with deint=interlaced leaves progressive frames alone, so it can
    sit in every graph rather than depending on what the container claims."""
    assert "bwdif=mode=send_frame:parity=auto:deint=interlaced" in dash(encoder)


@every_encoder
def test_dash_names_its_output_rather_than_following_the_source(encoder):
    """The manifest names its media, so those names must not carry a source stem
    that would need percent-encoding to survive as a URL."""
    metadata = TemplatedCommandGenerator(VideoFileVariantEnum.DASH, encoder).metadata

    assert metadata.output_name_for(Path("/uploads/Some Show, Episode 3.mov")) == "manifest.mpd"


@every_encoder
def test_dash_is_a_single_ffmpeg_invocation(encoder):
    """One decode feeding every rendition, so ffmpeg's progress needs no scaling."""
    template = TemplatedCommandGenerator(VideoFileVariantEnum.DASH, encoder)
    command = template.render(template_args())

    assert template.metadata.passes == 1
    assert command.count("ffmpeg") == 1
    assert command.count("-progress pipe:1") == 1


@every_encoder
def test_dash_sets_the_keyframe_interval_explicitly(encoder):
    """The muxer starts a segment at whichever keyframe it finds first, so an
    encoder placing keyframes of its own cuts segments the manifest does not
    know about. Pinning -g and -keyint_min together, with scene-cut detection
    off, is what makes the segments come out even."""
    command = dash(encoder, gop_frames=360)

    assert "-g 360 -keyint_min 360 -sc_threshold 0" in command
    assert "-flags +cgop" in command
    assert "-force_key_frames" not in command


def test_qsv_does_not_place_keyframes_of_its_own():
    command = dash("qsv")

    for i in range(len(RUNGS)):
        assert f"-adaptive_i:v:{i} 0 -adaptive_b:v:{i} 0" in command


def test_nvenc_does_not_place_keyframes_of_its_own():
    command = dash("nvenc")

    for i in range(len(RUNGS)):
        assert f"-no-scenecut:v:{i} 1" in command


@every_encoder
def test_dash_asks_for_the_segment_length_it_will_actually_produce(encoder):
    """ffmpeg copies -seg_duration into the manifest as the segment length
    players do their seek arithmetic with. Asking for a round 6s while the
    keyframes land every 6.006s is what makes a seek miss."""
    assert "-seg_duration 6.006000" in dash(encoder, gop_frames=360, segment_duration_s="6.006000")


@every_encoder
def test_dash_encodes_at_a_constant_frame_rate(encoder):
    """A GOP is a number of frames, so it is only a fixed length of time if the
    frames arrive at a fixed rate."""
    assert "-fps_mode cfr" in dash(encoder)


@every_encoder
def test_dash_is_told_which_constant_frame_rate(encoder):
    """Left to infer it, ffmpeg may not settle on the rate the GOP length and
    the segment length were worked out from -- and then the manifest describes
    segments the encoder never produced."""
    command = dash(encoder, frame_rate="60000/1001")

    assert "-r 60000/1001" in command
    assert "fps=60000/1001" in command


@every_encoder
def test_dash_audio_is_stereo_aac(encoder):
    """Opus in an MP4 is silence on anything running WebKit -- which on iOS is
    every browser, Firefox and Chrome included. AAC-LC is the one audio codec
    every DASH client will decode."""
    command = dash(encoder)

    assert "-ac 2 -c:a aac -b:a 128k" in command
    assert "libopus" not in command


@every_encoder
def test_dash_normalizes_a_measured_source_to_the_web_target(encoder):
    """-16 LUFS is what the rest of a browser tab sounds like. Playout works
    to -23 from the figure stored against the original instead, which is why
    the measurement describes the upload rather than this output."""
    command = dash(encoder, loudness=MEASURED)

    assert "loudnorm=I=-16:TP=-1" in command
    for measured in ("measured_I=-27.85", "measured_TP=-9.61", "measured_LRA=5.2", "measured_thresh=-38.2"):
        assert measured in command, command


@every_encoder
def test_dash_normalizes_in_one_linear_pass_rather_than_riding_the_gain(encoder):
    """Without the measurements loudnorm works dynamically, which pumps
    quiet passages up and audibly breathes on speech."""
    command = dash(encoder, loudness=MEASURED)

    assert "linear=true" in command
    assert "offset=0.15" in command


@every_encoder
def test_dash_resamples_after_normalizing(encoder):
    """loudnorm outputs 192kHz, which the AAC encoder does not accept -- without
    a resampler the encode fails outright rather than sounding wrong."""
    command = dash(encoder, loudness=MEASURED)

    assert command.index("loudnorm") < command.index("-ar 48000") < command.index("-c:a aac")


@every_encoder
def test_dash_leaves_the_level_alone_when_nothing_was_measured(encoder):
    """A wrong gain is worse than no gain."""
    command = dash(encoder, loudness=None)

    assert "loudnorm" not in command
    assert "-map 0:a:0 -ar 48000 -ac 2 -c:a aac" in command


@every_encoder
def test_dash_does_not_normalize_a_source_with_no_measurable_peak(encoder):
    """loudnorm has no syntax for an unknown true peak, and rendering the
    null into the filter would produce a command ffmpeg cannot parse."""
    command = dash(encoder, loudness=MEASURED.model_copy(update={"truepeak_lufs": None}))

    assert "loudnorm" not in command
    assert "None" not in command


@every_encoder
def test_dash_leaves_out_the_audio_adaptation_set_when_there_is_no_audio(encoder):
    """An adaptation set with no representation in it is not valid DASH."""
    command = dash(encoder, has_audio=False)

    assert "0:a:0" not in command
    assert "streams=a" not in command
    assert '-adaptation_sets "id=0,streams=0,1,2,3,4 id=1,streams=5,6"' in command


@every_encoder
def test_dash_puts_each_codec_in_its_own_adaptation_set(encoder):
    """A player picks the set it can decode, and switches only within it."""
    command = dash(encoder, has_audio=True)

    assert "-map 0:a:0" in command
    assert '-adaptation_sets "id=0,streams=0,1,2,3,4 id=1,streams=5,6 id=2,streams=a"' in command


def test_preview_is_one_h264_rung_that_plays_everywhere():
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH_PREVIEW).render(template_args())

    assert command.count("libx264") == 1
    assert "scale=-2:min(360" in command
    assert '-adaptation_sets "id=0,streams=v id=1,streams=a"' in command


def test_a_template_must_say_how_its_output_is_named():
    with pytest.raises(ValueError):
        ProfileMetadata()

    with pytest.raises(ValueError):
        ProfileMetadata(output_file_extension="webm", output_file_name="manifest.mpd")


def test_revision_defaults_to_the_first_one():
    """A template that says nothing is revision 1, not revision 0.

    Zero is reserved for files registered before revisions were recorded at
    all, so a template must never be able to claim it.
    """
    assert ProfileMetadata(output_file_extension="jpg").revision == 1


def test_revision_cannot_claim_the_untracked_sentinel():
    with pytest.raises(ValueError):
        ProfileMetadata(output_file_extension="jpg", revision=0)


def test_revision_is_read_from_the_header():
    assert ProfileMetadata(output_file_name="manifest.mpd", revision=4).revision == 4


@pytest.mark.parametrize(
    "format_name", [f.value for f in (VideoFileVariantEnum.DASH, VideoFileVariantEnum.LARGE_THUMB)]
)
def test_shipped_templates_declare_a_usable_revision(format_name):
    assert TemplatedCommandGenerator(format_name).metadata.revision >= 1


def test_templates_are_found_with_no_git_and_from_somewhere_else_entirely(tmp_path):
    """Neither a git checkout nor any particular working directory is something
    the templates should depend on.

    They were previously located by forking `git rev-parse --show-toplevel`
    and falling back, silently, to a hardcoded `/app`. In the image that came
    out right whichever branch ran -- `/app` is equally the WORKDIR and the
    root of the checkout copied in with the source -- so neither the fork nor
    the fallback was ever the part being relied on, and a wrong answer would
    have surfaced as a missing template rather than as a broken lookup.
    """
    repo_root = Path(__file__).resolve().parent.parent
    read_a_template = (
        "from app.media.comand_template import TemplatedCommandGenerator as G; print(G('dash').metadata.revision)"
    )

    result = subprocess.run(
        [sys.executable, "-c", read_a_template],
        cwd=tmp_path,
        env={"PATH": "", "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().isdigit(), result.stdout


def test_a_format_with_no_template_says_where_it_looked():
    """Loudly, rather than resolving to a directory that happens to exist: the
    templates decide what `current_revision` reports, and so what the
    reconciler thinks is stale."""
    with pytest.raises(TemplateNotFound) as raised:
        TemplatedCommandGenerator("no_such_format")

    assert "no_such_format.j2" in str(raised.value)
    assert str(TEMPLATE_DIR) in str(raised.value)


def test_a_template_is_read_and_compiled_once_however_many_generators_want_it():
    """`FormatProducer.produce` builds a generator per format per video, and a
    catalogue-wide backfill runs that thousands of times on the event loop.
    Sharing the compiled template is what keeps that from being thousands of
    blocking reads."""
    first = TemplatedCommandGenerator(VideoFileVariantEnum.DASH)
    second = TemplatedCommandGenerator(VideoFileVariantEnum.DASH)

    assert second.template is first.template
    # The enum member and its value must not be two cache entries.
    assert TemplatedCommandGenerator("dash").template is first.template
