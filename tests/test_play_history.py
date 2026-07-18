import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import PlayHistoryDatabase  # noqa: E402


class PlayHistoryTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = PlayHistoryDatabase(os.path.join(self.dir.name, 'music.db'))

    def tearDown(self):
        self.dir.cleanup()

    def seed(self):
        # deterministic timestamps: two days, known local hours
        base = time.mktime((2026, 7, 1, 20, 0, 0, 0, 0, -1))  # 20:00 local
        self.db.record('a', 'Song A', 'url', 'alice', 180, base)
        self.db.record('a', 'Song A', 'url', 'bob', 180, base + 3600)      # 21:00
        self.db.record('b', 'Song B', 'file', 'alice', 240, base + 7200)   # 22:00
        self.db.record('c', 'Song C', 'url', 'carol', 0, base + 86400)     # next day 20:00
        return base

    def test_record_and_totals(self):
        self.seed()
        stats = self.db.stats()
        self.assertEqual(4, stats['total_plays'])
        self.assertEqual(600, stats['total_seconds'])
        self.assertEqual(3, stats['unique_tracks'])

    def test_top_tracks_and_users(self):
        self.seed()
        stats = self.db.stats()
        self.assertEqual('a', stats['top_tracks'][0]['item_id'])
        self.assertEqual(2, stats['top_tracks'][0]['count'])
        self.assertEqual('alice', stats['top_users'][0]['user'])
        self.assertEqual(2, stats['top_users'][0]['count'])

    def test_by_type(self):
        self.seed()
        by_type = {row['type']: row['count'] for row in self.db.stats()['by_type']}
        self.assertEqual({'url': 3, 'file': 1}, by_type)

    def test_hour_histogram(self):
        self.seed()
        hours = self.db.stats()['hours']
        self.assertEqual(24, len(hours))
        self.assertEqual(2, hours[20])  # two plays at 20:00 local
        self.assertEqual(1, hours[21])
        self.assertEqual(1, hours[22])
        self.assertEqual(4, sum(hours))

    def test_busiest_day(self):
        self.seed()
        busiest = self.db.stats()['busiest_day']
        self.assertEqual('2026-07-01', busiest['date'])
        self.assertEqual(3, busiest['count'])

    def test_mark_skipped_hits_latest_play(self):
        base = self.seed()
        self.db.record('a', 'Song A', 'url', 'dave', 180, base + 999999)
        self.db.mark_skipped('a')
        stats = self.db.stats()
        self.assertEqual(1, len(stats['most_skipped']))
        self.assertEqual('a', stats['most_skipped'][0]['item_id'])
        self.assertEqual(1, stats['most_skipped'][0]['count'])
        # only the latest row for 'a' was flagged
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        flagged = conn.execute(
            "SELECT user FROM play_history WHERE skipped = 1").fetchall()
        conn.close()
        self.assertEqual([('dave',)], flagged)

    def test_mark_skipped_unknown_item_is_noop(self):
        self.seed()
        self.db.mark_skipped('zzz')
        self.assertEqual([], self.db.stats()['most_skipped'])

    def test_empty_db_stats(self):
        stats = self.db.stats()
        self.assertEqual(0, stats['total_plays'])
        self.assertEqual([0] * 24, stats['hours'])
        self.assertIsNone(stats['busiest_day'])
        self.assertEqual([], stats['top_tracks'])


class LaunchInstrumentationTestCase(unittest.TestCase):
    """launch_music must append exactly one history row per fresh start."""

    def test_record_on_fresh_start_only(self):
        import variables as var
        from bot.player import PlayerMixin

        class Recorder:
            rows = []

            def record(self, *args, **kwargs):
                self.rows.append(args)

        class FakeItem:
            id = 'x1'
            type = 'url'
            duration = 100

            def format_title(self):
                return 'T'

        class FakeWrapper:
            user = 'alice'

            def item(self):
                return FakeItem()

            def uri(self):
                return '/nope'

            def is_ready(self):
                return True

            def format_debug_string(self):
                return 'dbg'

            def format_current_playing(self):
                return 'np'

            def playable_from(self, *a):
                return True

        saved = var.play_history
        var.play_history = Recorder()
        try:
            player = PlayerMixin.__new__(PlayerMixin)
            import logging
            player.log = logging.getLogger('test')
            recorded = []
            Recorder.rows = recorded

            # Only exercise the instrumentation block, not the ffmpeg launch:
            # replicate the guard exactly by calling launch_music and catching
            # the inevitable failure when it reaches config access.
            for start_from in (0, 42.5):
                try:
                    player.launch_music(FakeWrapper(), start_from)
                except Exception:
                    pass
            self.assertEqual(1, len(recorded))
            self.assertEqual(('x1', 'T', 'url', 'alice', 100), recorded[0])
        finally:
            var.play_history = saved


if __name__ == '__main__':
    unittest.main()
