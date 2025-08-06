import pytest

from app.task_builder import build_task


@pytest.mark.asyncio
async def test_basic_run(tmp_path):
    test_path = tmp_path / "testfile"
    test_path.touch()
    assert test_path.exists()

    await build_task(f"rm {test_path}").execute()

    assert not test_path.exists()
