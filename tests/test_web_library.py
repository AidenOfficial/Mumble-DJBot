# coding=utf-8
"""/api/library/*:曲库信息、检索、加入队列(真 sqlite 曲库 + Flask test client)。"""
import configparser
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
from database import MusicDatabase  # noqa: E402


def no_auth(f):
    return f


class FakeLock:
    def locked(self):
        return False


class FakeCache(dict):
    dir_lock = FakeLock()

    def get_item_by_id(self, _id):
        if _id in self:
            return self[_id]
        rows = var.music_db.query_music_by_id(_id)
        if not rows:
            return None
        from media.item import dict_to_item
        self[_id] = dict_to_item(rows)
        return self[_id]


class FakePlaylist(list):
    current_index = 0

    def append(self, item):
        super().append(item)

    def extend(self, items):
        super().extend(items)


class FakeBot:
    downloads = 0

    def async_download_next(self):
        self.downloads += 1


def file_row(name, tags=(), keywords=None, title=None):
    """曲库里的一条本地文件记录;文件本身也建出来(FileItem 加载时会校验文件存在)。"""
    import hashlib
    if var.music_folder:
        os.makedirs(os.path.dirname(var.music_folder + name), exist_ok=True)
        open(var.music_folder + name, 'ab').close()
    return {'id': hashlib.md5(name.encode()).hexdigest(), 'type': 'file', 'path': name,
            'title': title or os.path.basename(name), 'tags': list(tags),
            'keywords': keywords or name.lower(), 'ready': 'yes', 'artist': 'Someone',
            'thumbnail': None, 'duration': 30}


class WebLibraryTestCase(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        import importlib
        importlib.import_module("media.file")  # 注册 file 类型
        import web_library
        import constants
        constants.load_lang('en_US')
        self._saved = {n: getattr(var, n, None)
                       for n in ('bot', 'playlist', 'cache', 'config', 'music_db', 'music_folder')}
        self.tmp = tempfile.TemporaryDirectory()
        var.music_folder = os.path.join(self.tmp.name, 'music') + '/'
        var.music_db = MusicDatabase(os.path.join(self.tmp.name, 'music.db'))
        from database import DatabaseMigration

        class _Settings:
            def get(self, *a, **k):
                return k.get('fallback')

            def set(self, *a, **k):
                pass

            def has_option(self, *a):
                return False

        DatabaseMigration(_Settings(), var.music_db).music_database_migrate()
        var.cache = FakeCache()
        var.playlist = FakePlaylist()
        var.bot = FakeBot()
        config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
        config.read_dict({'webinterface': {'upload_enabled': 'True', 'max_upload_file_size': '10M'}})
        var.config = config

        var.music_db.insert_music(file_row('rock/a.mp3', tags=['fav']))
        var.music_db.insert_music(file_row('rock/b.mp3'))
        var.music_db.insert_music(file_row('jazz/c.mp3', tags=['fav', 'night']))

        app = Flask(__name__)
        app.register_blueprint(web_library.create_blueprint(no_auth))
        self.client = app.test_client()

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(var, name, value)
        self.tmp.cleanup()

    def query(self, **body):
        rv = self.client.post('/api/library/query', json=body)
        self.assertEqual(200, rv.status_code, rv.data)
        return rv.get_json()

    def test_info(self):
        data = self.client.get('/api/library/info').get_json()
        self.assertEqual({'.', 'rock', 'jazz'}, set(data['dirs']))
        self.assertEqual({'fav', 'night'}, set(data['tags']))
        self.assertTrue(data['upload_enabled'])
        self.assertEqual(10 * 1024 * 1024, data['max_upload_file_size'])

    def test_query_all(self):
        data = self.query(page=1)
        self.assertEqual(3, len(data['items']))
        self.assertEqual(1, data['total_pages'])
        item = data['items'][0]
        self.assertEqual({'id', 'title', 'type', 'tags', 'thumb', 'path', 'artist'}, set(item))
        self.assertIsInstance(item['tags'], list)
        self.assertEqual('', item['thumb'])

    def test_query_filters(self):
        self.assertEqual(['c.mp3'], [i['title'] for i in self.query(tags='night')['items']])
        self.assertEqual(2, len(self.query(tags='fav')['items']))
        self.assertEqual(['b.mp3'], [i['title'] for i in self.query(keywords='rock b.mp3')['items']])
        self.assertEqual(2, len(self.query(type='file', dir='rock')['items']))
        self.assertEqual([], self.query(type='url')['items'])

    def test_query_rejects_unknown_type(self):
        rv = self.client.post('/api/library/query', json={'type': 'file,evil'})
        self.assertEqual(400, rv.status_code)

    def test_pagination(self):
        for i in range(12):
            var.music_db.insert_music(file_row(f'many/{i:02d}.mp3'))
        first, second = self.query(page=1), self.query(page=2)
        self.assertEqual(2, first['total_pages'])
        self.assertEqual(10, len(first['items']))
        self.assertEqual(5, len(second['items']))
        self.assertEqual(1, self.query(page=99)['active_page'])

    def test_enqueue_append_and_next(self):
        a = file_row('rock/a.mp3')['id']
        c = file_row('jazz/c.mp3')['id']
        rv = self.client.post('/api/library/enqueue', json={'id': a})
        self.assertEqual(200, rv.status_code)
        rv = self.client.post('/api/library/enqueue', json={'id': c, 'mode': 'next'})
        self.assertEqual(200, rv.status_code)
        self.assertEqual([a, c], [w.id for w in var.playlist])
        self.assertEqual(2, var.bot.downloads)

    def test_enqueue_unknown_item(self):
        self.assertEqual(404, self.client.post('/api/library/enqueue', json={'id': 'nope'}).status_code)
        self.assertEqual(400, self.client.post('/api/library/enqueue',
                                               json={'id': 'x', 'mode': 'replace'}).status_code)


if __name__ == '__main__':
    unittest.main()
