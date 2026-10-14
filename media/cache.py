import logging
import os

import json
import threading

from media.item import item_builders, item_id_generators, dict_to_item
import media.file
import media.url
import media.url_from_playlist
import media.radio
import media.livestream
from database import MusicDatabase, Condition
import variables as var
import util


class ItemNotCachedError(Exception):
    pass


class MusicCache(dict):
    def __init__(self, db: MusicDatabase):
        super().__init__()
        self.db = db
        self.log = logging.getLogger("bot")
        self.dir_lock = threading.Lock()

    def get_item_by_id(self, id):
        if id in self:
            return self[id]

        # if not cached, query the database
        item = self.fetch(id)
        if item is not None:
            self[id] = item
            self.log.debug("library: music found in database: %s" % item.format_debug_string())
            return item
        else:
            return None
            # print(id)
            # raise KeyError("Unable to fetch item from the database! Please try to refresh the cache by !recache.")

    def get_item(self, **kwargs):
        # kwargs should provide type and id, and parameters to build the item if not existed in the library.
        # if cached
        if 'id' in kwargs:
            id = kwargs['id']
        else:
            id = item_id_generators[kwargs['type']](**kwargs)

        if id in self:
            return self[id]

        # if not cached, query the database
        item = self.fetch(id)
        if item is not None:
            self[id] = item
            self.log.debug("library: music found in database: %s" % item.format_debug_string())
            return item

        # if not in the database, build one
        self[id] = item_builders[kwargs['type']](**kwargs)  # newly built item will not be saved immediately
        return self[id]

    def get_items_by_tags(self, tags):
        music_dicts = self.db.query_music_by_tags(tags)
        items = []
        if music_dicts:
            for music_dict in music_dicts:
                id = music_dict['id']
                # 已缓存的对象可能正在下载/播放,不能用库里的旧快照顶替
                if id not in self:
                    self[id] = dict_to_item(music_dict)
                items.append(self[id])

        return items

    def fetch(self, id):
        music_dict = self.db.query_music_by_id(id)
        if music_dict:
            self[id] = dict_to_item(music_dict)
            return self[id]
        else:
            return None

    def save(self, id):
        self.save_item(self[id])

    def save_item(self, item):
        self.log.debug("library: music save into database: %s" % item.format_debug_string())
        self.db.insert_music(item.to_dict())
        self.db.manage_special_tags()

    def free_and_delete(self, id):
        item = self.get_item_by_id(id)
        if item:
            self.log.debug("library: DELETE item from the database: %s" % item.format_debug_string())

            if item.type == 'url':
                if os.path.exists(item.path):
                    os.remove(item.path)

            if item.id in self:
                del self[item.id]
            self.db.delete_music(Condition().and_equal("id", item.id))

    def free(self, id):
        if id in self:
            self.log.debug("library: cache freed for item: %s" % self[id].format_debug_string())
            del self[id]

    def free_all(self):
        self.log.debug("library: all cache freed")
        self.clear()

    def build_dir_cache(self):
        self.dir_lock.acquire()
        self.log.info("library: rebuild directory cache")
        files = util.get_recursive_file_list_sorted(var.music_folder)

        # remove deleted files
        results = self.db.query_music(Condition().or_equal('type', 'file'))
        for result in results:
            if result['path'] not in files:
                self.log.debug("library: music file missed: %s, delete from library." % result['path'])
                self.db.delete_music(Condition().and_equal('id', result['id']))
            else:
                files.remove(result['path'])

        for file in files:
            results = self.db.query_music(Condition().and_equal('path', file))
            if not results:
                item = item_builders['file'](path=file)
                self.log.debug("library: music save into database: %s" % item.format_debug_string())
                self.db.insert_music(item.to_dict())

        self.db.manage_special_tags()
        self.dir_lock.release()


class CachedItemWrapper:
    def __init__(self, lib, id, type, user):
        self.lib = lib
        self.id = id
        self.user = user
        self.type = type
        self.log = logging.getLogger("bot")
        self.version = 0
        # 整张歌单一起入队时打的分组标记(队列里折叠成一个歌单显示),见 tag_group()
        self.group = None

    def item(self):
        if self.id in self.lib:
            return self.lib[self.id]
        else:
            raise ItemNotCachedError(f"Uncached item of id {self.id}, type {self.type}.")

    def to_dict(self):
        dict = self.item().to_dict()
        dict['user'] = self.user
        return dict

    # 校验 / 下载可能要几十秒,期间清空队列、一次性模式播完、换歌单播放都会把条目
    # 移出缓存。全程只用开始时拿到的那个对象,结束后不再回头查缓存
    # (以前这里会 ItemNotCachedError,日志里就是 "unexpected error while downloading")。
    def validate(self):
        item = self.item()
        ret = item.validate()
        if ret:
            self._sync(item)
        return ret

    def prepare(self):
        item = self.item()
        ret = item.prepare()
        if ret:
            self._sync(item)
        return ret

    def _sync(self, item):
        if item.version <= self.version:
            return
        self.version = item.version
        if self.lib.get(self.id) is item:
            self.lib.save_item(item)
        else:
            # 已被移出缓存(或换成了别的对象):不再写库,免得把刚删掉的条目又写回去
            self.log.debug("library: %s left the cache while preparing, not saved", self.id[:7])

    def uri(self):
        return self.item().uri()

    def add_tags(self, tags):
        self.item().add_tags(tags)
        if self.item().version > self.version:
            self.version = self.item().version
            self.lib.save(self.id)

    def remove_tags(self, tags):
        self.item().remove_tags(tags)
        if self.item().version > self.version:
            self.version = self.item().version
            self.lib.save(self.id)

    def clear_tags(self):
        self.item().clear_tags()
        if self.item().version > self.version:
            self.version = self.item().version
            self.lib.save(self.id)

    def is_ready(self):
        return self.item().is_ready()

    def is_failed(self):
        return self.item().is_failed()

    def playable_from(self, playhead, buffer_secs):
        # stream-while-downloading probe; only URL items implement it
        fn = getattr(self.item(), 'playable_from', None)
        return fn(playhead, buffer_secs) if fn else False

    def format_current_playing(self):
        return self.item().format_current_playing(self.user)

    def format_song_string(self):
        return self.item().format_song_string(self.user)

    def format_title(self):
        return self.item().format_title()

    def format_debug_string(self):
        return self.item().format_debug_string()

    def display_type(self):
        return self.item().display_type()


# Remember!!! Get wrapper functions will automatically add items into the cache!
def get_cached_wrapper(item, user):
    if item:
        # 同一首已经在缓存里(在队列里、正在下载)时沿用那个对象:库里读出来的是旧快照,
        # 顶替掉会让正在进行的下载"丢失",主循环又从头下一遍
        if item.id not in var.cache:
            var.cache[item.id] = item
        return CachedItemWrapper(var.cache, item.id, item.type, user)
    return None


def tag_group(wrappers, name, kind='playlist', ref=None):
    """把一批一起入队的条目标成同一组:Web 队列里折叠成一个歌单,而不是一长串单曲。
    每次入队都是新的一组(同一歌单播两次就是两组)。"""
    import uuid
    group = {'key': uuid.uuid4().hex[:12], 'name': str(name or '')[:200], 'kind': kind, 'ref': ref}
    for wrapper in wrappers:
        wrapper.group = group
    return wrappers

def get_cached_wrappers(items, user):
    wrappers = []
    for item in items:
        if item:
            wrappers.append(get_cached_wrapper(item, user))

    return wrappers

def get_cached_wrapper_from_scrap(**kwargs):
    item = var.cache.get_item(**kwargs)
    if 'user' not in kwargs:
        raise KeyError("Which user added this song?")
    return CachedItemWrapper(var.cache, item.id, kwargs['type'], kwargs['user'])

def get_cached_wrapper_from_dict(dict_from_db, user):
    if dict_from_db:
        item = dict_to_item(dict_from_db)
        return get_cached_wrapper(item, user)
    return None

def get_cached_wrappers_from_dicts(dicts_from_db, user):
    items = []
    for dict_from_db in dicts_from_db:
        if dict_from_db:
            items.append(get_cached_wrapper_from_dict(dict_from_db, user))

    return items

def get_cached_wrapper_by_id(id, user):
    item = var.cache.get_item_by_id(id)
    if item:
        return CachedItemWrapper(var.cache, item.id, item.type, user)

def get_cached_wrappers_by_tags(tags, user):
    items = var.cache.get_items_by_tags(tags)
    ret = []
    for item in items:
        ret.append(CachedItemWrapper(var.cache, item.id, item.type, user))
    return ret
