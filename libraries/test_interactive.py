import logging
import shutil
import unittest

from libraries import interactive


class ProcessGenerate(unittest.TestCase):
    def setUp(self):
        logging.getLogger("").setLevel(logging.WARNING)

    def test_generate(self):
        tests = (
            (
                "/tmp/original/test.ogv",
                [
                    "/tmp/large_thumb/test.jpg",
                    "/tmp/broadcast/test.dv",
                    "/tmp/theora/test.ogv",
                ],
            ),
            (
                "/tmp/broadcast/test.ogv",
                ["/tmp/large_thumb/test.jpg", "/tmp/theora/test.ogv"],
            ),
        )
        nop = lambda *_, **__: None
        for t in tests:
            in_fn, out_fns = t
            cmds = []

            interactive.generate_videos(
                0, in_fn, runner_run=lambda c, **__: cmds.append(c), register=nop
            )

            self.assertEqual(out_fns, [c[-1] for c in cmds])

    def test_get_loudness(self):
        # bs1770gain not exising shouldn't fail tests
        if not can_get_loudness():
            return self.skipTest("bs1770gain missing")
        # sine.wav was generated using sox -b 16 -n sine.wav synth 3 sine 300-3300
        # white.png was generated using convert xc:white white.png
        self.assertEqual(
            measure_loudness.get_loudness("tests/data/sine.wav"),
            {"integrated_lufs": -2.2, "truepeak_lufs": 0.54},
        )
        self.assertEqual(measure_loudness.get_loudness("tests/data/white.jpg"), None)

    def test_generate_wrong_format(self):
        self.assertRaises(AssertionError, lambda: interactive.generate_videos(0, "/a/b/c.d"))
        self.assertRaises(AssertionError, lambda: interactive.generate_videos(0, "/a/theora/c.d"))


def can_get_loudness():
    return shutil.which("bs1770gain")
