"""下载缓存管理:列出 / 固定(pin) / LRU 淘汰 / 手动删除 / 存入本地曲库。

缓存文件都在 tmp_folder 顶层,以条目 id(32 位 hex,url 的 md5)命名:
  <id>             音频本体(yt-dlp outtmpl 不带扩展名)
  <id>.jpg         封面
  <id>.part / .incomplete / ...  下载中的半成品
同一个 id 的文件视为一个缓存条目,一起计算大小、一起删除。

保留策略(优先级从高到低):
  1. 正在下载、在播放队列里的条目:任何清理都不碰。
  2. 固定(pinned)的条目:自动清理永不删除,只能手动删。
  3. 常听的条目(播放次数 >= cache_auto_keep_plays):按天数过期的清理跳过;
     容量超限时最后才淘汰。
  4. 其余按"最后使用时间"= max(最后播放, 文件修改时间) 从旧到新淘汰(LRU)。

固定状态存在 settings 数据库的 cache_pin 段(option = 条目 id)。
"""

import logging
import mimetypes
import os
import re
import shutil
import time

import variables as var
from database import Condition

log = logging.getLogger("bot")

HEX_ID_RE = re.compile(r'^[0-9a-f]{32}$')
PARTIAL_SUFFIXES = ('.part', '.ytdl', '.tmp', '.temp', '.frag', '.download', '.incomplete')
PIN_SECTION = 'cache_pin'
MB = 1024 * 1024


# ---- 配置 -----------------------------------------------------------------

def size_limit_bytes():
    """None = 不限;0 = 不保留缓存(播完即可删)。"""
    mb = var.config.getint('bot', 'tmp_folder_max_size', fallback=4096)
    if mb < 0:
        return None
    return mb * MB


def auto_keep_plays():
    return var.config.getint('bot', 'cache_auto_keep_plays', fallback=3)


def cache_folder():
    return os.path.abspath(getattr(var, 'tmp_folder', '') or '')


# ---- 固定 -----------------------------------------------------------------

def pinned_ids():
    try:
        return {option for option, _ in var.db.items(PIN_SECTION)}
    except Exception:
        return set()


def set_pinned(item_id, pinned):
    if not HEX_ID_RE.match(item_id or ''):
        raise ValueError(item_id)
    if pinned:
        var.db.set(PIN_SECTION, item_id, str(time.time()))
    else:
        var.db.remove_option(PIN_SECTION, item_id)


# ---- 枚举 -----------------------------------------------------------------

def protected_ids():
    """在播放队列里或正在下载的条目 id。"""
    ids = set()
    playlist = getattr(var, 'playlist', None)
    if playlist is not None:
        for wrapper in list(playlist):
            ids.add(getattr(wrapper, 'id', None))
    cache = getattr(var, 'cache', None)
    if cache is not None:
        for item in list(cache.values()):
            if getattr(item, 'downloading', False):
                ids.add(getattr(item, 'id', None))
    ids.discard(None)
    return ids


def downloading_ids():
    cache = getattr(var, 'cache', None)
    if cache is None:
        return set()
    return {getattr(i, 'id', None) for i in list(cache.values()) if getattr(i, 'downloading', False)}


def _usage():
    history = getattr(var, 'play_history', None)
    if history is None:
        return {}
    try:
        return history.usage()
    except Exception:
        log.debug("cache: could not read play history", exc_info=True)
        return {}


def _records():
    """music db 里 url 条目的标题/链接/时长,按 id 索引。"""
    try:
        rows = var.music_db.query_music(Condition().and_equal('type', 'url'))
    except Exception:
        return {}
    return {r['id']: r for r in rows}


def scan_files(folder=None):
    """{id: [(path, size, mtime), ...]},只认 32 位 hex 前缀的文件。"""
    folder = folder or cache_folder()
    groups = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return groups
    for name in names:
        base = name.split('.', 1)[0]
        if not HEX_ID_RE.match(base):
            continue
        path = os.path.join(folder, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        groups.setdefault(base, []).append((path, st.st_size, st.st_mtime))
    return groups


def list_entries():
    """所有缓存条目(dict),按最后使用时间倒序。"""
    folder = cache_folder()
    groups = scan_files(folder)
    pins = pinned_ids()
    protected = protected_ids()
    downloading = downloading_ids()
    usage = _usage()
    records = _records()
    keep_plays = auto_keep_plays()
    entries = []
    for item_id, files in groups.items():
        audio = os.path.join(folder, item_id)
        has_audio = any(p == audio for p, _, _ in files)
        partial = any(p.endswith(PARTIAL_SUFFIXES) for p, _, _ in files)
        mtime = max(m for _, _, m in files)
        plays, last_played = usage.get(item_id, (0, 0.0))
        record = records.get(item_id) or {}
        entries.append({
            'id': item_id,
            'title': record.get('title') or '',
            'url': record.get('url') or '',
            'duration': record.get('duration') or 0,
            'size': sum(s for _, s, _ in files),
            'files': len(files),
            'complete': has_audio and not partial,
            'mtime': mtime,
            'last_played': last_played or 0,
            'last_used': max(mtime, last_played or 0),
            'plays': plays,
            'pinned': item_id in pins,
            'frequent': keep_plays > 0 and plays >= keep_plays,
            'in_queue': item_id in protected and item_id not in downloading,
            'downloading': item_id in downloading or partial,
        })
    entries.sort(key=lambda e: e['last_used'], reverse=True)
    return entries


def summary(entries=None):
    entries = list_entries() if entries is None else entries
    folder = cache_folder()
    try:
        disk = shutil.disk_usage(folder)
        disk_total, disk_free = disk.total, disk.free
    except OSError:
        disk_total = disk_free = 0
    return {
        'folder': folder,
        # /tmp 在 Docker 容器里不持久,重建容器缓存就没了
        'persistent': not (folder == '/tmp' or folder.startswith('/tmp/')),
        'total_bytes': sum(e['size'] for e in entries),
        'count': len(entries),
        'pinned_bytes': sum(e['size'] for e in entries if e['pinned']),
        'limit_bytes': size_limit_bytes(),
        'disk_total': disk_total,
        'disk_free': disk_free,
        'auto_keep_plays': auto_keep_plays(),
        'keep_days': var.config.getint('bot', 'cleanup_keep_days', fallback=7),
    }


# ---- 删除 / 淘汰 ------------------------------------------------------------

def _remove_files(paths):
    freed = 0
    removed = []
    for path in paths:
        try:
            size = os.path.getsize(path)
            os.remove(path)
            freed += size
            removed.append(os.path.abspath(path))
        except OSError:
            continue
    return freed, removed


def invalidate(item_ids):
    """文件删掉后同步数据库与内存缓存:url 条目回到 validated,下次点播重新下载。"""
    cache = getattr(var, 'cache', None)
    for item_id in item_ids:
        try:
            record = var.music_db.query_music_by_id(item_id)
            if record and record.get('type') == 'url' and record.get('ready') == 'yes':
                record['ready'] = 'validated'
                var.music_db.insert_music(dict(record))
        except Exception:
            log.debug("cache: could not invalidate db record %s", item_id, exc_info=True)
        if cache is not None and item_id in cache:
            item = cache[item_id]
            if getattr(item, 'ready', None) == 'yes':
                item.ready = 'validated'


def delete_entry(item_id, force=False):
    """手动删除一个缓存条目。正在下载的拒绝(返回 None);
    在队列里的默认也拒绝,force=True 才删(之后轮到它会重新下载)。"""
    if not HEX_ID_RE.match(item_id or ''):
        return None
    if item_id in downloading_ids():
        return None
    if not force and item_id in protected_ids():
        return None
    files = scan_files().get(item_id, [])
    freed, _ = _remove_files([p for p, _, _ in files])
    invalidate([item_id])
    try:
        var.db.remove_option(PIN_SECTION, item_id)
    except Exception:
        pass
    if freed:
        log.info("cache: deleted %s (%.1f MB)", item_id, freed / MB)
    return freed


def evict(target_bytes, include_frequent=True, include_pinned=False):
    """把缓存总量压到 target_bytes 以下。返回 (freed_bytes, removed_ids)。"""
    entries = list_entries()
    total = sum(e['size'] for e in entries)
    if total <= target_bytes:
        return 0, []
    candidates = [e for e in entries
                  if not e['in_queue'] and not e['downloading']
                  and (include_pinned or not e['pinned'])
                  and (include_frequent or not e['frequent'])]
    # 普通条目先走,常听的其次;同一档里最久没用的先走
    candidates.sort(key=lambda e: (e['pinned'], e['frequent'], e['last_used']))
    freed_total = 0
    removed = []
    folder = cache_folder()
    for e in candidates:
        if total - freed_total <= target_bytes:
            break
        paths = [p for p, _, _ in scan_files(folder).get(e['id'], [])]
        freed, _ = _remove_files(paths)
        if freed:
            freed_total += freed
            removed.append(e['id'])
    invalidate(removed)
    if removed:
        log.info("cache: evicted %d item(s), freed %.1f MB", len(removed), freed_total / MB)
    return freed_total, removed


def enforce_size_limit():
    """下载前后调用:超过 tmp_folder_max_size 就按 LRU 淘汰(不碰 pinned)。"""
    limit = size_limit_bytes()
    if limit is None:
        return 0, []
    return evict(limit)


def clear_unpinned():
    return evict(0, include_frequent=True, include_pinned=False)


# ---- 存入本地曲库 -------------------------------------------------------------

_EXT_BY_MIME = {
    'audio/webm': '.webm', 'video/webm': '.webm',
    'audio/mp4': '.m4a', 'video/mp4': '.m4a', 'audio/x-m4a': '.m4a',
    'audio/mpeg': '.mp3', 'audio/ogg': '.ogg', 'audio/opus': '.opus',
    'audio/flac': '.flac', 'audio/x-flac': '.flac', 'audio/x-wav': '.wav',
    'video/x-matroska': '.mka', 'audio/x-matroska': '.mka',
}


def _guess_ext(path):
    try:
        import magic
        mime = magic.from_file(path, mime=True)
    except Exception:
        mime = ''
    return _EXT_BY_MIME.get(mime) or mimetypes.guess_extension(mime or '') or '.audio'


def _safe_filename(title, fallback):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', title or '').strip().strip('.')
    name = re.sub(r'\s+', ' ', name)[:120]
    return name or fallback


def save_to_library(item_id, subfolder='saved'):
    """把缓存音频复制进 music_folder/<subfolder>/,登记成本地曲库条目。
    返回新文件相对 music_folder 的路径;缓存不完整返回 None。"""
    import media.file  # noqa: F401  注册 file 类型
    from media.item import item_builders

    if not HEX_ID_RE.match(item_id or ''):
        return None
    folder = cache_folder()
    src = os.path.join(folder, item_id)
    if not os.path.isfile(src) or os.path.exists(src + '.incomplete'):
        return None
    record = var.music_db.query_music_by_id(item_id) or {}
    title = record.get('title') or item_id
    target_dir = os.path.join(var.music_folder, subfolder)
    os.makedirs(target_dir, exist_ok=True)
    base = _safe_filename(title, item_id)
    ext = _guess_ext(src)
    rel = os.path.join(subfolder, base + ext)
    n = 1
    while os.path.exists(os.path.join(var.music_folder, rel)):
        n += 1
        rel = os.path.join(subfolder, f"{base} ({n}){ext}")
    dst = os.path.join(var.music_folder, rel)
    shutil.copyfile(src, dst)
    cover = src + '.jpg'
    if os.path.isfile(cover):
        try:
            shutil.copyfile(cover, os.path.splitext(dst)[0] + '.jpg')
        except OSError:
            pass
    item = item_builders['file'](path=rel)
    # 下载来的文件基本没有标签,FileItem 会退回用文件名(已去掉 / 等字符),
    # 用缓存记录里的原标题更准确
    item.title = title
    item.keywords = f"{title} {item.artist or ''}".strip()
    var.music_db.insert_music(item.to_dict())
    log.info("cache: saved %s to library as %s", item_id, rel)
    return rel
