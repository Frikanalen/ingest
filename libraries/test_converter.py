# class ProcessConvert(unittest.TestCase):
# def test_basic_theora_convert(self):
#     cmd, fn = Converter().convert_cmds(Path("/tmp/yadda/test.yey"), "theora")
#     self.assertEqual(cmd[0], "ffmpeg")
#     self.assertIn("libtheora", cmd)
#     self.assertEqual(cmd[-1], "/tmp/theora/test.ogv")
#     self.assertEqual(cmd[-1], fn)
#
# def test_extensionless_convert(self):
#     cmd, fn = Converter().convert_cmds(Path("/tmp/yadda/test"), "theora")
#     self.assertEqual(cmd[0], "ffmpeg")
#     self.assertEqual(cmd[-1], "/tmp/theora/test.ogv")
#     self.assertEqual(cmd[-1], fn)
