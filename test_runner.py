import runner


def test_basic_run(tmp_path):
    fn = tmp_path / "testfile"
    fn.write_text("dummy content")
    assert fn.exists()

    runner.Runner.run(f"rm {fn}")

    assert not fn.exists()
