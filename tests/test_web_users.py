"""Cloudflare Access 身份、别名与个人歌单 API。"""
import configparser
import functools
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
import web_users  # noqa: E402
from tests.test_web_api import FakeBot, FakeCache, FakePlaylist, FakeWrapper, FakeItem  # noqa: E402

EMAIL_HEADER = 'Cf-Access-Authenticated-User-Email'


def identity_auth(f):
    """测试用 requires_auth:只做身份解析(生产环境的 _with_identity 同样调用它)。"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        from flask import g
        g.web_identity = web_users.resolve_identity(None)
        return f(*args, **kwargs)
    return decorated


class Base(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        import web_api
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = {n: getattr(var, n, None)
                       for n in ('bot', 'playlist', 'cache', 'db', 'config', 'user_db')}
        config = configparser.ConfigParser(interpolation=None)
        config.read_dict({'webinterface': {'access_email_header': EMAIL_HEADER,
                                           'access_team_domain': '', 'access_aud': ''},
                          'bot': {}})
        var.config = config
        var.bot = FakeBot()
        var.playlist = FakePlaylist([FakeWrapper(FakeItem('aaa', 'Song A', url='https://e.com/a'))])
        var.playlist.extend = lambda ws: list.extend(var.playlist, ws)
        var.cache = FakeCache()
        var.user_db = web_users.UserDatabase(os.path.join(self.tmp.name, 'settings.db'))
        app = Flask(__name__)
        app.register_blueprint(web_api.create_blueprint(identity_auth))
        app.register_blueprint(web_users.create_blueprint(identity_auth))
        self.client = app.test_client()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(var, k, v)
        self.tmp.cleanup()

    def as_user(self, email='alice@example.com'):
        return {EMAIL_HEADER: email} if email else {}


class IdentityTest(Base):
    def test_email_from_access_header(self):
        rv = self.client.get('/api/me', headers=self.as_user('Alice@Example.com'))
        data = rv.get_json()
        self.assertEqual(data['email'], 'alice@example.com')
        self.assertEqual(data['display_name'], 'alice')
        self.assertTrue(data['can_have_playlists'])

    def test_anonymous_without_header(self):
        data = self.client.get('/api/me').get_json()
        self.assertIsNone(data['identity'])
        self.assertEqual(data['display_name'], 'Remote Control')
        self.assertEqual(self.client.post('/api/me', json={'alias': 'x'}).status_code, 403)

    def test_set_and_clear_alias(self):
        h = self.as_user()
        rv = self.client.post('/api/me', json={'alias': '  Aiden '}, headers=h)
        self.assertEqual(rv.get_json()['display_name'], 'Aiden')
        self.assertEqual(self.client.get('/api/me', headers=h).get_json()['alias'], 'Aiden')
        self.client.post('/api/me', json={'alias': ''}, headers=h)
        self.assertEqual(self.client.get('/api/me', headers=h).get_json()['display_name'], 'alice')

    def test_alias_unique_case_insensitive(self):
        self.client.post('/api/me', json={'alias': 'Aiden'}, headers=self.as_user('a@x.com'))
        rv = self.client.post('/api/me', json={'alias': 'aiden'}, headers=self.as_user('b@x.com'))
        self.assertEqual(rv.status_code, 409)
        # 自己改大小写不算冲突
        rv = self.client.post('/api/me', json={'alias': 'AIDEN'}, headers=self.as_user('a@x.com'))
        self.assertEqual(rv.status_code, 200)

    def test_alias_rejects_markup(self):
        for bad in ('<b>x</b>', 'a&b', 'x' * 40):
            rv = self.client.post('/api/me', json={'alias': bad}, headers=self.as_user())
            self.assertEqual(rv.status_code, 400, bad)

    def test_alias_used_as_requester(self):
        h = self.as_user()
        self.client.post('/api/me', json={'alias': 'Aiden'}, headers=h)
        with mock.patch('web_api.get_cached_wrapper_from_scrap') as scrap:
            scrap.return_value = FakeWrapper(FakeItem('bbb'))
            self.client.post('/api/search/add', json={'url': 'https://e.com/b'}, headers=h)
        self.assertEqual(scrap.call_args.kwargs['user'], 'Aiden')


class JwtTest(Base):
    def setUp(self):
        super().setUp()
        var.config.set('webinterface', 'access_team_domain', 'team.cloudflareaccess.com')
        var.config.set('webinterface', 'access_aud', 'aud-tag')
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        signing_key = mock.Mock(key=self.key.public_key())
        client = mock.Mock()
        client.get_signing_key_from_jwt.return_value = signing_key
        web_users._jwks_clients.clear()
        self.patch = mock.patch('jwt.PyJWKClient', return_value=client)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        web_users._jwks_clients.clear()
        super().tearDown()

    def token(self, aud='aud-tag', email='bob@example.com'):
        import jwt
        return jwt.encode({'aud': aud, 'email': email, 'iss': 'https://team.cloudflareaccess.com',
                           'exp': int(time.time()) + 60}, self.key, algorithm='RS256')

    def test_valid_jwt(self):
        rv = self.client.get('/api/me', headers={'Cf-Access-Jwt-Assertion': self.token()})
        self.assertEqual(rv.get_json()['email'], 'bob@example.com')
        self.assertTrue(rv.get_json()['jwt_verified'])

    def test_missing_jwt_is_forbidden_even_with_spoofed_header(self):
        rv = self.client.get('/api/me', headers=self.as_user('evil@example.com'))
        self.assertEqual(rv.status_code, 403)

    def test_wrong_audience_is_forbidden(self):
        rv = self.client.get('/api/me', headers={'Cf-Access-Jwt-Assertion': self.token(aud='other')})
        self.assertEqual(rv.status_code, 403)


class PlaylistTest(Base):
    def create(self, name='Chill', email='alice@example.com'):
        rv = self.client.post('/api/playlists', json={'name': name}, headers=self.as_user(email))
        self.assertEqual(rv.status_code, 200)
        return rv.get_json()['id']

    def test_anonymous_has_no_playlists(self):
        self.assertEqual(self.client.get('/api/playlists').status_code, 403)

    def test_crud_and_isolation(self):
        pid = self.create()
        h = self.as_user()
        rv = self.client.post(f'/api/playlists/{pid}/items', headers=h,
                              json={'source': 'url', 'url': 'https://e.com/x', 'title': 'X', 'duration': 7200})
        self.assertEqual(rv.get_json()['added'], 1)
        # 重复添加去重
        rv = self.client.post(f'/api/playlists/{pid}/items', headers=h,
                              json={'source': 'url', 'url': 'https://e.com/x'})
        self.assertEqual(rv.get_json()['added'], 0)
        self.client.post(f'/api/playlists/{pid}/items', headers=h, json={'source': 'current'})
        pl = self.client.get(f'/api/playlists/{pid}', headers=h).get_json()
        self.assertEqual([i['title'] for i in pl['items']], ['X', 'Song A'])

        listing = self.client.get('/api/playlists', headers=h).get_json()['playlists']
        self.assertEqual(listing[0]['count'], 2)
        self.assertEqual(listing[0]['total_duration'], 7380)

        # 别人看不到、改不了
        other = self.as_user('mallory@example.com')
        self.assertEqual(self.client.get(f'/api/playlists/{pid}', headers=other).status_code, 404)
        self.assertEqual(self.client.delete(f'/api/playlists/{pid}', headers=other).status_code, 404)
        self.assertEqual(self.client.get('/api/playlists', headers=other).get_json()['playlists'], [])

        # 排序 / 删除条目 / 改名 / 删除歌单
        pl = self.client.post(f'/api/playlists/{pid}/move', headers=h, json={'index': 1, 'to': 0}).get_json()
        self.assertEqual([i['title'] for i in pl['items']], ['Song A', 'X'])
        pl = self.client.delete(f"/api/playlists/{pid}/items/{pl['items'][0]['id']}", headers=h).get_json()
        self.assertEqual(len(pl['items']), 1)
        pl = self.client.post(f'/api/playlists/{pid}/rename', headers=h, json={'name': 'Night'}).get_json()
        self.assertEqual(pl['name'], 'Night')
        self.assertEqual(self.client.delete(f'/api/playlists/{pid}', headers=h).status_code, 200)
        self.assertEqual(self.client.get(f'/api/playlists/{pid}', headers=h).status_code, 404)

    def test_add_from_queue_and_bad_input(self):
        pid = self.create()
        h = self.as_user()
        rv = self.client.post(f'/api/playlists/{pid}/items', headers=h, json={'source': 'queue', 'index': 0})
        self.assertEqual(rv.status_code, 200)
        rv = self.client.post(f'/api/playlists/{pid}/items', headers=h, json={'source': 'queue', 'index': 9})
        self.assertEqual(rv.status_code, 400)
        rv = self.client.post(f'/api/playlists/{pid}/items', headers=h,
                              json={'source': 'url', 'url': 'javascript:alert(1)'})
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(self.client.post('/api/playlists', headers=h, json={'name': ''}).status_code, 400)

    def test_play_queues_with_alias(self):
        pid = self.create()
        h = self.as_user()
        self.client.post('/api/me', json={'alias': 'Aiden'}, headers=h)
        for n in range(3):
            self.client.post(f'/api/playlists/{pid}/items', headers=h,
                             json={'source': 'url', 'url': f'https://e.com/{n}'})
        built = []

        def fake_wrapper(entry, user):
            built.append((entry['ref'], user))
            return FakeWrapper(FakeItem(entry['item_id']))
        with mock.patch('web_users.wrapper_from_entry', side_effect=fake_wrapper):
            rv = self.client.post(f'/api/playlists/{pid}/play', headers=h, json={'mode': 'append'})
        self.assertEqual(rv.get_json()['queued'], 3)
        self.assertEqual(len(var.playlist), 4)
        self.assertEqual({u for _, u in built}, {'Aiden'})
        self.assertIn('async_download_next', var.bot.calls)

    def test_play_replace_clears_and_unpauses(self):
        pid = self.create()
        h = self.as_user()
        self.client.post(f'/api/playlists/{pid}/items', headers=h,
                         json={'source': 'url', 'url': 'https://e.com/1'})
        var.bot.is_pause = True
        with mock.patch('web_users.wrapper_from_entry',
                        side_effect=lambda e, u: FakeWrapper(FakeItem(e['item_id']))):
            self.client.post(f'/api/playlists/{pid}/play', headers=h, json={'mode': 'replace'})
        self.assertIn('clear', var.bot.calls)
        self.assertFalse(var.bot.is_pause)

    def test_play_replace_keeps_items_cached(self):
        # 真实的 bot.clear() 会 var.cache.free_all();重建好的条目必须在清空后仍在缓存里,
        # 否则队列全是悬空引用,播放循环 ItemNotCachedError 一首首跳过
        pid = self.create()
        h = self.as_user()
        for n in range(2):
            self.client.post(f'/api/playlists/{pid}/items', headers=h,
                             json={'source': 'url', 'url': f'https://e.com/{n}'})
        var.bot.clear = lambda: (var.bot.calls.append('clear'), var.playlist.clear(),
                                 var.cache.clear())

        def fake_wrapper(entry, user):
            item = FakeItem(entry['item_id'])
            var.cache[item.id] = item
            return FakeWrapper(item)
        with mock.patch('web_users.wrapper_from_entry', side_effect=fake_wrapper):
            rv = self.client.post(f'/api/playlists/{pid}/play', headers=h, json={'mode': 'replace'})
        self.assertEqual(rv.get_json()['queued'], 2)
        self.assertEqual(len(var.playlist), 2)
        for wrapper in var.playlist:
            self.assertIn(wrapper.id, var.cache)


class EntryRebuildTest(unittest.TestCase):
    def test_url_from_playlist_is_stored_as_plain_url(self):
        item = mock.Mock(type='url_from_playlist', url='https://e.com/v', duration=60, id='pl-id')
        item.format_title.return_value = 'V'
        entry = web_users.entry_from_item(item)
        self.assertEqual(entry['type'], 'url')
        self.assertEqual(entry['item_id'], web_users.entry_from_url('https://e.com/v')['item_id'])

    def test_rebuild_dispatch(self):
        with mock.patch('media.cache.get_cached_wrapper_from_scrap') as scrap, \
                mock.patch('media.cache.get_cached_wrapper_by_id', return_value=None):
            web_users.wrapper_from_entry({'type': 'url', 'ref': 'https://e.com/v', 'item_id': 'x'}, 'A')
            self.assertEqual(scrap.call_args.kwargs, {'type': 'url', 'url': 'https://e.com/v', 'user': 'A'})
            web_users.wrapper_from_entry({'type': 'file', 'ref': 'a/b.mp3', 'item_id': 'y'}, 'A')
            self.assertEqual(scrap.call_args.kwargs, {'type': 'file', 'path': 'a/b.mp3', 'user': 'A'})


if __name__ == '__main__':
    unittest.main()
