from app.media.ffmpeg_progress import FfmpegProgressParser


def test_ignores_lines_that_are_not_position_updates():
    parser = FfmpegProgressParser(duration_s=10)

    assert parser.feed_line("frame=12") is None
    assert parser.feed_line("fps=25.00") is None
    assert parser.feed_line("") is None


def test_out_time_reports_fraction_of_duration():
    parser = FfmpegProgressParser(duration_s=10)

    assert parser.feed_line("out_time_us=5000000") == 0.5


def test_out_time_is_clamped_to_the_end_of_the_duration():
    """ffmpeg can overshoot the probed duration by a frame or two."""
    parser = FfmpegProgressParser(duration_s=10)

    assert parser.feed_line("out_time_us=11000000") == 1.0


def test_unset_out_time_is_ignored_rather_than_read_as_zero():
    """Before ffmpeg has a real position it reports N/A or a huge negative sentinel."""
    parser = FfmpegProgressParser(duration_s=10)
    parser.feed_line("out_time_us=5000000")

    assert parser.feed_line("out_time_us=N/A") is None
    assert parser.feed_line("out_time_us=-9223372036854775808") is None


def test_progress_end_completes_a_single_pass_template():
    parser = FfmpegProgressParser(duration_s=10, passes=1)
    parser.feed_line("out_time_us=5000000")

    assert parser.feed_line("progress=end") == 1.0


def test_two_pass_template_advances_by_half_at_each_pass_boundary():
    parser = FfmpegProgressParser(duration_s=10, passes=2)

    assert parser.feed_line("out_time_us=10000000") == 0.5
    assert parser.feed_line("progress=end") == 0.5

    assert parser.feed_line("out_time_us=5000000") == 0.75

    assert parser.feed_line("progress=end") == 1.0


def test_progress_continue_does_not_advance_the_pass_count():
    parser = FfmpegProgressParser(duration_s=10, passes=2)

    assert parser.feed_line("progress=continue") is None
    assert parser.feed_line("out_time_us=5000000") == 0.25
