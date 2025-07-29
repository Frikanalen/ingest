import shutil

import pytest

import app.media.loudness.get_loudness


def test_get_loudness():
    if not can_get_loudness():
        pytest.skip("bs1770gain missing")

    # sine.wav was generated using sox -b 16 -n sine.wav synth 3 sine 300-3300
    # white.png was generated using convert xc:white white.png
    assert app.media.loudness.get_loudness.get_loudness("../tests/data/sine.wav") == {
        "integrated_lufs": -2.2,
        "truepeak_lufs": 0.54,
    }
    assert app.media.loudness.get_loudness.get_loudness("../tests/data/white.jpg") is None


def can_get_loudness():
    return shutil.which("bs1770gain") is not None
