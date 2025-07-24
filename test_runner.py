import runner


def test_basic_run(tmp_path):
    test_path = tmp_path / "testfile"
    test_path.touch()
    assert test_path.exists()

    runner.Runner.run(f"rm {test_path}")

    assert not test_path.exists()
