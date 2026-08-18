import pytest

from app.runner import Task


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
