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

    def is_ready(self):
        return True

    def validate(self):
        return True


class FakePlaylist(list):
    def __init__(self, wrappers=(), mode='one-shot'):
        super().__init__(wrappers)
        import threading
        self.version = 7
        self.mode = mode
        self.current_index = 0 if wrappers else -1
        self.playlist_lock = threading.RLock()

    def current_item(self):
        if not len(self):
            return False
        return self[self.current_index]

    def next(self):
        if self.current_index < len(self) - 1:
            self.current_index += 1
            return self[self.current_index]
        return False

    def skip_current(self):
        pass

    def remove(self, index):
        self.version += 1
        removed = self[index]
        super().__delitem__(index)
        if self.current_index > index:
            self.current_index -= 1
        return removed


class FakeVolume:
    plain_volume_set = 0.42

    def set_volume(self, value):
        self.plain_volume_set = value


class FakeDB:
    def __init__(self):
        self.saved = {}

    def set(self, section, option, value):
        self.saved[(section, option)] = value


class FakeBot:
    def __init__(self):
        self.is_pause = False
        self.playhead = 33.5
        self.volume_helper = FakeVolume()
        self.is_ducking = False
        self.on_ducking = False
        self.wait_for_ready = False
        self.calls = []

    def pause(self):
        self.calls.append('pause')
        self.is_pause = True

    def resume(self):
        self.calls.append('resume')
        self.is_pause = False

    def interrupt(self):
        self.calls.append('interrupt')

    def clear(self):
        self.calls.append('clear')

    def stop(self):
        self.calls.append('stop')

    def play(self, index=-1, start_at=0):
        self.calls.append(('play', index))

    def async_download_next(self):
        self.calls.append('async_download_next')


class FakeCache(dict):
    def get_item_by_id(self, _id):
        return self.get(_id)


def no_auth(f):
    return f


class WebApiTestCase(unittest.TestCase):
    def setUp(self):
        import configparser
        from flask import Flask
        import web_api
        self._saved = {n: getattr(var, n, None)
                       for n in ('bot', 'playlist', 'cache', 'db', 'config')}
        var.bot = FakeBot()
        self.item = FakeItem()
        var.playlist = FakePlaylist([FakeWrapper(self.item)])
        var.cache = FakeCache({self.item.id: self.item})
        var.db = FakeDB()
        config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
        config.add_section('bot')
        config.set('bot', 'clear_when_stop_in_oneshot', 'False')
        var.config = config

        app = Flask(__name__)
        app.register_blueprint(web_api.create_blueprint(no_auth))
        self.client = app.test_client()

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(var, name, value)

    def fill_queue(self, count):
        items = [FakeItem(_id='id%02d' % i, title='Song %d' % i)
                 for i in range(count)]
        var.playlist = FakePlaylist([FakeWrapper(i) for i in items])
        return var.playlist

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

    # ---- controls ---------------------------------------------------------

    def post_controls(self, **body):
        return self.client.post('/api/controls', json=body)

    def test_controls_pause_resume(self):
        data = self.post_controls(action='pause').get_json()
        self.assertIn('pause', var.bot.calls)
        self.assertFalse(data['play'])
        data = self.post_controls(action='resume').get_json()
        self.assertIn('resume', var.bot.calls)
        self.assertTrue(data['play'])

    def test_controls_skip_while_playing_interrupts(self):
        self.post_controls(action='skip')
        self.assertEqual(['interrupt'], var.bot.calls)

    def test_controls_skip_while_paused_advances(self):
        self.fill_queue(3)
        var.bot.is_pause = True
        self.post_controls(action='skip')
        self.assertNotIn('interrupt', var.bot.calls)
        self.assertEqual(1, var.playlist.current_index)
        self.assertTrue(var.bot.wait_for_ready)

    def test_controls_volume(self):
        data = self.post_controls(action='volume', volume=0.7).get_json()
        self.assertEqual(0.7, var.bot.volume_helper.plain_volume_set)
        self.assertEqual(0.7, data['volume'])
        self.assertIn(('bot', 'volume'), var.db.saved)

    def test_controls_volume_clamped(self):
        self.post_controls(action='volume', volume=3)
        self.assertEqual(1.0, var.bot.volume_helper.plain_volume_set)
        self.post_controls(action='volume', volume=-1)
        self.assertEqual(0.0, var.bot.volume_helper.plain_volume_set)

    def test_controls_bad_requests(self):
        self.assertEqual(400, self.post_controls(action='explode').status_code)
        self.assertEqual(400, self.post_controls().status_code)
        self.assertEqual(400, self.post_controls(action='volume', volume='x').status_code)
        self.assertEqual(400, self.post_controls(action='mode', mode='bogus').status_code)

    def test_controls_stop(self):
        self.post_controls(action='stop')
        self.assertIn('stop', var.bot.calls)

    def test_controls_mode_switch_repeat(self):
        playlist = self.fill_queue(3)
        rv = self.post_controls(action='mode', mode='repeat')
        self.assertEqual(200, rv.status_code)
        self.assertEqual('repeat', var.playlist.mode)
        self.assertEqual(list(playlist), list(var.playlist))
        self.assertEqual(('playlist', 'playback_mode'), list(var.db.saved)[-1])

    # ---- queue ------------------------------------------------------------

    def test_queue_listing(self):
        self.fill_queue(3)
        data = self.client.get('/api/queue').get_json()
        self.assertEqual(3, len(data['items']))
        self.assertEqual(['Song 0', 'Song 1', 'Song 2'],
                         [i['title'] for i in data['items']])
        self.assertTrue(data['items'][0]['is_current'])
        self.assertFalse(data['items'][1]['is_current'])

    def post_queue(self, **body):
        return self.client.post('/api/queue', json=body)

    def test_queue_move_updates_current_index(self):
        playlist = self.fill_queue(4)
        playlist.current_index = 1
        ids = [w.id for w in playlist]
        self.post_queue(action='move', index=3, to=0)
        self.assertEqual([ids[3], ids[0], ids[1], ids[2]],
                         [w.id for w in var.playlist])
        self.assertEqual(2, var.playlist.current_index)  # shifted right

    def test_queue_move_current_follows(self):
        playlist = self.fill_queue(4)
        playlist.current_index = 1
        ids = [w.id for w in playlist]
        self.post_queue(action='move', index=1, to=3)
        self.assertEqual(3, var.playlist.current_index)
        self.assertEqual(ids[1], var.playlist[3].id)

    def test_queue_move_bounds_checked(self):
        self.fill_queue(2)
        self.assertEqual(400, self.post_queue(action='move', index=0, to=9).status_code)

    def test_queue_top_inserts_after_current(self):
        playlist = self.fill_queue(4)
        playlist.current_index = 0
        ids = [w.id for w in playlist]
        self.post_queue(action='top', index=3)
        self.assertEqual([ids[0], ids[3], ids[1], ids[2]],
                         [w.id for w in var.playlist])
        self.assertEqual(0, var.playlist.current_index)

    def test_queue_remove_noncurrent(self):
        playlist = self.fill_queue(3)
        playlist.current_index = 0
        self.post_queue(action='remove', index=2)
        self.assertEqual(2, len(var.playlist))
        self.assertNotIn('interrupt', var.bot.calls)

    def test_queue_remove_current_interrupts(self):
        playlist = self.fill_queue(3)
        playlist.current_index = 1
        self.post_queue(action='remove', index=1)
        self.assertEqual(2, len(var.playlist))
        self.assertIn('interrupt', var.bot.calls)

    def test_queue_play_jumps(self):
        self.fill_queue(3)
        self.post_queue(action='play', index=2)
        self.assertIn(('play', 2), var.bot.calls)

    def test_queue_clear(self):
        self.post_queue(action='clear')
        self.assertIn('clear', var.bot.calls)

    # ---- search -----------------------------------------------------------

    def test_search_endpoint(self):
        from unittest import mock
        import web_search
        fake = ([{'source': 'youtube', 'id': 'x', 'url': 'https://y/x',
                  'title': 'T', 'uploader': 'U', 'duration': 10,
                  'thumbnail': ''}], ['bilibili'])
        with mock.patch.object(web_search, 'unified_search', return_value=fake):
            data = self.client.get('/api/search?q=hello').get_json()
        self.assertEqual('hello', data['query'])
        self.assertEqual(1, len(data['results']))
        self.assertEqual(['bilibili'], data['failed'])

    def test_search_requires_query(self):
        self.assertEqual(400, self.client.get('/api/search').status_code)
        self.assertEqual(400, self.client.get('/api/search?q=a').status_code)

    def test_search_add_youtube(self):
        from unittest import mock
        import web_api
        wrapper = FakeWrapper(FakeItem(_id='new1', title='New'))
        with mock.patch.object(web_api, 'get_cached_wrapper_from_scrap',
                               return_value=wrapper) as scrap:
            rv = self.client.post('/api/search/add', json={
                'source': 'youtube', 'url': 'https://www.youtube.com/watch?v=x'})
        self.assertEqual(200, rv.status_code)
        scrap.assert_called_once_with(
            type='url', url='https://www.youtube.com/watch?v=x', user='Web Search')
        self.assertEqual('new1', var.playlist[-1].id)

    def test_search_add_bilibili_normalizes(self):
        from unittest import mock
        import web_api
        wrapper = FakeWrapper(FakeItem(_id='new2'))
        with mock.patch.object(web_api, 'get_cached_wrapper_from_scrap',
                               return_value=wrapper) as scrap:
            rv = self.client.post('/api/search/add', json={
                'source': 'bilibili', 'id': 'BV1xx411c7mD'})
        self.assertEqual(200, rv.status_code)
        url = scrap.call_args.kwargs['url']
        self.assertTrue(url.startswith('https://www.bilibili.com/video/av'),
                        url)

    def test_search_add_rejects_garbage(self):
        rv = self.client.post('/api/search/add', json={
            'source': 'youtube', 'url': 'javascript:alert(1)'})
        self.assertEqual(400, rv.status_code)
        rv = self.client.post('/api/search/add', json={'source': 'bilibili',
                                                       'id': 'not-a-bvid'})
        self.assertEqual(400, rv.status_code)


if __name__ == '__main__':
    unittest.main()
