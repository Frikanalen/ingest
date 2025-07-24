import pytest

import runner


@pytest.mark.asyncio
async def test_basic_run(tmp_path):
    test_path = tmp_path / "testfile"
    test_path.touch()
    assert test_path.exists()

    await runner.Runner().run(["rm", str(test_path)])

    assert not test_path.exists()
