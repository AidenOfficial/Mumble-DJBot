"""Mumble 账号 <-> Web 身份绑定,以及聊天里的 !mylist / !fav。"""
import configparser
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import constants  # noqa: E402
import variables as var  # noqa: E402
import web_users  # noqa: E402
from commands import personal  # noqa: E402


class FakeMumbleUser:
    def __init__(self, name, user_id=None, cert_hash=None):
        self.name, self.user_id, self.cert_hash = name, user_id, cert_hash


class FakeBot:
    def __init__(self, users):
        self.mumble = mock.Mock()
        self.mumble.users = users
        self.sent = []
        self.downloads = 0

    def send_msg(self, msg, text):
        self.sent.append(msg)

    def async_download_next(self):
        self.downloads += 1


class Text:
    def __init__(self, actor):
        self.actor = actor


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = (var.user_db, var.playlist, var.config)
        var.user_db = web_users.UserDatabase(os.path.join(self.tmp.name, 's.db'))
        var.config = configparser.ConfigParser()
        var.config.read_dict({'commands': {'command_symbol': '!:！', 'bind': 'bind'}})
        constants.load_lang('zh_CN')
        personal._failures.clear()
        self.users = {1: FakeMumbleUser('Aiden', user_id=7), 2: FakeMumbleUser('guest', cert_hash='abc'),
                      3: FakeMumbleUser('Mallory', user_id=9)}
        self.bot = FakeBot(self.users)
        self.db = var.user_db
        self.db.touch('aiden@x.com', 'aiden@x.com')

    def tearDown(self):
        var.user_db, var.playlist, var.config = self._saved
        self.tmp.cleanup()

    def bind(self, actor, code):
        personal.cmd_bind(self.bot, self.users[actor].name, Text(actor), 'bind', code)
        return self.bot.sent[-1]


class BindTest(Base):
    def test_bind_sets_alias_and_is_one_time(self):
        code, _ = self.db.create_bind_code('aiden@x.com')
        self.assertIn('aiden@x.com', self.bind(1, code))
        self.assertEqual(self.db.identity_for_mumble('uid:7'), 'aiden@x.com')
        self.assertEqual(self.db.get_alias('aiden@x.com'), 'Aiden')    # 没别名时用 Mumble 名
        self.assertEqual(self.db.mumble_link_for('aiden@x.com')['mumble_name'], 'Aiden')
        self.assertIn('不对', self.bind(3, code))                        # 码只能用一次

    def test_existing_alias_kept(self):
        self.db.set_alias('aiden@x.com', 'DJ A')
        code, _ = self.db.create_bind_code('aiden@x.com')
        self.bind(1, code)
        self.assertEqual(self.db.get_alias('aiden@x.com'), 'DJ A')

    def test_expired_and_malformed(self):
        code, _ = self.db.create_bind_code('aiden@x.com', ttl=-1)
        self.assertIn('不对', self.bind(1, code))
        self.assertIn('用法', self.bind(1, '12'))

    def test_lockout_after_repeated_failures(self):
        for _ in range(personal.MAX_FAILURES):
            self.bind(3, '000000')
        code, _ = self.db.create_bind_code('aiden@x.com')
        self.assertIn('10 分钟', self.bind(3, code))                      # 正确的码也被拒
        self.assertIsNone(self.db.identity_for_mumble('uid:9'))

    def test_rebind_moves_link_and_unbind(self):
        code, _ = self.db.create_bind_code('aiden@x.com')
        self.bind(1, code)
        code, _ = self.db.create_bind_code('aiden@x.com')
        self.bind(2, code)                                                # 换一个 Mumble 账号
        self.assertIsNone(self.db.identity_for_mumble('uid:7'))
        self.assertEqual(self.db.identity_for_mumble('cert:abc'), 'aiden@x.com')
        personal.cmd_unbind(self.bot, 'guest', Text(2), 'unbind', '')
        self.assertIsNone(self.db.identity_for_mumble('cert:abc'))

    def test_requester_names_merge(self):
        code, _ = self.db.create_bind_code('aiden@x.com')
        self.bind(1, code)
        self.db.set_alias('aiden@x.com', 'DJ A')
        self.assertEqual(self.db.requester_display_names(), {'Aiden': 'DJ A'})


class PlaylistCommandTest(Base):
    def setUp(self):
        super().setUp()
        code, _ = self.db.create_bind_code('aiden@x.com')
        self.bind(1, code)
        self.pid = self.db.create_playlist('aiden@x.com', '深夜作业')
        self.db.add_items('aiden@x.com', self.pid, [web_users.entry_from_url(f'https://youtu.be/{i}' * 1, f'S{i}')
                                                     for i in range(3)])
        var.playlist = mock.MagicMock()

    def test_unbound_user_gets_instructions(self):
        personal.cmd_my_playlist(self.bot, 'Mallory', Text(3), 'mylist', '')
        self.assertIn('!bind', self.bot.sent[-1])

    def test_list_and_queue(self):
        personal.cmd_my_playlist(self.bot, 'Aiden', Text(1), 'mylist', '')
        self.assertIn('深夜作业', self.bot.sent[-1])
        built = []
        with mock.patch('web_users.wrapper_from_entry', side_effect=lambda e, u: built.append((e['title'], u)) or e):
            personal.cmd_my_playlist(self.bot, 'Aiden', Text(1), 'mylist', '1')
            personal.cmd_my_playlist(self.bot, 'Aiden', Text(1), 'mylist', '深夜 shuffle')
        self.assertEqual(built[:3], [('S0', 'Aiden'), ('S1', 'Aiden'), ('S2', 'Aiden')])
        self.assertEqual(var.playlist.extend.call_count, 2)
        self.assertEqual(self.bot.downloads, 2)
        personal.cmd_my_playlist(self.bot, 'Aiden', Text(1), 'mylist', '不存在')
        self.assertIn('不存在', self.bot.sent[-1])

    def test_fav_creates_default_playlist(self):
        item = mock.Mock(type='url', url='https://youtu.be/zz', duration=100, id='zz')
        item.format_title.return_value = 'Now'
        current = mock.Mock()
        current.item.return_value = item
        current.format_title.return_value = 'Now'
        var.playlist.__len__.return_value = 1
        var.playlist.current_item.return_value = current
        personal.cmd_favorite(self.bot, 'Aiden', Text(1), 'fav', '')
        personal.cmd_favorite(self.bot, 'Aiden', Text(1), 'fav', '')
        names = [p['name'] for p in self.db.list_playlists('aiden@x.com')]
        self.assertIn('Favorites', names)
        self.assertIn('已经在', self.bot.sent[-1])
        personal.cmd_favorite(self.bot, 'Aiden', Text(1), 'fav', '深夜作业')
        self.assertEqual(len(self.db.get_playlist('aiden@x.com', self.pid)['items']), 4)


class ApiTest(unittest.TestCase):
    def test_bind_endpoints(self):
        from tests.test_web_users import Base as WebBase
        base = WebBase('run')
        base.setUp()
        try:
            var.config.read_dict({'commands': {'command_symbol': '!:！', 'bind': 'bind'}})
            c, h = base.client, base.as_user()
            self.assertEqual(c.post('/api/me/bind').status_code, 403)
            rv = c.post('/api/me/bind', headers=h).get_json()
            self.assertRegex(rv['code'], r'^\d{6}$')
            self.assertEqual(rv['command'], f"!bind {rv['code']}")
            self.assertGreater(rv['expires_at'], time.time() + 500)
            self.assertIsNone(c.get('/api/me', headers=h).get_json()['mumble'])
            var.user_db.consume_bind_code(rv['code'], 'uid:1', 'Aiden')
            self.assertEqual(c.get('/api/me', headers=h).get_json()['mumble']['mumble_name'], 'Aiden')
            self.assertIsNone(c.delete('/api/me/bind', headers=h).get_json()['mumble'])
        finally:
            base.tearDown()


if __name__ == '__main__':
    unittest.main()
