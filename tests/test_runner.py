import pytest

from app.runner import Task


@pytest.mark.asyncio
async def test_basic_run(tmp_path):
    test_path = tmp_path / "testfile"
    test_path.touch()
    assert test_path.exists()

    await Task(f"rm {test_path}").execute()

    assert not test_path.exists()
