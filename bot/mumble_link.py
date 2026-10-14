"""Mumble 连接适配层:整个 bot 只有这个模块 import pymumble_rev。

bot 其余部分只用这里暴露的小接口:
  - 状态快照:users(session -> User,只读)、myself、my_channel_id、channels(channel_id -> dict)
  - 命令:send_private / send_channel / move / set_tokens / set_comment / set_avatar / set_self_mute
  - 音频:write_audio(阻塞到缓冲有空位)、audio_buffered、volume、receive_voice、cut_audio
  - 回调:on_text / on_user_joined / on_user_updated / on_user_left / on_voice

回调在本模块自己的分发线程里依次调用(不是网络线程,也不持任何锁),
单个回调抛异常只记日志,不影响连接和其他回调。快照对象都是不可变的,任何线程都能直接读。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from pymumble_rev import (
    ClientConfig,
    ConnectionClosedError,
    ThreadedClient,
    User,
    default_ssl_context,
    events,
)
from pymumble_rev.audio import BufferClosed
from pymumble_rev.opus import Decoder

log = logging.getLogger("bot")

#: 写给服务器的音频缓冲上限(秒)。播放线程写满后自然被播放速度节流。
AUDIO_BUFFER_SECONDS = 0.5
#: 建连 + TLS + 服务器同步完成的总超时(秒)。库默认 10 秒,频道多、链路慢的服务器可能不够。
CONNECT_TIMEOUT = 30.0


@dataclass(frozen=True)
class ChatMessage:
    """收到的文字消息。字段名沿用旧 pymumble 的 protobuf 习惯,命令处理代码不用改。"""

    actor: int  # 发送者 session;0 = 服务器自己发的(例如欢迎词)
    message: str
    session: tuple  # 非空 = 私聊
    channel_id: tuple


class MumbleLink:
    def __init__(self, host, port, username, password=None, tokens=(), certfile=None,
                 stereo=False, bitrate=96000, debug=False):
        tokens = [t.strip() for t in (tokens or []) if t and t.strip()]
        config = ClientConfig(
            username=username,
            password=password or None,
            tokens=tokens,
            client_type="bot",
        )
        if debug:
            logging.getLogger("pymumble_rev").setLevel(logging.DEBUG)
        self.stereo = stereo
        #: 本次会话当前的频道访问令牌(服务器每次收到的是完整列表,不是增量)
        self.tokens = list(tokens)
        self._client = ThreadedClient(
            host,
            config,
            port=port,
            ssl_context=default_ssl_context(certfile or None),
            channels=2 if stereo else 1,
            bitrate=bitrate,
            connect_timeout=CONNECT_TIMEOUT,
        )
        self._buffer = None
        self._volume = 1.0
        self._cutting = False
        #: 每次 cut_audio / clear_audio 加一。write_audio 用它发现"我阻塞期间缓冲被清过",
        #: 把 PcmBuffer.write 在 clear 之后补写进去的剩余部分再清掉(见 write_audio)
        self._cut_generation = 0
        self._closing = False
        self._decoders = {}
        self._closed = threading.Event()
        self._dispatcher = None

        self.on_text: Optional[Callable[[ChatMessage], None]] = None
        self.on_user_joined: Optional[Callable[[User], None]] = None
        self.on_user_updated: Optional[Callable[[User, frozenset], None]] = None
        self.on_user_left: Optional[Callable[[User], None]] = None
        #: (user, pcm) 解码后的 16 位 48 kHz 单声道 PCM;只有 receive_voice 打开时才会调用
        self.on_voice: Optional[Callable[[User, bytes], None]] = None

    # ---- 生命周期 ----------------------------------------------------------

    def connect(self):
        """连上并等服务器同步完。失败时抛 ConnectionRejected / TimeoutError / OSError。"""
        self._client.connect()
        self._buffer, playing = self._client.open_stream(capacity=AUDIO_BUFFER_SECONDS)
        playing.add_done_callback(self._on_stream_end)
        self._dispatcher = threading.Thread(target=self._dispatch, name="MumbleEvents", daemon=True)
        self._dispatcher.start()

    def close(self):
        self._closing = True
        self._client.close()
        self._closed.set()

    @property
    def alive(self):
        """连接还在。断线后 bot 主循环据此退出,由 Docker / systemd 拉起新进程。"""
        return not self._closed.is_set() and self._client.connected

    @property
    def close_reason(self):
        return self._client.close_reason

    def _on_stream_end(self, future):
        # 正常情况下这个流永远不会结束;结束了说明连接断了。自己 close() 时不用报
        if not future.cancelled() and future.exception() is not None and not self._closing:
            log.warning("mumble: audio stream ended: %s", future.exception())

    def _dispatch(self):
        try:
            for event in self._client.events():
                try:
                    self._handle(event)
                except Exception:
                    log.exception("mumble: event handler failed for %s", type(event).__name__)
        finally:
            log.warning("mumble: connection closed (%s)", self._client.close_reason)
            self._closed.set()
            if self._buffer is not None:
                # 唤醒可能卡在 write_audio 里的播放线程
                self._buffer.close()

    def _handle(self, event):
        if isinstance(event, events.TextMessage):
            if self.on_text:
                self.on_text(ChatMessage(
                    actor=event.actor if event.actor is not None else 0,
                    message=event.message,
                    session=event.sessions,
                    channel_id=event.channel_ids,
                ))
        elif isinstance(event, events.UserJoined):
            if self.on_user_joined:
                self.on_user_joined(event.user)
        elif isinstance(event, events.UserUpdated):
            if self.on_user_updated:
                self.on_user_updated(event.user, event.changed)
        elif isinstance(event, events.UserLeft):
            self._decoders.pop(event.user.session, None)
            if self.on_user_left:
                self.on_user_left(event.user)
        elif isinstance(event, events.AudioReceived):
            self._on_audio(event)

    def _on_audio(self, event):
        handler = self.on_voice
        if handler is None:
            return
        user = self._client.state.users.get(event.sender_session)
        if user is None:
            return
        decoder = self._decoders.get(event.sender_session)
        if decoder is None:
            decoder = self._decoders[event.sender_session] = Decoder(1)
        try:
            pcm = decoder.decode(event.opus_data)
        except Exception:
            return  # 坏包直接丢掉
        handler(user, pcm)

    # ---- 状态快照 ----------------------------------------------------------

    @property
    def users(self) -> Mapping[int, User]:
        """session -> User 的不可变快照。按 session 取不到会抛 KeyError(用户已离开)。"""
        return self._client.state.users

    @property
    def myself(self):
        return self._client.state.myself

    @property
    def my_channel_id(self):
        me = self.myself
        return me.channel_id if me is not None else None

    @property
    def channels(self):
        """channel_id -> dict(channel_id/name/parent/position/temporary),给频道树逻辑用。"""
        out = {}
        for cid, ch in self._client.state.channels.items():
            d = {'channel_id': cid, 'name': ch.name, 'position': ch.position,
                 'temporary': ch.temporary}
            if ch.parent is not None:
                d['parent'] = ch.parent
            out[cid] = d
        return out

    @property
    def max_message_length(self):
        """服务器允许的文字消息长度;0 = 不限。"""
        return self._client.state.server.message_length

    # ---- 命令 --------------------------------------------------------------

    def send_private(self, session, message):
        self._client.send_text(message, sessions=[session])

    def send_channel(self, channel_id, message):
        self._client.send_text(message, channel_ids=[channel_id])

    def move(self, channel_id):
        self._client.move(channel_id)

    def set_tokens(self, tokens):
        tokens = [t for t in tokens if t]
        self._client.set_tokens(tokens)
        self.tokens = tokens

    def set_comment(self, comment):
        self._client.set_self_state(comment=comment or "")

    def set_avatar(self, data):
        self._client.set_self_state(texture=data or b"")

    def set_self_mute(self, muted):
        self._client.set_self_state(self_mute=bool(muted))

    # ---- 音频 --------------------------------------------------------------

    def write_audio(self, pcm):
        """把 PCM(s16le 48 kHz)交给发送端。缓冲满 0.5 秒时阻塞,由播放速度节流。

        连接已断返回 False(不抛异常),调用方据此停止播放。

        播放线程绝大部分时间都阻塞在这里(ffmpeg 解码比实时快)。这时 cut_audio() 把缓冲清空,
        PcmBuffer.write 会被唤醒并把手里这块剩余的旧歌(≤ 10 ms)接着写进刚清空的缓冲——
        它会在下一首开头冒出来。所以写完后发现期间发生过清空,就把这点残留再清掉。
        """
        buf = self._buffer
        if buf is None:
            return False
        generation = self._cut_generation
        try:
            buf.write(pcm)
        except BufferClosed:
            return False
        if generation != self._cut_generation:
            buf.clear()
        return True

    @property
    def audio_buffered(self):
        """已交给发送端、还没播出去的秒数。"""
        buf = self._buffer
        return buf.buffered if buf is not None else 0.0

    def clear_audio(self):
        """丢掉还没播出去的音频。"""
        if self._buffer is not None:
            self._cut_generation += 1
            self._buffer.clear()

    def cut_audio(self, fade=0.03):
        """切歌 / 停止 / 暂停用:先让正在发的那一帧淡出到静音,再丢掉缓冲里还没播的部分。

        发送端按帧做音量渐变,所以"音量设 0 → 等一帧多一点 → 清空 → 恢复音量"
        就是一个 20 ms 的淡出,不会爆音;清空之后新歌从干净的缓冲开始。
        """
        if self._buffer is None:
            return
        self._cutting = True
        self._cut_generation += 1
        try:
            self._set_client_volume(0.0)
            time.sleep(fade)
            self._buffer.clear()
        finally:
            self._cutting = False
            self._set_client_volume(self._volume)

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value):
        self._volume = value
        if not self._cutting:  # 淡出期间别被播放线程的音量更新顶回去
            self._set_client_volume(value)

    def _set_client_volume(self, value):
        try:
            self._client.volume = value
        except (ConnectionClosedError, RuntimeError):
            # RuntimeError:事件循环刚好在关闭("Event loop is closed")。断线时播放线程不该因此崩
            pass

    @property
    def receive_voice(self):
        return self._client.receive_audio

    @receive_voice.setter
    def receive_voice(self, value):
        self._client.receive_audio = bool(value)
        if not value:
            self._decoders.clear()
