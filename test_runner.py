import os
import tempfile
import unittest

import runner


class ProcessRunner(unittest.TestCase):
    def test_basic_run(self):
        _, fn = tempfile.mkstemp()
        self.assertTrue(os.path.exists(fn))
        cmd = runner.Runner.run(["rm", fn])
        self.assertFalse(os.path.exists(fn))
