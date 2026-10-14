"""整张歌单入队后的分组、网页收藏(♥)、歌单占位标题、下载期间条目被移出缓存、
聊天命令不在 pymumble 线程里处理。"""
import configparser
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
import web_users  # noqa: E402
from media.cache import CachedItemWrapper, get_cached_wrapper, tag_group  # noqa: E402
from tests.test_web_api import FakeItem, FakeWrapper, FakePlaylist  # noqa: E402
from tests.test_web_users import Base  # noqa: E402


class SavingCache(dict):
    def __init__(self, *a):
        super().__init__(*a)
        self.saved = []

    def save_item(self, item):
        self.saved.append(item.id)


class Preparable:
    def __init__(self, _id, on_prepare=None):
        self.id = _id
        self.version = 0
        self.on_prepare = on_prepare

    def prepare(self):
        if self.on_prepare:
            self.on_prepare()
        self.version += 1
        return True

    validate = prepare


class WrapperLifecycleTest(unittest.TestCase):
    def test_item_freed_while_downloading_is_not_an_error(self):
        # 日志里的 "unexpected error while downloading":下载期间队列被清空,
        # 下载结束后 wrapper 回头查缓存抛 ItemNotCachedError
        cache = SavingCache()
        item = Preparable('x', on_prepare=lambda: cache.clear())
        cache['x'] = item
        wrapper = CachedItemWrapper(cache, 'x', 'url', 'u')
        self.assertTrue(wrapper.prepare())
        self.assertEqual(wrapper.version, 1)
        self.assertEqual(cache.saved, [])  # 已经移出缓存的不再写库

    def test_still_cached_item_is_saved(self):
        cache = SavingCache()
        cache['x'] = Preparable('x')
        wrapper = CachedItemWrapper(cache, 'x', 'url', 'u')
        self.assertTrue(wrapper.validate())
        self.assertEqual(cache.saved, ['x'])

    def test_cached_object_is_not_replaced_by_db_snapshot(self):
        saved = var.cache
        try:
            live, snapshot = FakeItem('same'), FakeItem('same')
            live.type = snapshot.type = 'url'
            var.cache = {'same': live}
            get_cached_wrapper(snapshot, 'u')
            self.assertIs(var.cache['same'], live)
        finally:
            var.cache = saved


class GroupPersistenceTest(unittest.TestCase):
    def setUp(self):
        from database import SettingsDatabase, MusicDatabase, DatabaseMigration
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = {n: getattr(var, n, None) for n in ('db', 'cache')}
        var.db = SettingsDatabase(os.path.join(self.tmp.name, 's.db'))
        DatabaseMigration(var.db, MusicDatabase(os.path.join(self.tmp.name, 'm.db'))).migrate()

        class Cache(dict):
            def get_item_by_id(self, _id):
                item = FakeItem(_id)
                item.type = 'url'
                return self.setdefault(_id, item)
        var.cache = Cache()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(var, k, v)
        self.tmp.cleanup()

    def test_groups_survive_save_and_load(self):
        from media.playlist import BasePlaylist
        pl = BasePlaylist()
        wrappers = [CachedItemWrapper(var.cache, i, 'url', 'u') for i in ('a', 'b', 'c')]
        tag_group(wrappers[1:], 'Night', 'playlist', 3)
        list.extend(pl, wrappers)
        pl.current_index = 0
        pl.save()

        loaded = BasePlaylist()
        with mock.patch.object(BasePlaylist, 'async_validate'):
            loaded.load()
        self.assertEqual([w.id for w in loaded], ['a', 'b', 'c'])
        self.assertIsNone(loaded[0].group)
        self.assertEqual(loaded[1].group['name'], 'Night')
        self.assertIs(loaded[1].group, loaded[2].group)


class QueueGroupApiTest(Base):
    def setUp(self):
        super().setUp()
        items = [FakeItem('id%d' % i, 'Song %d' % i) for i in range(5)]
        var.playlist = FakePlaylist([FakeWrapper(i) for i in items])
        tag_group(var.playlist[1:4], 'Night')
        var.bot.remove_group_from_queue = lambda key: \
            __import__('bot.player', fromlist=['PlayerMixin']).PlayerMixin.remove_group_from_queue(var.bot, key)

    def queue(self):
        return self.client.get('/api/queue').get_json()['items']

    def test_listing_carries_group(self):
        items = self.queue()
        self.assertIsNone(items[0]['group'])
        self.assertEqual({i['group']['key'] for i in items[1:4]}, {items[1]['group']['key']})
        self.assertEqual(items[1]['group']['name'], 'Night')

    def test_remove_group(self):
        key = self.queue()[1]['group']['key']
        rv = self.client.post('/api/queue', json={'action': 'remove_group', 'group': key})
        self.assertEqual(rv.status_code, 200)
        self.assertEqual([w.id for w in var.playlist], ['id0', 'id4'])
        self.assertEqual(self.client.post('/api/queue', json={'action': 'remove_group', 'group': key}).status_code,
                         400)

    def test_remove_group_containing_current(self):
        var.playlist.current_index = 2
        key = self.queue()[1]['group']['key']
        self.client.post('/api/queue', json={'action': 'remove_group', 'group': key})
        self.assertEqual([w.id for w in var.playlist], ['id0', 'id4'])
        self.assertIn('interrupt', var.bot.calls)  # 正在放的那首也被删了

    def test_item_dragged_out_leaves_group(self):
        self.client.post('/api/queue', json={'action': 'move', 'index': 2, 'to': 4})
        items = self.queue()
        self.assertEqual(items[4]['id'], 'id2')
        self.assertIsNone(items[4]['group'])
        self.assertIsNotNone(items[1]['group'])
        # 组内挪动不影响分组
        self.client.post('/api/queue', json={'action': 'move', 'index': 1, 'to': 2})
        self.assertIsNotNone(self.queue()[2]['group'])

    def test_whole_playlist_play_is_grouped_single_item_is_not(self):
        h = self.as_user()
        pid = self.client.post('/api/playlists', json={'name': 'Mix'}, headers=h).get_json()['id']
        for n in range(3):
            self.client.post(f'/api/playlists/{pid}/items', headers=h,
                             json={'source': 'url', 'url': f'https://e.com/{n}', 'title': f'T{n}'})
        var.playlist = FakePlaylist([])
        var.playlist.extend = lambda ws: list.extend(var.playlist, ws)
        with mock.patch('web_users.wrapper_from_entry', side_effect=lambda e, u: FakeWrapper(FakeItem(e['item_id']))):
            self.client.post(f'/api/playlists/{pid}/play', headers=h, json={'mode': 'append'})
            row = self.client.get(f'/api/playlists/{pid}', headers=h).get_json()['items'][0]['id']
            self.client.post(f'/api/playlists/{pid}/play', headers=h, json={'mode': 'append', 'item': row})
        groups = [getattr(w, 'group', None) for w in var.playlist]
        self.assertEqual(len({g['key'] for g in groups[:3]}), 1)
        self.assertEqual(groups[0]['name'], 'Mix')
        self.assertIsNone(groups[3])


class FavoritesTest(Base):
    def test_toggle_creates_shared_favorites_playlist(self):
        h = self.as_user()
        self.assertEqual(self.client.get('/api/favorites', headers=h).get_json(),
                         {'playlist_id': None, 'item_ids': []})
        rv = self.client.post('/api/favorites', headers=h, json={'source': 'current', 'favorite': True})
        data = rv.get_json()
        self.assertEqual(data['item_ids'], ['aaa'])
        listing = self.client.get('/api/playlists', headers=h).get_json()['playlists']
        self.assertEqual([(p['name'], p['favorites']) for p in listing], [('Favorites', True)])
        # 和聊天里的 !fav 同一张歌单
        self.assertEqual(var.user_db.favorites_id('alice@example.com'), data['playlist_id'])

        rv = self.client.post('/api/favorites', headers=h,
                              json={'source': 'url', 'url': 'https://e.com/b', 'title': 'B'})
        self.assertEqual(len(rv.get_json()['item_ids']), 2)
        rv = self.client.post('/api/favorites', headers=h, json={'source': 'current', 'favorite': False})
        self.assertEqual(len(rv.get_json()['item_ids']), 1)

    def test_anonymous(self):
        self.assertEqual(self.client.get('/api/favorites').get_json()['item_ids'], [])
        self.assertEqual(self.client.post('/api/favorites', json={'source': 'current'}).status_code, 403)

    def test_other_playlists_are_not_favorites(self):
        h = self.as_user()
        self.client.post('/api/playlists', json={'name': 'Chill'}, headers=h)
        listing = self.client.get('/api/playlists', headers=h).get_json()['playlists']
        self.assertFalse(listing[0]['favorites'])


class PlaceholderTitleTest(unittest.TestCase):
    def setUp(self):
        self._saved = {n: getattr(var, n, None) for n in ('tmp_folder', 'cache', 'user_db', 'music_db')}
        self.tmp = tempfile.TemporaryDirectory()
        var.tmp_folder = self.tmp.name + '/'

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(var, k, v)
        self.tmp.cleanup()

    def test_unvalidated_url_uses_playlist_title(self):
        from media.url import URLItem
        item = URLItem('https://e.com/watch?v=1')
        wrapper = mock.Mock()
        wrapper.item.return_value = item
        entry = {'type': 'url', 'ref': 'https://e.com/watch?v=1', 'title': '夜鷹', 'duration': 240,
                 'item_id': item.id}
        with mock.patch('media.cache.get_cached_wrapper_from_scrap', return_value=wrapper):
            web_users.wrapper_from_entry(entry, 'u')
        self.assertEqual(item.format_title(), '夜鷹')
        self.assertEqual(item.duration, 240)

    def test_link_title_is_not_used_as_placeholder(self):
        from media.url import URLItem
        item = URLItem('https://e.com/2')
        wrapper = mock.Mock()
        wrapper.item.return_value = item
        entry = {'type': 'url', 'ref': 'https://e.com/2', 'title': 'https://e.com/2', 'item_id': item.id}
        with mock.patch('media.cache.get_cached_wrapper_from_scrap', return_value=wrapper):
            web_users.wrapper_from_entry(entry, 'u')
        self.assertEqual(item.title, '')

    def test_heal_titles_from_known_items(self):
        var.user_db = web_users.UserDatabase(os.path.join(self.tmp.name, 'u.db'))
        pid = var.user_db.create_playlist('a', 'L')
        var.user_db.add_items('a', pid, [web_users.entry_from_url('https://e.com/3'),
                                         web_users.entry_from_url('https://e.com/4', 'Kept')])
        known = FakeItem(web_users.entry_from_url('https://e.com/3')['item_id'], duration=99)
        known.title = 'Real Title'
        var.cache = {known.id: known}
        var.music_db = mock.Mock()
        healed = web_users.heal_entry_titles(var.user_db.get_playlist('a', pid))
        self.assertEqual([e['title'] for e in healed['items']], ['Real Title', 'Kept'])
        stored = var.user_db.get_playlist('a', pid)['items']
        self.assertEqual((stored[0]['title'], stored[0]['duration']), ('Real Title', 99))


class MessageWorkerTest(unittest.TestCase):
    def test_callback_returns_before_command_finishes(self):
        from bot.core import MumbleBot
        bot = MumbleBot.__new__(MumbleBot)
        bot.log = mock.Mock()
        done = threading.Event()
        bot._handle_message = lambda text: (time.sleep(0.3), done.set())
        bot._start_message_worker()
        started = time.time()
        bot.message_received(object())
        self.assertLess(time.time() - started, 0.1)  # pymumble 线程立刻回去发音频
        self.assertTrue(done.wait(2))

    def test_worker_survives_a_failing_command(self):
        from bot.core import MumbleBot
        bot = MumbleBot.__new__(MumbleBot)
        bot.log = mock.Mock()
        handled = []
        done = threading.Event()

        def handle(text):
            if text == 'boom':
                raise RuntimeError('x')
            handled.append(text)
            done.set()
        bot._handle_message = handle
        bot._start_message_worker()
        bot.message_received('boom')
        bot.message_received('ok')
        self.assertTrue(done.wait(2))
        self.assertEqual(handled, ['ok'])
        bot.log.exception.assert_called_once()


if __name__ == '__main__':
    unittest.main()
