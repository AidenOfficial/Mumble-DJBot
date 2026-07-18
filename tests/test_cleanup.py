import configparser
import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
from database import SettingsDatabase, MusicDatabase, DatabaseMigration  # noqa: E402
from bot.cleanup import CacheCleaner, DAY_SECONDS  # noqa: E402


NOW = 1_700_000_000.0


def hex_id(seed):
    return hashlib.md5(seed.encode()).hexdigest()


class FakeWrapper:
    def __init__(self, path):
        self.path = path

    def uri(self):
        return self.path


class FakeItem:
    def __init__(self, path, downloading=False, ready='yes'):
        self.path = path
        self.downloading = downloading
        self.ready = ready


class CleanupTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        base = self.root.name
        self.tmp_folder = os.path.join(base, 'tmp') + os.sep
        self.spotify_folder = os.path.join(base, 'spotdl_cache')
        self.music_folder = os.path.join(base, 'music') + os.sep
        os.makedirs(self.tmp_folder)
        os.makedirs(self.spotify_folder)
        os.makedirs(self.music_folder)

        config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
        config.add_section('bot')
        config.set('bot', 'cleanup_interval_days', '7')
        config.set('bot', 'cleanup_keep_days', '7')
        config.add_section('spotify')
        config.set('spotify', 'download_folder', self.spotify_folder)

        self._saved = {name: getattr(var, name, None) for name in
                       ('config', 'db', 'music_db', 'playlist', 'cache',
                        'tmp_folder', 'music_folder')}
        var.config = config
        var.db = SettingsDatabase(os.path.join(base, 'settings.db'))
        var.music_db = MusicDatabase(os.path.join(base, 'music.db'))
        DatabaseMigration(var.db, var.music_db).migrate()
        var.playlist = []
        var.cache = {}
        var.tmp_folder = self.tmp_folder
        var.music_folder = self.music_folder

        self.clock_now = NOW
        self.cleaner = CacheCleaner(clock=lambda: self.clock_now)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(var, name, value)
        self.root.cleanup()

    def make_file(self, folder, name, age_days=0.0):
        path = os.path.join(folder, name)
        with open(path, 'wb') as f:
            f.write(b'x' * 16)
        mtime = self.clock_now - age_days * DAY_SECONDS
        os.utime(path, (mtime, mtime))
        return path

    def insert_url_record(self, url, path, ready='yes'):
        _id = hashlib.md5(url.encode()).hexdigest()
        var.music_db.insert_music({
            'id': _id, 'type': 'url', 'title': 'a title', 'url': url,
            'path': path, 'duration': 60, 'thumbnail': '', 'ready': ready,
            'tags': [], 'keywords': 'a title', 'user': 'tester'})
        return _id

    def insert_file_record(self, path, ready='yes'):
        _id = hashlib.md5(path.encode()).hexdigest()
        var.music_db.insert_music({
            'id': _id, 'type': 'file', 'title': 'spotify track',
            'artist': 'x', 'path': path, 'duration': 60, 'thumbnail': '',
            'ready': ready, 'tags': [], 'keywords': 'spotify track',
            'user': 'tester'})
        return _id

    # ---- sweep behaviour --------------------------------------------------

    def test_expired_hex_cache_file_is_removed(self):
        path = self.make_file(self.tmp_folder, hex_id('a'), age_days=10)
        removed = self.cleaner.run_once()
        self.assertEqual([os.path.abspath(path)], removed)
        self.assertFalse(os.path.exists(path))

    def test_recent_file_is_kept(self):
        path = self.make_file(self.tmp_folder, hex_id('a'), age_days=3)
        self.cleaner.run_once()
        self.assertTrue(os.path.exists(path))

    def test_foreign_tmp_file_is_never_touched(self):
        # tmp_folder is /tmp/ by default and shared with other software
        path = self.make_file(self.tmp_folder, 'some-other-app.sock', age_days=30)
        self.cleaner.run_once()
        self.assertTrue(os.path.exists(path))

    def test_partial_download_is_kept(self):
        path = self.make_file(self.tmp_folder, hex_id('a') + '.part', age_days=30)
        self.cleaner.run_once()
        self.assertTrue(os.path.exists(path))

    def test_playlist_referenced_file_is_kept(self):
        path = self.make_file(self.tmp_folder, hex_id('a'), age_days=30)
        var.playlist = [FakeWrapper(path)]
        self.cleaner.run_once()
        self.assertTrue(os.path.exists(path))

    def test_downloading_item_is_kept(self):
        path = self.make_file(self.tmp_folder, hex_id('a'), age_days=30)
        var.cache = {'someid': FakeItem(path, downloading=True)}
        self.cleaner.run_once()
        self.assertTrue(os.path.exists(path))

    def test_music_folder_is_never_swept(self):
        path = self.make_file(self.music_folder, hex_id('a'), age_days=365)
        var.tmp_folder = self.music_folder  # worst-case misconfiguration
        self.cleaner.run_once()
        self.assertTrue(os.path.exists(path))

    def test_db_pathed_non_hex_tmp_file_is_removed(self):
        path = self.make_file(self.tmp_folder, 'oddly-named-download', age_days=30)
        self.insert_url_record('https://example.com/a', path)
        removed = self.cleaner.run_once()
        self.assertIn(os.path.abspath(path), removed)

    def test_spotify_cache_file_is_removed_with_db_record(self):
        path = self.make_file(self.spotify_folder, 'Artist - Song.opus', age_days=30)
        _id = self.insert_file_record(path)
        self.cleaner.run_once()
        self.assertFalse(os.path.exists(path))
        self.assertIsNone(var.music_db.query_music_by_id(_id))

    def test_relative_file_records_survive(self):
        # local library records store a relative path; they must never be
        # deleted from the db even if a same-named tmp file was removed
        self.make_file(self.tmp_folder, hex_id('a'), age_days=30)
        _id = self.insert_file_record('subdir/song.mp3')
        self.cleaner.run_once()
        self.assertIsNotNone(var.music_db.query_music_by_id(_id))

    def test_url_record_is_invalidated_not_deleted(self):
        path = self.make_file(self.tmp_folder, hex_id('u'), age_days=30)
        _id = self.insert_url_record('https://example.com/u', path, ready='yes')
        var.cache = {_id: FakeItem(path, ready='yes')}
        self.cleaner.run_once()
        record = var.music_db.query_music_by_id(_id)
        self.assertIsNotNone(record)
        self.assertEqual('validated', record['ready'])
        self.assertEqual('validated', var.cache[_id].ready)

    # ---- scheduling -------------------------------------------------------

    def test_due_immediately_when_never_run(self):
        self.assertLessEqual(self.cleaner.seconds_until_due(), 0)

    def test_not_due_after_run_and_persisted_across_instances(self):
        self.cleaner.run_once()
        self.assertAlmostEqual(7 * DAY_SECONDS,
                               self.cleaner.seconds_until_due(), delta=1)
        # a fresh instance (i.e. a bot restart) sees the same schedule
        second = CacheCleaner(clock=lambda: self.clock_now + 3 * DAY_SECONDS)
        self.assertAlmostEqual(4 * DAY_SECONDS,
                               second.seconds_until_due(), delta=1)
        third = CacheCleaner(clock=lambda: self.clock_now + 8 * DAY_SECONDS)
        self.assertLessEqual(third.seconds_until_due(), 0)

    def test_run_records_timestamp_in_settings_db(self):
        self.cleaner.run_once()
        self.assertAlmostEqual(NOW, var.db.getfloat('cleanup', 'last_run'), delta=1)

    def test_disabled_by_interval_zero(self):
        var.config.set('bot', 'cleanup_interval_days', '0')
        self.cleaner.start()
        self.assertIsNone(self.cleaner._thread)

    def test_keep_days_zero_still_protects_very_recent_files(self):
        var.config.set('bot', 'cleanup_keep_days', '0')
        fresh = self.make_file(self.tmp_folder, hex_id('fresh'), age_days=0)
        old = self.make_file(self.tmp_folder, hex_id('old'), age_days=1)
        self.cleaner.run_once()
        self.assertTrue(os.path.exists(fresh))   # under MIN_AGE_SECONDS
        self.assertFalse(os.path.exists(old))


if __name__ == '__main__':
    unittest.main()
