"""频道:探测服务器频道树、默认频道(Web 可配置)、自动跟随用户。

设置存在 settings 数据库的 channel 段,优先于 configuration.ini 的 [server] channel:
  default   默认频道路径(JSON 名字列表,根频道 = []);启动、!oust 时回到这里
  follow    跟随模式:off / auto / user
              auto  所在频道没人了(只剩 bot)就去找人:优先去最后离开的那位去的频道,
                    否则去人最多的频道
              user  始终跟着 follow_user 指定的用户
  follow_user   user 模式下跟随的用户名
  return_home   服务器上一个人都没有时回到默认频道(1/0)

pymumble 的回调在它自己的线程里、持着 users.lock 调用,所以这里只记录事件,
真正的决策放到防抖定时器线程里做(人在频道间来回切时不会被带着乱跑)。
本模块不 import pymumble,单测用假的 mumble 对象即可。
"""

import json
import logging
import threading
import time

import variables as var

log = logging.getLogger("bot")

FOLLOW_MODES = ('off', 'auto', 'user')
SECTION = 'channel'
DEBOUNCE_SECONDS = 2.5
LAST_LEAVER_TTL = 60  # "最后离开的人去了哪"多久内有效


class ChannelMixin:

    # ---- 设置 -----------------------------------------------------------

    def channel_settings(self):
        default = None
        raw = var.db.get(SECTION, 'default', fallback=None)
        if raw is not None:
            try:
                default = json.loads(raw)
            except ValueError:
                default = None
        if default is None:
            # 没在 Web 上配置过:沿用 ini / 命令行的 channel(A/B 形式)
            default = [p for p in (self.channel or '').split('/') if p]
        mode = var.db.get(SECTION, 'follow', fallback='off')
        return {
            'default': default,
            'follow': mode if mode in FOLLOW_MODES else 'off',
            'follow_user': var.db.get(SECTION, 'follow_user', fallback=''),
            'return_home': var.db.get(SECTION, 'return_home', fallback='1') == '1',
        }

    def save_channel_settings(self, default=None, follow=None, follow_user=None, return_home=None):
        if default is not None:
            var.db.set(SECTION, 'default', json.dumps(list(default), ensure_ascii=False))
        if follow is not None:
            if follow not in FOLLOW_MODES:
                raise ValueError(follow)
            var.db.set(SECTION, 'follow', follow)
        if follow_user is not None:
            var.db.set(SECTION, 'follow_user', follow_user)
        if return_home is not None:
            var.db.set(SECTION, 'return_home', '1' if return_home else '0')
        self.schedule_follow_check(delay=0.2)

    # ---- 频道树 -----------------------------------------------------------

    def _channels(self):
        return dict(self.mumble.channels)

    def _users(self):
        return list(self.mumble.users.by_session().values())

    def my_channel_id(self):
        try:
            return self.mumble.users.myself.channel_id
        except AttributeError:
            return None

    def channel_path(self, channel_id, channels=None):
        """根频道 -> [];其余返回从根往下的名字列表。"""
        channels = channels or self._channels()
        path = []
        seen = set()
        current = channels.get(channel_id)
        while current is not None and current.get('channel_id', 0) != 0 and current['channel_id'] not in seen:
            seen.add(current['channel_id'])
            path.insert(0, current.get('name', ''))
            current = channels.get(current.get('parent'))
        return path

    def find_channel_by_path(self, path, channels=None):
        """按名字逐级向下找,返回 channel_id;找不到返回 None。"""
        channels = channels or self._channels()
        current = 0 if 0 in channels else None
        if current is None:
            return None
        for name in path:
            nxt = None
            for cid, ch in channels.items():
                if ch.get('parent') == current and cid != current and ch.get('name') == name:
                    nxt = cid
                    break
            if nxt is None:
                return None
            current = nxt
        return current

    def _is_ignored(self, user):
        """bot 自己和 when_nobody_in_channel_ignore 里列出的其他 bot 不算人。"""
        myself = getattr(self.mumble.users, 'myself', None)
        if myself is not None and user.session == myself.session:
            return True
        return user.name in getattr(self, 'bots', set())

    def humans_by_channel(self):
        counts = {}
        for user in self._users():
            if self._is_ignored(user):
                continue
            counts.setdefault(user.channel_id, []).append(user)
        return counts

    def channel_tree(self):
        """给 Web 用的嵌套频道树快照。"""
        channels = self._channels()
        users = self._users()
        myself = getattr(self.mumble.users, 'myself', None)
        by_channel = {}
        for u in users:
            by_channel.setdefault(u.channel_id, []).append({
                'session': u.session,
                'name': u.name,
                'is_me': myself is not None and u.session == myself.session,
                'is_bot': self._is_ignored(u),
                'muted': bool(getattr(u, 'self_mute', False) or getattr(u, 'mute', False)),
                'deafened': bool(getattr(u, 'self_deaf', False) or getattr(u, 'deaf', False)),
            })
        children = {}
        for cid, ch in channels.items():
            if cid == 0:
                continue
            children.setdefault(ch.get('parent', 0), []).append(cid)

        def node(cid, depth=0):
            ch = channels[cid]
            kids = sorted(children.get(cid, []),
                          key=lambda c: (channels[c].get('position', 0), channels[c].get('name', '')))
            return {
                'id': cid,
                'name': ch.get('name') or ('Root' if cid == 0 else '?'),
                'path': self.channel_path(cid, channels),
                'temporary': bool(ch.get('temporary', False)),
                'users': sorted(by_channel.get(cid, []), key=lambda u: u['name'].lower()),
                'children': [node(k, depth + 1) for k in kids if depth < 32],
            }

        return node(0) if 0 in channels else None

    # ---- 移动 -------------------------------------------------------------

    def move_to_channel(self, channel_id, reason=''):
        if channel_id is None or channel_id == self.my_channel_id():
            return False
        channel = self.mumble.channels.get(channel_id)
        if channel is None:
            return False
        log.info("channel: moving to %s%s", '/'.join(self.channel_path(channel_id)) or '(root)',
                 f" ({reason})" if reason else '')
        channel.move_in()
        return True

    def join_channel(self):
        """去默认频道(启动时、!oust 时调用)。"""
        path = self.channel_settings()['default']
        if not path:
            return
        cid = self.find_channel_by_path(path)
        if cid is None:
            log.warning("channel: default channel %s not found on the server", '/'.join(path))
            return
        self.move_to_channel(cid, 'default channel')

    # ---- 跟随 -------------------------------------------------------------

    def _init_follow(self):
        self._follow_lock = threading.Lock()
        self._follow_timer = None
        self._user_channels = {}  # session -> channel_id,用来知道"从哪个频道走的"
        self._last_leaver = None  # (time, 去向 channel_id 或 None=下线)

    def _note_user_event(self, user, kind):
        """在 pymumble 回调里调用:只记账,不做决策。"""
        if not hasattr(self, '_follow_lock'):
            self._init_follow()
        mine = self.my_channel_id()
        old = self._user_channels.get(user.session)
        if kind == 'removed':
            self._user_channels.pop(user.session, None)
            if old is not None and old == mine and not self._is_ignored(user):
                self._last_leaver = (time.time(), None)
        else:
            new = getattr(user, 'channel_id', 0)
            self._user_channels[user.session] = new
            if old is not None and old != new and old == mine and not self._is_ignored(user):
                self._last_leaver = (time.time(), new)
        self.schedule_follow_check()

    def schedule_follow_check(self, delay=DEBOUNCE_SECONDS):
        if not hasattr(self, '_follow_lock'):
            self._init_follow()
        with self._follow_lock:
            if self._follow_timer is not None:
                self._follow_timer.cancel()
            self._follow_timer = threading.Timer(delay, self._safe_follow_check)
            self._follow_timer.daemon = True
            self._follow_timer.start()

    def _safe_follow_check(self):
        try:
            self.follow_check()
        except Exception:
            log.exception("channel: follow check failed")

    def follow_target(self):
        """按当前设置算出应该去的频道;不需要移动返回 None。"""
        settings = self.channel_settings()
        mode = settings['follow']
        mine = self.my_channel_id()
        humans = self.humans_by_channel()

        if mode == 'user':
            name = settings['follow_user']
            for users in humans.values():
                for u in users:
                    if u.name == name:
                        return u.channel_id if u.channel_id != mine else None
            # 跟随对象不在线:退回到"空了就找人"的逻辑
        elif mode != 'auto':
            return None

        if humans.get(mine):
            return None  # 本频道还有人,不动
        if self._last_leaver and time.time() - self._last_leaver[0] < LAST_LEAVER_TTL:
            target = self._last_leaver[1]
            if target is not None and humans.get(target):
                return target
        if humans:
            # 人最多的频道;平手取 id 小的,结果稳定
            return max(humans, key=lambda cid: (len(humans[cid]), -cid))
        if settings['return_home'] and settings['default'] is not None:
            home = self.find_channel_by_path(settings['default'])
            if home is not None and home != mine:
                return home
        return None

    def follow_check(self):
        target = self.follow_target()
        if target is not None and self.move_to_channel(target, 'following users'):
            self._last_leaver = None

    def follow_would_move(self):
        """when_nobody_in_channel 用:跟随马上要带 bot 去别的频道时,就别暂停/清空了。"""
        try:
            return self.follow_target() is not None
        except Exception:
            return False
