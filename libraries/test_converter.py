import unittest

from libraries import converter


class ProcessConvert(unittest.TestCase):
    def test_basic_theora_convert(self):
        cmd, fn = converter.Converter.convert_cmds("/tmp/yadda/test.yey", "theora")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("libtheora", cmd)
        self.assertEqual(cmd[-1], "/tmp/theora/test.ogv")
        self.assertEqual(cmd[-1], fn)

    def test_extensionless_convert(self):
        cmd, fn = converter.Converter.convert_cmds("/tmp/yadda/test", "theora")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertEqual(cmd[-1], "/tmp/theora/test.ogv")
        self.assertEqual(cmd[-1], fn)

    def test_basic_broadcast_convert(self):
        cmd, fn = converter.Converter.convert_cmds("/tmp/original/test.yey", "broadcast")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("pal-dv", cmd)
        self.assertEqual(cmd[-1], "/tmp/broadcast/test.dv")
        self.assertEqual(cmd[-1], fn)
