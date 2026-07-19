# coding=utf-8
"""clear_tmp_folder must survive files vanishing mid-scan (concurrent
downloads / yt-dlp temp files / the cache cleaner all delete things in
tmp_folder while it runs) - regression for a FileNotFoundError that took
the whole bot down."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import util  # noqa: E402


class ClearTmpFolderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cleartmp_")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for name in os.listdir(self.dir):
            try:
                os.remove(os.path.join(self.dir, name))
            except OSError:
                pass
        os.rmdir(self.dir)

    def make_file(self, name, mb, age=0):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(b"\0" * (mb * 1024 * 1024))
        if age:
            t = time.time() - age
            os.utime(path, (t, t))
        return path

    def test_removes_oldest_when_over_limit(self):
        old = self.make_file("old", 2, age=3600)
        new = self.make_file("new", 2, age=60)
        util.clear_tmp_folder(self.dir, 3)  # 4 MB present, 3 MB allowed
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))

    def test_under_limit_removes_nothing(self):
        keep = self.make_file("keep", 1)
        util.clear_tmp_folder(self.dir, 100)
        self.assertTrue(os.path.exists(keep))

    def test_minus_one_disables_cleanup(self):
        keep = self.make_file("keep", 2)
        util.clear_tmp_folder(self.dir, -1)
        self.assertTrue(os.path.exists(keep))

    def test_survives_file_vanishing_between_list_and_stat(self):
        self.make_file("a", 2, age=3600)
        self.make_file("b", 2, age=60)
        ghost = os.path.join(self.dir, "ghost")

        real_listdir = os.listdir

        def listdir_with_ghost(p):
            # simulate another thread deleting a file right after the scan
            return real_listdir(p) + ["ghost"]

        with mock.patch.object(util.os, "listdir", side_effect=listdir_with_ghost):
            util.clear_tmp_folder(self.dir, 3)  # must not raise
        self.assertFalse(os.path.exists(ghost))

    def test_subdirectories_are_left_alone(self):
        sub = os.path.join(self.dir, "req_spotify")
        os.mkdir(sub)
        inner = os.path.join(sub, "song.opus")
        with open(inner, "wb") as f:
            f.write(b"\0" * (4 * 1024 * 1024))
        self.addCleanup(lambda: (os.remove(inner), os.rmdir(sub)))
        util.clear_tmp_folder(self.dir, 1)  # over limit only because of subdir
        self.assertTrue(os.path.exists(inner))


if __name__ == "__main__":
    unittest.main()
