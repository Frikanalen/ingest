import pytest

from runner import Runner


@pytest.mark.asyncio
async def test_basic_run(tmp_path):
    test_path = tmp_path / "testfile"
    test_path.touch()
    assert test_path.exists()

    run = await Runner().run(f"rm {test_path}")
    await run.wait_for_completion()

    assert not test_path.exists()
