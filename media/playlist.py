import json
import threading
import logging
import random
import time

import variables as var
from media.cache import (CachedItemWrapper, ItemNotCachedError,
                         get_cached_wrapper_from_dict, get_cached_wrapper_by_id)
from database import Condition
from media.item import ValidationFailedError, PreparationFailedError


def get_playlist(mode, _list=None, _index=None):
    index = -1
    if _list and _index is None:
        index = _list.current_index

    if _list is None:
        if mode == "one-shot":
            return OneshotPlaylist()
        elif mode == "repeat":
            return RepeatPlaylist()
        elif mode == "single":
            return SingleLoopPlaylist()
        elif mode == "random":
            return RandomPlaylist()
        elif mode == "autoplay":
            return AutoPlaylist()
    else:
        if mode == "one-shot":
            return OneshotPlaylist().from_list(_list, index)
        elif mode == "repeat":
            return RepeatPlaylist().from_list(_list, index)
        elif mode == "single":
            return SingleLoopPlaylist().from_list(_list, index)
        elif mode == "random":
            return RandomPlaylist().from_list(_list, index)
        elif mode == "autoplay":
            return AutoPlaylist().from_list(_list, index)
    raise


class BasePlaylist(list):
    def __init__(self):
        super().__init__()
        self.current_index = -1
        self.version = 0  # increase by one after each change
        self.mode = "base"  # "repeat", "random"
        self.pending_items = []
        self.log = logging.getLogger("bot")
        self.validating_thread_lock = threading.Lock()
        self.playlist_lock = threading.RLock()
        # 删掉当前曲后指针已经落在"原来的下一首"上,下一次 next() 不再前进
        self._stay_once = False

    def is_empty(self):
        return True if len(self) == 0 else False

    def from_list(self, _list, current_index):
        self.version += 1
        self._stay_once = False
        super().clear()
        self.extend(_list)
        self.current_index = current_index

        return self

    def append(self, item: CachedItemWrapper):
        with self.playlist_lock:
            self.version += 1
            super().append(item)
            self.pending_items.append(item)

        self.async_validate()

        return item

    def insert(self, index, item):
        with self.playlist_lock:
            self.version += 1
            if index == -1:
                index = self.current_index

            super().insert(index, item)

            if index <= self.current_index:
                self.current_index += 1

            self.pending_items.append(item)

        self.async_validate()

        return item

    def extend(self, items):
        with self.playlist_lock:
            self.version += 1
            super().extend(items)
            self.pending_items.extend(items)

        self.async_validate()
        return items

    def next(self):
        with self.playlist_lock:
            if len(self) == 0:
                self._stay_once = False
                return False
            if self._take_stay():
                return self[self.current_index]

            if self.current_index < len(self) - 1:
                self.current_index += 1
                return self[self.current_index]
            else:
                return False

    def _take_stay(self):
        """消费 _stay_once:指针在队列范围内时这次 next() 原地不动。"""
        stay, self._stay_once = self._stay_once, False
        return stay and 0 <= self.current_index < len(self)

    def point_to(self, index):
        with self.playlist_lock:
            if -1 <= index < len(self):
                self._stay_once = False
                self.current_index = index

    def skip_current(self):
        # A user skip. Most modes don't care (next() already advances);
        # single-loop overrides this to break out of the loop for one step.
        pass

    def find(self, id):
        with self.playlist_lock:
            for index, wrapper in enumerate(self):
                if wrapper.item.id == id:
                    return index
        return None

    def __delitem__(self, key):
        return self.remove(key)

    def remove(self, index):
        with self.playlist_lock:
            self.version += 1
            if index > len(self) - 1:
                return False

            removed = self[index]
            super().__delitem__(index)

            if self.current_index > index:
                self.current_index -= 1

        # reference counter
        counter = 0
        for wrapper in self:
            if wrapper.id == removed.id:
                counter += 1

        if counter == 0:
            var.cache.free(removed.id)
        return removed

    def remove_by_id(self, id, rewind=False):
        """删掉队列里所有 id 相同的条目。

        删到当前曲时指针会落在"原来的下一首"上。rewind=True 表示调用方随后会
        next()(主循环播放失败、正在播的歌被删),让那次 next() 原地不动,
        正好落到原来的下一首,而不是跳过它。"""
        with self.playlist_lock:
            to_be_removed = [index for index, wrapper in enumerate(self) if wrapper.id == id]
            if not to_be_removed:
                return
            self.version += 1
            removed_current = self.current_index in to_be_removed

            # 倒序删:先删后面的,前面的下标不会因此移位
            for index in reversed(to_be_removed):
                self.remove(index)

            if removed_current and rewind:
                self.rewind_after_removing_current()

    def remove_current(self, rewind):
        """删掉当前这一条(只删这一个位置,不管有没有重复)。rewind 含义同 remove_by_id。"""
        with self.playlist_lock:
            if not 0 <= self.current_index < len(self):
                return None
            removed = self.remove(self.current_index)
            if rewind:
                self.rewind_after_removing_current()
            return removed

    def rewind_after_removing_current(self):
        # 删掉当前曲后指针已经指向原来的下一首(或越过队尾),让接下来那次
        # next() 原地不动。不直接退指针:one-shot 下 -1 会被 current_item()
        # 改回 0,next() 随后就把原来的下一首删了。
        self._stay_once = True

    def current_item(self):
        with self.playlist_lock:
            # 指针为 -1(还没开始 / 刚打乱)或越过队尾(删掉了最后一首)时没有当前曲。
            # 以前会返回 self[-1](队尾那首)或抛 IndexError。
            if not 0 <= self.current_index < len(self):
                return False

            return self[self.current_index]

    def next_index(self):
        with self.playlist_lock:
            if self.current_index < len(self) - 1:
                return self.current_index + 1

        return False

    def next_item(self):
        with self.playlist_lock:
            if self.current_index < len(self) - 1:
                return self[self.current_index + 1]

        return False

    def upcoming_items(self, count):
        # Peek at up to `count` items that would play after the current one,
        # in play order, without changing any playlist state. Used by the
        # player's prefetcher.
        with self.playlist_lock:
            if count <= 0:
                return []
            start = max(self.current_index + 1, 0)
            return list(self[start:start + count])

    def randomize(self):
        with self.playlist_lock:
            # current_index will lose track after shuffling, thus we take current music out before shuffling
            # current = self.current_item()
            # del self[self.current_index]

            random.shuffle(self)

            # self.insert(0, current)
            self.current_index = -1
            self._stay_once = False
            self.version += 1

    def clear(self):
        with self.playlist_lock:
            self.version += 1
            self.current_index = -1
            self._stay_once = False
            super().clear()

        var.cache.free_all()

    def save(self):
        with self.playlist_lock:
            var.db.remove_section("playlist_item")
            assert self.current_index is not None
            var.db.set("playlist", "current_index", self.current_index)

            for index, music in enumerate(self):
                var.db.set("playlist_item", str(index), json.dumps({'id': music.id, 'user': music.user}))

    def load(self):
        current_index = var.db.getint("playlist", "current_index", fallback=-1)
        if current_index == -1:
            return

        items = var.db.items("playlist_item")
        if items:
            music_wrappers = []
            items.sort(key=lambda v: int(v[0]))
            for item in items:
                item = json.loads(item[1])
                music_wrapper = get_cached_wrapper_by_id(item['id'], item['user'])
                if music_wrapper:
                    music_wrappers.append(music_wrapper)
            self.from_list(music_wrappers, current_index)

    def _debug_print(self):
        print("===== Playlist(%d) =====" % self.current_index)
        for index, item_wrapper in enumerate(self):
            if index == self.current_index:
                print("-> %d %s" % (index, item_wrapper.format_debug_string()))
            else:
                print("%d %s" % (index, item_wrapper.format_debug_string()))
        print("=====     End     =====")

    def async_validate(self):
        if not self.validating_thread_lock.locked():
            time.sleep(0.1)  # Just avoid validation finishes too fast and delete songs while something is reading it.
            th = threading.Thread(target=self._check_valid, name="Validating")
            th.daemon = True
            th.start()

    def _check_valid(self):
        self.log.debug("playlist: start validating...")
        self.validating_thread_lock.acquire()
        try:
            self._validate_pending()
        finally:
            # 以前异常会让锁永远不释放,之后加的歌再也不会被校验
            self.validating_thread_lock.release()

    def _validate_pending(self):
        while len(self.pending_items) > 0:
            item = self.pending_items.pop()
            try:
                item.item()
            except ItemNotCachedError:
                # In some very subtle case, items are removed and freed from
                # the playlist and the cache, before validation even starts,
                # causes, freed items remain in pending_items.
                # Simply ignore these items here.
                continue

            self.log.debug("playlist: validating %s" % item.format_debug_string())
            ver = item.version

            try:
                item.validate()
            except ValidationFailedError as e:
                self.log.debug("playlist: validating failed.")
                if var.bot:
                    var.bot.send_channel_msg(e.msg)
                self._drop(item.id)
                var.cache.free_and_delete(item.id)
                continue
            except Exception:
                # 任何意外错误只影响这一首,校验线程继续处理后面的
                self.log.exception("playlist: unexpected error while validating %s", item.id)
                self._drop(item.id)
                continue

            if item.version > ver:
                self.version += 1

        self.log.debug("playlist: validating finished.")

    def _drop(self, item_id):
        # 由 bot 决定删掉当前曲后指针怎么走(见 PlayerMixin.drop_from_queue)
        if var.bot is not None and hasattr(var.bot, 'drop_from_queue'):
            var.bot.drop_from_queue(item_id)
        else:
            self.remove_by_id(item_id)


class OneshotPlaylist(BasePlaylist):
    def __init__(self):
        super().__init__()
        self.mode = "one-shot"
        self.current_index = -1

    def current_item(self):
        with self.playlist_lock:
            if len(self) == 0:
                self.current_index = -1
                return False

            if self.current_index == -1:
                self.current_index = 0

            return self[self.current_index]

    def from_list(self, _list, current_index):
        with self.playlist_lock:
            if len(_list) > 0:
                if current_index > -1:
                    for i in range(current_index):
                        _list.pop(0)
                    return super().from_list(_list, 0)
                return super().from_list(_list, -1)
            return self

    def next(self):
        with self.playlist_lock:
            if len(self) > 0 and self._take_stay():
                # 当前曲已经被删掉了,队首就是原来的下一首,不能再删一次
                self.version += 1
                self.current_index = 0
                return self[0]

            if len(self) > 0:
                self.version += 1

                if self.current_index != -1:
                    super().__delitem__(self.current_index)
                    if len(self) == 0:
                        return False
                else:
                    self.current_index = 0

                return self[0]
            else:
                self.current_index = -1
                return False

    def next_index(self):
        if len(self) > 1:
            return 1
        else:
            return False

    def next_item(self):
        if len(self) > 1:
            return self[1]
        else:
            return False

    def upcoming_items(self, count):
        # index 0 is (or is about to become) the current item
        with self.playlist_lock:
            if count <= 0:
                return []
            return list(self[1:1 + count])

    def point_to(self, index):
        with self.playlist_lock:
            self.version += 1
            self._stay_once = False
            self.current_index = -1
            for i in range(index):
                super().__delitem__(0)


class RepeatPlaylist(BasePlaylist):
    def __init__(self):
        super().__init__()
        self.mode = "repeat"

    def next(self):
        with self.playlist_lock:
            if len(self) == 0:
                self._stay_once = False
                return False
            if self._take_stay():
                return self[self.current_index]

            if self.current_index < len(self) - 1:
                self.current_index += 1
                return self[self.current_index]
            else:
                self.current_index = 0
                return self[0]

    def next_index(self):
        with self.playlist_lock:
            if self.current_index < len(self) - 1:
                return self.current_index + 1
            else:
                return 0

    def next_item(self):
        if len(self) == 0:
            return False
        return self[self.next_index()]

    def upcoming_items(self, count):
        # wraps around, but never repeats an item within one window
        with self.playlist_lock:
            n = len(self)
            if n <= 1 or count <= 0:
                return []
            items = []
            index = self.current_index
            for _ in range(min(count, n - 1)):
                index = (index + 1) % n
                items.append(self[index])
            return items


class SingleLoopPlaylist(BasePlaylist):
    """Single-track loop: the current song plays again when it ends. A user
    skip (chat !skip or the web skip button) advances to the next track -
    wrapping around like repeat mode - and keeps looping there."""

    def __init__(self):
        super().__init__()
        self.mode = "single"
        self._advance_once = False

    def skip_current(self):
        with self.playlist_lock:
            self._advance_once = True

    def next(self):
        with self.playlist_lock:
            if len(self) == 0:
                self.current_index = -1
                self._stay_once = False
                return False

            if not 0 <= self.current_index < len(self):
                # 还没开始,或者删掉了队尾那首:从头开始
                self.current_index = 0
                self._stay_once = False
            elif self._take_stay():
                pass  # 当前曲被删了,指针已经在原来的下一首上
            elif self._advance_once:
                self.current_index = (self.current_index + 1) % len(self)
            self._advance_once = False

            return self[self.current_index]

    def next_index(self):
        with self.playlist_lock:
            if len(self) == 0:
                return False
            return max(self.current_index, 0)

    def next_item(self):
        with self.playlist_lock:
            if len(self) == 0:
                return False
            return self[max(self.current_index, 0)]

    def upcoming_items(self, count):
        # nothing else will play on its own - no point prefetching
        return []


class RandomPlaylist(BasePlaylist):
    def __init__(self):
        super().__init__()
        self.mode = "random"

    def from_list(self, _list, current_index):
        self.version += 1
        random.shuffle(_list)
        return super().from_list(_list, -1)

    def next(self):
        with self.playlist_lock:
            if len(self) == 0:
                self._stay_once = False
                return False
            if self._take_stay():
                return self[self.current_index]

            if self.current_index < len(self) - 1:
                self.current_index += 1
                return self[self.current_index]
            else:
                self.version += 1
                self.randomize()
                self.current_index = 0
                return self[0]


class AutoPlaylist(OneshotPlaylist):
    def __init__(self):
        super().__init__()
        self.mode = "autoplay"

    def refresh(self):
        dicts = var.music_db.query_random_music(var.config.getint("bot", "autoplay_length"),
                                                Condition().and_not_sub_condition(
                                                    Condition().and_like('tags', "%don't autoplay,%")))

        if dicts:
            _list = [get_cached_wrapper_from_dict(_dict, "AutoPlay") for _dict in dicts]
            self.from_list(_list, -1)

    # def from_list(self, _list, current_index):
    #     self.version += 1
    #     self.refresh()
    #     return self

    def clear(self):
        super().clear()
        self.refresh()

    def next(self):
        if len(self) == 0:
            self.refresh()
        return super().next()
