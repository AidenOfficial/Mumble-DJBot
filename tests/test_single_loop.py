# coding=utf-8
"""Single-track loop mode: the song repeats on natural end, a user skip
advances one step (wrapping like repeat mode)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from media.playlist import BasePlaylist, SingleLoopPlaylist, get_playlist  # noqa: E402


class W:
    def __init__(self, id_):
        self.id = id_


def make(n=3):
    pl = SingleLoopPlaylist()
    # bypass append()/extend() - they spawn validation threads on real caches
    list.extend(pl, [W(i) for i in range(n)])
    return pl


class SingleLoopTest(unittest.TestCase):
    def test_natural_end_repeats_current(self):
        pl = make()
        first = pl.next()
        self.assertEqual(first.id, 0)
        self.assertEqual(pl.next().id, 0)
        self.assertEqual(pl.next().id, 0)
        self.assertEqual(pl.current_index, 0)

    def test_skip_advances_once_then_loops_there(self):
        pl = make()
        pl.next()
        pl.skip_current()
        self.assertEqual(pl.next().id, 1)   # the skip advanced
        self.assertEqual(pl.next().id, 1)   # and we loop on the new song

    def test_skip_wraps_around(self):
        pl = make(2)
        pl.next()
        pl.skip_current()
        pl.next()                            # -> index 1
        pl.skip_current()
        self.assertEqual(pl.next().id, 0)    # wrapped

    def test_empty_playlist(self):
        pl = make(0)
        self.assertFalse(pl.next())
        self.assertEqual(pl.current_index, -1)

    def test_next_item_is_current_and_no_prefetch(self):
        pl = make()
        pl.next()
        self.assertEqual(pl.next_item().id, 0)
        self.assertEqual(pl.upcoming_items(5), [])

    def test_get_playlist_wires_single_and_preserves_index(self):
        pl = make()
        pl.next()
        pl.skip_current()
        pl.next()  # index 1
        converted = get_playlist("single", pl)
        self.assertEqual(converted.mode, "single")
        self.assertEqual(converted.current_index, 1)

    def test_base_skip_current_is_noop(self):
        pl = BasePlaylist()
        pl.skip_current()  # must not raise or change anything
        self.assertEqual(pl.current_index, -1)


if __name__ == "__main__":
    unittest.main()
