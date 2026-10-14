"""MumbleLink:事件分发、快照转换、语音解码、断线时解开播放线程。用假的 ThreadedClient。"""
import os
import queue
import sys
import threading
import time
import unittest
from types import MappingProxyType

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymumble_rev import Channel, ServerInfo, ServerState, User, events  # noqa: E402
from pymumble_rev.audio import PcmBuffer  # noqa: E402
from pymumble_rev.opus import Encoder  # noqa: E402

from bot.mumble_link import ChatMessage, MumbleLink  # noqa: E402


class FakeClient:
    def __init__(self):
        self.state = ServerState(
            session=1,
            users=MappingProxyType({
                1: User(session=1, name='DJBot', channel_id=2),
                5: User(session=5, name='alice', channel_id=2, user_id=7),
            }),
            channels=MappingProxyType({
                0: Channel(channel_id=0, name='Root'),
                2: Channel(channel_id=2, parent=0, name='Music', position=3, temporary=True),
            }),
            server=ServerInfo(message_length=5000),
        )
        self.connected = True
        self.close_reason = None
        self.receive_audio = False
        self.volume = 1.0
        self.sent = []
        self._events = queue.Queue()
        self.buffer = PcmBuffer(1, capacity=0.02)

    # ThreadedClient 接口
    def connect(self):
        pass

    def open_stream(self, capacity):
        from concurrent.futures import Future
        return self.buffer, Future()

    def events(self):
        while (ev := self._events.get()) is not None:
            yield ev

    def send_text(self, message, **kw):
        self.sent.append(('text', message, kw))

    def move(self, cid):
        self.sent.append(('move', cid))

    def set_tokens(self, tokens):
        self.sent.append(('tokens', list(tokens)))

    def set_self_state(self, **kw):
        self.sent.append(('self', kw))

    def close(self):
        self.connected = False
        self._events.put(None)

    # 测试辅助
    def push(self, event):
        self._events.put(event)

    def end(self, reason='connection closed by peer'):
        self.connected = False
        self.close_reason = reason
        self._events.put(None)


def make_link():
    link = MumbleLink('host', 64738, 'DJBot', tokens=['a', ' ', 'b'])
    link._client = FakeClient()
    link.connect()
    return link, link._client


def wait_until(pred, timeout=2):
    deadline = time.time() + timeout
    while not pred():
        if time.time() > deadline:
            raise AssertionError('condition not met')
        time.sleep(0.01)


class SnapshotTest(unittest.TestCase):
    def test_users_channels_and_limits(self):
        link, _ = make_link()
        self.assertEqual(link.users[5].name, 'alice')
        self.assertEqual(link.myself.name, 'DJBot')
        self.assertEqual(link.my_channel_id, 2)
        self.assertEqual(link.channels[0], {'channel_id': 0, 'name': 'Root', 'position': 0,
                                            'temporary': False})
        self.assertEqual(link.channels[2]['parent'], 0)
        self.assertTrue(link.channels[2]['temporary'])
        self.assertEqual(link.max_message_length, 5000)
        self.assertEqual(link.tokens, ['a', 'b'])  # 空白 token 被去掉
        with self.assertRaises(KeyError):
            link.users[99]

    def test_commands(self):
        link, client = make_link()
        link.send_private(5, 'hi')
        link.send_channel(2, 'all')
        link.move(0)
        link.set_tokens(['a', 'vip'])
        link.set_comment(None)
        link.set_avatar(None)
        link.set_self_mute(0)
        self.assertEqual(client.sent, [
            ('text', 'hi', {'sessions': [5]}),
            ('text', 'all', {'channel_ids': [2]}),
            ('move', 0),
            ('tokens', ['a', 'vip']),
            ('self', {'comment': ''}),
            ('self', {'texture': b''}),
            ('self', {'self_mute': False}),
        ])
        self.assertEqual(link.tokens, ['a', 'vip'])


class DispatchTest(unittest.TestCase):
    def test_text_and_user_events_reach_handlers(self):
        link, client = make_link()
        got = []
        link.on_text = got.append
        link.on_user_joined = lambda u: got.append(('joined', u.name))
        link.on_user_updated = lambda u, changed: got.append(('updated', u.name, changed))
        link.on_user_left = lambda u: got.append(('left', u.name))

        bob = User(session=9, name='bob')
        client.push(events.TextMessage(actor=5, message='!play x', sessions=(1,), channel_ids=(),
                                       tree_ids=()))
        client.push(events.TextMessage(actor=None, message='welcome', sessions=(), channel_ids=(0,),
                                       tree_ids=()))
        client.push(events.UserJoined(user=bob))
        client.push(events.UserUpdated(user=bob, previous=bob, changed=frozenset({'channel_id'}),
                                       actor=None))
        client.push(events.UserLeft(user=bob, actor=None, reason='', ban=False))
        wait_until(lambda: len(got) == 5)

        self.assertEqual(got[0], ChatMessage(actor=5, message='!play x', session=(1,), channel_id=()))
        self.assertEqual(got[1].actor, 0)  # 服务器消息:actor 记成 0,和以前一样
        self.assertEqual(got[2:], [('joined', 'bob'), ('updated', 'bob', frozenset({'channel_id'})),
                                   ('left', 'bob')])

    def test_a_failing_handler_does_not_stop_dispatch(self):
        link, client = make_link()
        got = []

        def boom(_):
            raise RuntimeError('handler bug')

        link.on_text = boom
        link.on_user_joined = lambda u: got.append(u.name)
        client.push(events.TextMessage(actor=5, message='x', sessions=(), channel_ids=(0,),
                                       tree_ids=()))
        client.push(events.UserJoined(user=User(session=9, name='bob')))
        wait_until(lambda: got == ['bob'])
        self.assertTrue(link.alive)

    def test_voice_is_decoded_for_the_handler(self):
        link, client = make_link()
        link.receive_voice = True
        self.assertTrue(client.receive_audio)
        heard = []
        link.on_voice = lambda user, pcm: heard.append((user.name, len(pcm)))
        packet = Encoder(1).encode(b'\0' * 1920)
        client.push(events.AudioReceived(sender_session=5, frame_number=0, opus_data=packet,
                                         context=0, is_terminator=False, volume_adjustment=0.0))
        client.push(events.AudioReceived(sender_session=404, frame_number=0, opus_data=packet,
                                         context=0, is_terminator=False, volume_adjustment=0.0))
        wait_until(lambda: heard)
        time.sleep(0.05)
        self.assertEqual(heard, [('alice', 1920)])  # 不认识的 session 忽略


class CutTest(unittest.TestCase):
    def test_cut_fades_then_clears_and_restores_volume(self):
        link, client = make_link()
        link.volume = 0.7
        self.assertEqual(client.volume, 0.7)
        link.write_audio(b'\1' * 1920)
        seen = []

        def player_updates_volume():
            # 播放线程在淡出期间照常更新音量:不能把发送端音量顶回去
            time.sleep(0.01)
            link.volume = 0.6
            seen.append(client.volume)

        t = threading.Thread(target=player_updates_volume)
        t.start()
        link.cut_audio(fade=0.05)
        t.join()
        self.assertEqual(seen, [0.0])
        self.assertEqual(client.buffer.buffered, 0.0)
        self.assertEqual(client.volume, 0.6)  # 恢复成最新的目标音量
        self.assertEqual(link.volume, 0.6)


class CutDuringBlockedWriteTest(unittest.TestCase):
    def test_cut_drops_the_remainder_of_a_blocked_write(self):
        # 播放线程阻塞在 write_audio 里时切歌:PcmBuffer.write 被 clear 唤醒后会把剩余的旧歌
        # 补写进去,write_audio 要把这点残留再清掉,否则它会在下一首开头冒出来
        link, client = make_link()
        self.assertTrue(link.write_audio(b'\1' * 1920))  # 缓冲(20 ms)已满
        results = []
        t = threading.Thread(target=lambda: results.append(link.write_audio(b'\2' * 960)))
        t.start()
        time.sleep(0.05)
        self.assertTrue(t.is_alive())  # 卡在写缓冲上
        link.cut_audio(fade=0.01)
        t.join(2)
        self.assertEqual(results, [True])
        self.assertEqual(client.buffer.buffered, 0.0)  # 残留没留下
        self.assertTrue(link.write_audio(b'\3' * 960))  # 之后照常能写
        self.assertEqual(client.buffer.buffered, 0.01)

    def test_clear_audio_also_drops_the_remainder(self):
        link, client = make_link()
        link.write_audio(b'\1' * 1920)
        t = threading.Thread(target=lambda: link.write_audio(b'\2' * 960))
        t.start()
        time.sleep(0.05)
        link.clear_audio()
        t.join(2)
        self.assertEqual(client.buffer.buffered, 0.0)


class VolumeAfterLoopClosedTest(unittest.TestCase):
    def test_volume_update_on_a_closed_loop_is_swallowed(self):
        # ThreadedClient 的 volume setter 在事件循环刚关掉时会抛 RuntimeError("Event loop is closed");
        # 播放线程每帧都可能推音量,不能因此崩
        link, client = make_link()

        class Closed:
            @property
            def volume(self):
                return 1.0

            @volume.setter
            def volume(self, value):
                raise RuntimeError('Event loop is closed')

        link._client = Closed()
        link.volume = 0.5  # 不抛
        self.assertEqual(link.volume, 0.5)

    def test_local_close_does_not_warn_about_the_stream(self):
        from concurrent.futures import Future
        link, client = make_link()
        link.close()
        done = Future()
        done.set_exception(RuntimeError('connection closed during playback'))
        with self.assertNoLogs('bot', level='WARNING'):
            link._on_stream_end(done)


class DisconnectTest(unittest.TestCase):
    def test_disconnect_unblocks_the_player_and_marks_dead(self):
        link, client = make_link()
        self.assertTrue(link.write_audio(b'\0' * 1920))  # 缓冲只有 20 ms,已经写满
        results = []
        t = threading.Thread(target=lambda: results.append(link.write_audio(b'\0' * 1920)))
        t.start()
        time.sleep(0.05)
        self.assertTrue(t.is_alive())  # 卡在写缓冲上
        client.end()
        t.join(2)
        self.assertEqual(results, [False])
        self.assertFalse(link.alive)
        self.assertEqual(link.close_reason, 'connection closed by peer')


if __name__ == '__main__':
    unittest.main()
