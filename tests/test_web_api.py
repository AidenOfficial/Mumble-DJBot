import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402


class FakeItem:
    def __init__(self, _id='abc123', title='A Song', duration=180,
                 thumbnail=None, url='https://example.com/v'):
        self.id = _id
        self.duration = duration
        self.thumbnail = thumbnail
        self.url = url
        self._title = title
        self.artist = 'Some Artist'

    def display_type(self):
        return 'url'

    def format_title(self):
        return self._title


class FakeWrapper:
    def __init__(self, item):
        self._item = item
        self.id = item.id

    def item(self):
        return self._item


class FakePlaylist(list):
    def __init__(self, wrappers=(), mode='one-shot'):
        super().__init__(wrappers)
        self.version = 7
        self.mode = mode
        self.current_index = 0 if wrappers else -1

    def current_item(self):
        if not len(self):
            return False
        return self[self.current_index]


class FakeVolume:
    plain_volume_set = 0.42


class FakeBot:
    def __init__(self):
        self.is_pause = False
        self.playhead = 33.5
        self.volume_helper = FakeVolume()
        self.is_ducking = False
        self.on_ducking = False


class FakeCache(dict):
    def get_item_by_id(self, _id):
        return self.get(_id)


def no_auth(f):
    return f


class WebApiTestCase(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        import web_api
        self._saved = {n: getattr(var, n, None)
                       for n in ('bot', 'playlist', 'cache')}
        var.bot = FakeBot()
        self.item = FakeItem()
        var.playlist = FakePlaylist([FakeWrapper(self.item)])
        var.cache = FakeCache({self.item.id: self.item})

        app = Flask(__name__)
        app.register_blueprint(web_api.create_blueprint(no_auth))
        self.client = app.test_client()

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(var, name, value)

    def test_status_with_current_item(self):
        rv = self.client.get('/api/status')
        self.assertEqual(200, rv.status_code)
        data = rv.get_json()
        self.assertFalse(data['empty'])
        self.assertTrue(data['play'])
        self.assertEqual('one-shot', data['mode'])
        self.assertEqual(0.42, data['volume'])
        self.assertEqual(33.5, data['playhead'])
        self.assertEqual(1, data['queue_length'])
        self.assertEqual('A Song', data['current']['title'])
        self.assertEqual('abc123', data['current']['id'])
        self.assertEqual(180, data['current']['duration'])
        self.assertFalse(data['current']['has_thumbnail'])
        self.assertIn('server_time', data)

    def test_status_empty_playlist(self):
        var.playlist = FakePlaylist([])
        rv = self.client.get('/api/status')
        data = rv.get_json()
        self.assertTrue(data['empty'])
        self.assertIsNone(data['current'])
        self.assertEqual(0, data['playhead'])
        self.assertEqual(-1, data['current_index'])

    def test_status_paused(self):
        var.bot.is_pause = True
        data = self.client.get('/api/status').get_json()
        self.assertFalse(data['play'])

    def test_thumbnail_roundtrip(self):
        raw = b'\xff\xd8fakejpegdata'
        self.item.thumbnail = base64.b64encode(raw).decode()
        rv = self.client.get('/api/thumbnail/abc123')
        self.assertEqual(200, rv.status_code)
        self.assertEqual('image/jpeg', rv.mimetype)
        self.assertEqual(raw, rv.data)
        self.assertIn('max-age', rv.headers.get('Cache-Control', ''))

    def test_thumbnail_missing(self):
        self.assertEqual(404, self.client.get('/api/thumbnail/abc123').status_code)
        self.assertEqual(404, self.client.get('/api/thumbnail/nope').status_code)

    def test_status_reports_thumbnail_presence(self):
        self.item.thumbnail = base64.b64encode(b'x').decode()
        data = self.client.get('/api/status').get_json()
        self.assertTrue(data['current']['has_thumbnail'])


if __name__ == '__main__':
    unittest.main()
