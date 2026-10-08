"""缓存管理:枚举、固定、LRU 淘汰、手动删除、存入曲库、定期清理的冷热判断。"""
import configparser
import hashlib
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
from bot import cache_store  # noqa: E402
from bot.cleanup import CacheCleaner, DAY_SECONDS  # noqa: E402
from database import DatabaseMigration, MusicDatabase, PlayHistoryDatabase, SettingsDatabase  # noqa: E402

MB = 1024 * 1024


def hid(seed):
    return hashlib.md5(seed.encode()).hexdigest()


class Wrapper:
    def __init__(self, _id):
        self.id = _id

    def uri(self):
        return os.path.join(var.tmp_folder, self.id)


class Item:
    def __init__(self, _id, downloading=False, ready='yes'):
        self.id = _id
        self.path = os.path.join(var.tmp_folder, _id)
        self.downloading = downloading
        self.ready = ready


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        base = self.root.name
        self._saved = {n: getattr(var, n, None) for n in
                       ('config', 'db', 'music_db', 'playlist', 'cache', 'tmp_folder',
                        'music_folder', 'play_history', 'cleaner')}
        var.tmp_folder = os.path.join(base, 'cache') + os.sep
        var.music_folder = os.path.join(base, 'music') + os.sep
        os.makedirs(var.tmp_folder)
        os.makedirs(var.music_folder)
        config = configparser.ConfigParser(interpolation=None)
        config.read_dict({'bot': {'tmp_folder_max_size': '10', 'cache_auto_keep_plays': '3',
                                  'cleanup_interval_days': '7', 'cleanup_keep_days': '7'},
                          'spotify': {'download_folder': ''}})
        var.config = config
        var.db = SettingsDatabase(os.path.join(base, 'settings.db'))
        var.music_db = MusicDatabase(os.path.join(base, 'music.db'))
        DatabaseMigration(var.db, var.music_db).migrate()
        var.play_history = PlayHistoryDatabase(var.music_db.db_path)
        var.playlist = []
        var.cache = {}

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(var, n, v)
        self.root.cleanup()

    def make(self, seed, size_mb=1, age_days=30, title=None, with_cover=True):
        _id = hid(seed)
        path = os.path.join(var.tmp_folder, _id)
        with open(path, 'wb') as f:
            f.write(b'\x1aE\xdf\xa3' + b'x' * (int(size_mb * MB) - 4))
        files = [path]
        if with_cover:
            files.append(path + '.jpg')
            with open(path + '.jpg', 'wb') as f:
                f.write(b'jpg')
        mtime = time.time() - age_days * DAY_SECONDS
        for p in files:
            os.utime(p, (mtime, mtime))
        var.music_db.insert_music({
            'id': _id, 'type': 'url', 'title': title or seed, 'url': f'https://e.com/{seed}',
            'path': path, 'duration': 60, 'thumbnail': '', 'ready': 'yes', 'tags': [],
            'keywords': seed})
        return _id

    def play(self, _id, times=1, days_ago=0.0):
        for _ in range(times):
            var.play_history.record(_id, 't', 'url', 'u', 60, played_at=time.time() - days_ago * DAY_SECONDS)

    def exists(self, _id):
        return os.path.exists(os.path.join(var.tmp_folder, _id))


class ListingTest(Base):
    def test_entries_group_files_and_join_metadata(self):
        a = self.make('a', size_mb=2, title='Song A')
        self.play(a, 4, days_ago=1)
        cache_store.set_pinned(a, True)
        var.playlist = [Wrapper(a)]
        with open(os.path.join(var.tmp_folder, 'not-ours.sock'), 'w') as f:
            f.write('x')
        [e] = cache_store.list_entries()
        self.assertEqual(e['id'], a)
        self.assertEqual(e['title'], 'Song A')
        self.assertEqual(e['url'], 'https://e.com/a')
        self.assertEqual(e['files'], 2)
        self.assertEqual(e['size'], 2 * MB + 3)
        self.assertEqual(e['plays'], 4)
        self.assertTrue(e['pinned'] and e['frequent'] and e['in_queue'] and e['complete'])
        s = cache_store.summary()
        self.assertEqual(s['limit_bytes'], 10 * MB)
        self.assertTrue(s['persistent'] or s['folder'].startswith('/tmp'))

    def test_partial_download_flagged(self):
        a = self.make('a')
        open(os.path.join(var.tmp_folder, a + '.incomplete'), 'w').close()
        [e] = cache_store.list_entries()
        self.assertFalse(e['complete'])
        self.assertTrue(e['downloading'])


class EvictionTest(Base):
    def test_lru_respects_pins_queue_and_frequency(self):
        old = self.make('old', size_mb=4, age_days=30)
        newer = self.make('newer', size_mb=4, age_days=2)
        frequent = self.make('freq', size_mb=4, age_days=40)
        self.play(frequent, 5, days_ago=40)
        pinned = self.make('pin', size_mb=4, age_days=50)
        cache_store.set_pinned(pinned, True)
        queued = self.make('queued', size_mb=4, age_days=60)
        var.playlist = [Wrapper(queued)]
        var.config.set('bot', 'tmp_folder_max_size', '13')
        # 共 20MB,上限 13MB:先删普通里最旧的 old,再删 newer,够了就停;
        # 常听的 freq 留到最后,固定和队列里的永远不碰
        freed, removed = cache_store.enforce_size_limit()
        self.assertEqual(removed, [old, newer])
        self.assertTrue(self.exists(frequent) and self.exists(pinned) and self.exists(queued))
        self.assertGreaterEqual(freed, 8 * MB)
        # 被淘汰的 url 记录回到 validated,下次点播会重新下载
        self.assertEqual(var.music_db.query_music_by_id(old)['ready'], 'validated')

    def test_recently_played_counts_as_recently_used(self):
        a = self.make('a', size_mb=6, age_days=30)
        b = self.make('b', size_mb=6, age_days=5)
        self.play(a, 1, days_ago=0)  # 下载得早但刚播过
        _, removed = cache_store.enforce_size_limit()
        self.assertEqual(removed, [b])

    def test_unlimited(self):
        var.config.set('bot', 'tmp_folder_max_size', '-1')
        self.make('a', size_mb=30)
        self.assertEqual(cache_store.enforce_size_limit(), (0, []))

    def test_clear_unpinned(self):
        a, b = self.make('a'), self.make('b')
        cache_store.set_pinned(b, True)
        cache_store.clear_unpinned()
        self.assertFalse(self.exists(a))
        self.assertTrue(self.exists(b))


class ManualTest(Base):
    def test_delete_refuses_downloading_and_queued_unless_forced(self):
        a = self.make('a')
        var.cache = {a: Item(a, downloading=True)}
        self.assertIsNone(cache_store.delete_entry(a))
        var.cache = {a: Item(a)}
        var.playlist = [Wrapper(a)]
        self.assertIsNone(cache_store.delete_entry(a))
        self.assertGreater(cache_store.delete_entry(a, force=True), 0)
        self.assertFalse(self.exists(a))
        self.assertEqual(var.cache[a].ready, 'validated')

    def test_delete_rejects_bad_ids(self):
        self.assertIsNone(cache_store.delete_entry('../../etc/passwd'))

    def test_save_to_library(self):
        a = self.make('a', title='夜に駆ける / YOASOBI')
        rel = cache_store.save_to_library(a)
        self.assertTrue(rel.startswith('saved' + os.sep))
        self.assertNotIn('/', os.path.basename(rel))
        self.assertTrue(os.path.isfile(os.path.join(var.music_folder, rel)))
        self.assertTrue(os.path.isfile(os.path.splitext(os.path.join(var.music_folder, rel))[0] + '.jpg'))
        records = var.music_db.query_music(__import__('database').Condition().and_equal('type', 'file'))
        self.assertEqual(records[0]['title'], '夜に駆ける / YOASOBI')
        # 再存一次不覆盖
        rel2 = cache_store.save_to_library(a)
        self.assertNotEqual(rel, rel2)


class SweepTest(Base):
    def test_sweep_skips_pinned_frequent_and_recently_played(self):
        plain = self.make('plain', age_days=30)
        pinned = self.make('pinned', age_days=30)
        cache_store.set_pinned(pinned, True)
        frequent = self.make('freq', age_days=30)
        self.play(frequent, 3, days_ago=20)
        recent = self.make('recent', age_days=30)
        self.play(recent, 1, days_ago=2)
        CacheCleaner().run_once()
        self.assertFalse(self.exists(plain))
        self.assertTrue(self.exists(pinned) and self.exists(frequent) and self.exists(recent))


class ApiTest(Base):
    def setUp(self):
        super().setUp()
        from flask import Flask
        import web_cache
        app = Flask(__name__)
        app.register_blueprint(web_cache.create_blueprint(lambda f: f))
        self.client = app.test_client()

    def test_overview_pin_delete_cleanup(self):
        a = self.make('a', size_mb=6)
        b = self.make('b', size_mb=6, age_days=1)
        data = self.client.get('/api/cache').get_json()
        self.assertEqual(data['summary']['count'], 2)
        self.assertEqual(self.client.post('/api/cache/pin', json={'id': b, 'pinned': True}).status_code, 200)
        self.assertEqual(self.client.post('/api/cache/pin', json={'id': 'nope'}).status_code, 400)
        rv = self.client.post('/api/cache/cleanup', json={'mode': 'limit'}).get_json()
        self.assertGreater(rv['freed'], 0)
        self.assertFalse(self.exists(a))
        var.playlist = [Wrapper(b)]
        self.assertEqual(self.client.post('/api/cache/delete', json={'id': b}).status_code, 409)
        self.assertEqual(self.client.post('/api/cache/delete', json={'id': b, 'force': True}).status_code, 200)
        self.assertEqual(self.client.post('/api/cache/cleanup', json={'mode': 'bogus'}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
