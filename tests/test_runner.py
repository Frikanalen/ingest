import tracemalloc

import pytest

from app.runner import FAILURE_TAIL_BYTES, Task


@pytest.mark.asyncio
async def test_basic_run(tmp_path):
    test_path = tmp_path / "testfile"
    test_path.touch()
    assert test_path.exists()

    await Task(f"rm {test_path}").execute()

    assert not test_path.exists()


@pytest.mark.asyncio
async def test_on_progress_is_called_as_the_command_reports_position():
    calls = []

    async def on_progress(fraction):
        calls.append(fraction)

    command = r"printf 'out_time_us=2000000\nprogress=continue\nout_time_us=5000000\nprogress=end\n'"
    await Task(command, duration_s=10, on_progress=on_progress).execute()

    assert calls == [0.2, 0.5, 1.0]


@pytest.mark.asyncio
async def test_on_progress_is_ignored_without_a_duration():
    """duration_s is what turns an out_time into a fraction; without it there's nothing to report."""
    calls = []

    async def on_progress(fraction):
        calls.append(fraction)

    command = r"printf 'out_time_us=2000000\nprogress=end\n'"
    stdout, _ = await Task(command, on_progress=on_progress).execute()

    assert calls == []
    assert "out_time_us" in stdout


@pytest.mark.asyncio
async def test_raises_on_nonzero_exit_even_while_tracking_progress():
    async def on_progress(fraction):
        pass

    command = r"printf 'out_time_us=1000000\n'; exit 1"

    with pytest.raises(ChildProcessError):
        await Task(command, duration_s=10, on_progress=on_progress).execute()


@pytest.mark.asyncio
async def test_run_reports_progress_without_handing_the_stream_back():
    calls = []

    async def on_progress(fraction):
        calls.append(fraction)

    command = r"printf 'out_time_us=2000000\nprogress=continue\nout_time_us=5000000\nprogress=end\n'"
    assert await Task(command, duration_s=10, on_progress=on_progress).run() is None

    assert calls == [0.2, 0.5, 1.0]


@pytest.mark.asyncio
async def test_run_still_says_what_the_command_last_complained_about():
    """A discarded stream is still worth its tail: that is the failure message."""
    command = r"printf 'padding %s\n' $(seq 5000) >&2; echo 'Conversion failed!' >&2; exit 1"

    with pytest.raises(ChildProcessError) as failure:
        await Task(command).run()

    assert "Conversion failed!" in str(failure.value)
    assert "padding 1\n" not in str(failure.value)
    assert len(str(failure.value)) < 2 * FAILURE_TAIL_BYTES + 200


@pytest.mark.asyncio
async def test_run_holds_no_more_of_a_long_progress_stream_than_a_short_one():
    """The retained size must not track the encode's duration -- that is the point."""

    async def on_progress(fraction):
        pass

    async def peak_bytes_for(blocks: int) -> int:
        command = rf"for i in $(seq {blocks}); do printf 'out_time_us=1000000\nprogress=continue\n'; done"
        tracemalloc.start()
        try:
            await Task(command, duration_s=10, on_progress=on_progress).run()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return peak

    short = await peak_bytes_for(200)
    long = await peak_bytes_for(20_000)

    # A hundredfold longer stream is ~750 kB of progress lines, which the
    # retaining version held as several times that in bytes objects.
    assert long < short + 200_000
