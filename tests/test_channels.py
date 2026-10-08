"""频道树 / 默认频道 / 自动跟随。用假的 pymumble 对象(频道是 dict,用户有 name/channel_id/session)。"""
import configparser
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
from bot import channels as chmod  # noqa: E402
from bot.channels import ChannelMixin  # noqa: E402
from database import SettingsDatabase  # noqa: E402


class FakeChannel(dict):
    def __init__(self, mumble, cid, name, parent=None, position=0):
        super().__init__(channel_id=cid, name=name, position=position)
        if parent is not None:
            self['parent'] = parent
        self.mumble = mumble

    def move_in(self):
        self.mumble.moves.append(self['channel_id'])
        self.mumble.users.myself.channel_id = self['channel_id']


class FakeUser:
    def __init__(self, session, name, channel_id):
        self.session, self.name, self.channel_id = session, name, channel_id
        self.self_mute = self.self_deaf = self.mute = self.deaf = False


class FakeUsers:
    def __init__(self):
        self.all = {}
        self.myself = None

    def by_session(self):
        return dict(self.all)

    def add(self, session, name, channel_id):
        self.all[session] = FakeUser(session, name, channel_id)
        return self.all[session]


class FakeMumble:
    def __init__(self):
        self.moves = []
        self.users = FakeUsers()
        self.channels = {}
        # Root ─┬ Lobby(1) ─ AFK(4)
        #       ├ Games(2) ─┬ Squad(5)
        #       │           └ Lobby(6)   同名不同路径
        #       └ Music(3)
        for cid, name, parent, pos in [(0, 'Root', None, 0), (1, 'Lobby', 0, 0), (2, 'Games', 0, 1),
                                       (3, 'Music', 0, 2), (4, 'AFK', 1, 0), (5, 'Squad', 2, 0),
                                       (6, 'Lobby', 2, 1)]:
            self.channels[cid] = FakeChannel(self, cid, name, parent, pos)
        self.users.myself = self.users.add(1, 'DJBot', 3)


class Bot(ChannelMixin):
    def __init__(self):
        self.mumble = FakeMumble()
        self.channel = ''
        self.bots = {'OtherBot'}
        self._init_follow()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = {n: getattr(var, n, None) for n in ('db', 'config', 'bot')}
        var.db = SettingsDatabase(os.path.join(self.tmp.name, 's.db'))
        from database import DatabaseMigration, MusicDatabase
        DatabaseMigration(var.db, MusicDatabase(os.path.join(self.tmp.name, 'm.db'))).migrate()
        self.bot = Bot()
        # 测试里同步执行跟随检查,不起定时器
        self.bot.schedule_follow_check = lambda delay=0: None

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(var, n, v)
        self.tmp.cleanup()


class TreeTest(Base):
    def test_paths_and_lookup(self):
        b = self.bot
        self.assertEqual(b.channel_path(6), ['Games', 'Lobby'])
        self.assertEqual(b.channel_path(0), [])
        self.assertEqual(b.find_channel_by_path(['Games', 'Lobby']), 6)
        self.assertEqual(b.find_channel_by_path(['Lobby']), 1)
        self.assertEqual(b.find_channel_by_path([]), 0)
        self.assertIsNone(b.find_channel_by_path(['Nope']))

    def test_tree_snapshot(self):
        self.bot.mumble.users.add(2, 'alice', 5)
        self.bot.mumble.users.add(3, 'OtherBot', 5)
        tree = self.bot.channel_tree()
        self.assertEqual([c['name'] for c in tree['children']], ['Lobby', 'Games', 'Music'])
        squad = tree['children'][1]['children'][0]
        self.assertEqual(squad['path'], ['Games', 'Squad'])
        self.assertEqual([(u['name'], u['is_bot']) for u in squad['users']], [('alice', False), ('OtherBot', True)])
        music = tree['children'][2]
        self.assertTrue(music['users'][0]['is_me'])

    def test_default_channel_falls_back_to_ini_then_db(self):
        self.bot.channel = 'Games/Squad'
        self.assertEqual(self.bot.channel_settings()['default'], ['Games', 'Squad'])
        self.bot.save_channel_settings(default=['Lobby'])
        self.assertEqual(self.bot.channel_settings()['default'], ['Lobby'])
        self.bot.join_channel()
        self.assertEqual(self.bot.mumble.moves, [1])


class FollowTest(Base):
    def test_off_never_moves(self):
        self.bot.mumble.users.add(2, 'alice', 5)
        self.assertIsNone(self.bot.follow_target())

    def test_auto_stays_while_someone_is_here(self):
        self.bot.save_channel_settings(follow='auto')
        self.bot.mumble.users.add(2, 'alice', 3)
        self.bot.mumble.users.add(3, 'bob', 5)
        self.assertIsNone(self.bot.follow_target())

    def test_auto_follows_last_leaver(self):
        b = self.bot
        b.save_channel_settings(follow='auto')
        alice = b.mumble.users.add(2, 'alice', 3)
        b.mumble.users.add(3, 'bob', 1)
        b.mumble.users.add(4, 'carol', 1)
        b._note_user_event(alice, 'created')
        alice.channel_id = 5           # alice 从 bot 所在频道走到 Squad
        b._note_user_event(alice, 'updated')
        # Lobby 人更多,但应该跟着刚走的 alice 去 Squad
        b.follow_check()
        self.assertEqual(b.mumble.moves, [5])

    def test_auto_goes_to_busiest_when_leaver_disconnected(self):
        b = self.bot
        b.save_channel_settings(follow='auto')
        alice = b.mumble.users.add(2, 'alice', 3)
        b._note_user_event(alice, 'created')
        del b.mumble.users.all[2]
        b._note_user_event(alice, 'removed')
        b.mumble.users.add(3, 'bob', 1)
        b.mumble.users.add(4, 'carol', 2)
        b.mumble.users.add(5, 'dave', 2)
        b.mumble.users.add(6, 'OtherBot', 1)  # 其他 bot 不算人
        b.follow_check()
        self.assertEqual(b.mumble.moves, [2])

    def test_stale_leaver_ignored(self):
        b = self.bot
        b.save_channel_settings(follow='auto')
        b._last_leaver = (time.time() - chmod.LAST_LEAVER_TTL - 1, 5)
        b.mumble.users.add(2, 'alice', 5)
        b.mumble.users.add(3, 'bob', 2)
        b.mumble.users.add(4, 'carol', 2)
        self.assertEqual(b.follow_target(), 2)

    def test_return_home_when_server_empty(self):
        b = self.bot
        b.save_channel_settings(follow='auto', default=['Lobby'])
        b.follow_check()
        self.assertEqual(b.mumble.moves, [1])
        b.mumble.moves.clear()
        b.follow_check()  # 已经在家,不再动
        self.assertEqual(b.mumble.moves, [])
        b.mumble.users.myself.channel_id = 3
        b.save_channel_settings(return_home=False)
        b.follow_check()
        self.assertEqual(b.mumble.moves, [])

    def test_follow_specific_user(self):
        b = self.bot
        b.save_channel_settings(follow='user', follow_user='aiden')
        b.mumble.users.add(2, 'someone', 3)  # 本频道有人也照样跟
        aiden = b.mumble.users.add(3, 'aiden', 4)
        b.follow_check()
        self.assertEqual(b.mumble.moves, [4])
        aiden.channel_id = 6
        b.follow_check()
        self.assertEqual(b.mumble.moves, [4, 6])

    def test_followed_user_offline_falls_back_to_auto(self):
        b = self.bot
        b.save_channel_settings(follow='user', follow_user='aiden', return_home=False)
        b.mumble.users.add(2, 'bob', 2)
        self.assertEqual(b.follow_target(), 2)

    def test_follow_would_move_guards_pause(self):
        b = self.bot
        self.assertFalse(b.follow_would_move())
        b.save_channel_settings(follow='auto')
        b.mumble.users.add(2, 'bob', 2)
        self.assertTrue(b.follow_would_move())

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            self.bot.save_channel_settings(follow='teleport')


class DebounceTest(Base):
    def test_rapid_events_collapse_into_one_check(self):
        b = Bot()
        calls = []
        b.follow_check = lambda: calls.append(1)
        with mock.patch.object(chmod, 'DEBOUNCE_SECONDS', 0.05):
            for _ in range(5):
                b.schedule_follow_check(delay=0.05)
            time.sleep(0.3)
        self.assertEqual(len(calls), 1)


class ApiTest(Base):
    def setUp(self):
        super().setUp()
        from flask import Flask
        import web_channels
        var.bot = self.bot
        var.config = configparser.ConfigParser()
        app = Flask(__name__)
        app.register_blueprint(web_channels.create_blueprint(lambda f: f))
        self.client = app.test_client()

    def test_overview_settings_and_join(self):
        self.bot.mumble.users.add(2, 'alice', 5)
        data = self.client.get('/api/channels').get_json()
        self.assertEqual(data['current_channel_id'], 3)
        self.assertEqual(data['online_users'], ['alice'])
        self.assertEqual(data['tree']['name'], 'Root')

        rv = self.client.post('/api/channels/settings', json={
            'default_channel_id': 6, 'follow': 'user', 'follow_user': 'alice', 'return_home': False})
        s = rv.get_json()['settings']
        self.assertEqual((s['default_path'], s['default_channel_id'], s['follow'], s['follow_user'], s['return_home']),
                         (['Games', 'Lobby'], 6, 'user', 'alice', False))

        self.assertEqual(self.client.post('/api/channels/settings', json={'follow': 'x'}).status_code, 400)
        self.assertEqual(self.client.post('/api/channels/settings', json={'default_channel_id': 99}).status_code, 404)
        self.assertEqual(self.client.post('/api/channels/settings', json={}).status_code, 400)

        rv = self.client.post('/api/channels/join', json={'channel_id': 2})
        self.assertEqual(rv.get_json()['current_channel_id'], 2)
        self.assertEqual(self.client.post('/api/channels/join', json={'channel_id': 'x'}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
