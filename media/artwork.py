"""高清封面。

播放页的封面框是 288px(高分屏要 ~600px),而条目里存的 thumbnail 只有 100px
(给 Mumble 聊天消息和旧版界面用),直接拉伸就是糊的。这里另存一份高清图:

  1. 先按视频标题去曲库(网易云 → iTunes → Deezer)找正式发行的专辑封面;
  2. 找不到、或者不敢确定是同一首歌时,用视频封面;
  3. 本地文件用文件自带的封面。

标题解析(parse_title)只负责"猜"出几个可能的歌名,真正决定用不用的是匹配校验
(choose_match):歌名必须完全一致,并且歌手在视频标题/简介/标签/UP 主里出现过,
或者时长与视频几乎相同且只有一位歌手对得上。宁可退回视频封面,也不贴错专辑封面。

高清图存在 artwork_folder(默认 settings 数据库旁的 artwork/),按 LRU 控制总大小;
条目的 artwork 字段记录来源:album / video / embedded / none(都没有,不再重试)。
"""

import base64
import itertools
import logging
import os
import queue
import re
import threading
import time
import unicodedata
from io import BytesIO

import variables as var

log = logging.getLogger("bot")

ALBUM_SIZE = 1000          # 向曲库要的专辑封面边长
MAX_EDGE = 1280            # 存盘时最长边(视频封面 16:9,竖向 720 足够 288px 框的 2x)
SMALL_EDGE = 100           # 条目 thumbnail(base64)的边长,保持旧行为
MAX_LOOKUP_DURATION = 900  # 超过 15 分钟基本是合集/串烧,不去找专辑封面
DURATION_TOLERANCE = 2.0   # 只靠时长确认时允许的误差(秒)
MAX_CALLS_PER_ITEM = 8     # 每首歌最多查几次曲库

DONE_STATES = ('album', 'video', 'embedded')
URL_TYPES = ('url', 'url_from_playlist')

_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


# ---- 存储 -----------------------------------------------------------------------

def folder():
    conf = ''
    if var.config is not None:
        conf = var.config.get('bot', 'artwork_folder', fallback='') or ''
    if conf:
        return conf
    base = os.path.dirname(os.path.abspath(var.settings_db_path)) if var.settings_db_path else \
        os.path.join(var.tmp_folder or '.', '')
    return os.path.join(base, 'artwork')


def path_for(item_id):
    if not item_id or not _ID_RE.match(item_id):
        return None
    return os.path.join(folder(), item_id + '.jpg')


def has_hd(item_id):
    path = path_for(item_id)
    return bool(path) and os.path.isfile(path)


def small_b64(im):
    """100px 的 base64 JPEG,和以前各条目 _prepare_thumbnail 的结果一致。"""
    im = im.copy()
    im.thumbnail((SMALL_EDGE, SMALL_EDGE))
    buffer = BytesIO()
    im.convert('RGB').save(buffer, format='JPEG')
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


def save(item_id, im):
    """存高清图(原子替换),返回 100px 的 base64 缩略图。"""
    from PIL import Image
    path = path_for(item_id)
    small = small_b64(im)
    if not path:
        return small
    os.makedirs(os.path.dirname(path), exist_ok=True)
    big = im.convert('RGB')
    big.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
    tmp = path + '.tmp'
    big.save(tmp, format='JPEG', quality=88, optimize=True)
    os.replace(tmp, path)
    _note_saved()
    return small


_saved_since_prune = 0


def _note_saved():
    global _saved_since_prune
    _saved_since_prune += 1
    if _saved_since_prune >= 20:
        _saved_since_prune = 0
        prune()


def prune():
    """总大小超过 artwork_folder_max_size(MB)时,按最后修改时间从旧到新删。
    被删的条目下次显示时会重新获取。"""
    limit = var.config.getint('bot', 'artwork_folder_max_size', fallback=512) if var.config else 512
    if limit <= 0:
        return
    limit *= 1024 * 1024
    root = folder()
    entries = []
    try:
        for name in os.listdir(root):
            if name.endswith('.jpg'):
                p = os.path.join(root, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, p))
    except OSError:
        return
    total = sum(e[1] for e in entries)
    if total <= limit:
        return
    for _, size, p in sorted(entries):
        try:
            os.remove(p)
            total -= size
        except OSError:
            pass
        if total <= limit * 0.9:
            break


# ---- 文本归一化 ------------------------------------------------------------------

_t2s = None


def _to_simplified(text):
    global _t2s
    if not re.search(r'[一-鿿]', text):
        return text
    if _t2s is None:
        try:
            from opencc import OpenCC
            _t2s = OpenCC('t2s')
        except Exception:
            _t2s = False
    return _t2s.convert(text) if _t2s else text


def _base(text):
    """全角转半角、小写、繁转简。"""
    return _to_simplified(unicodedata.normalize('NFKC', text or '').lower())


_BRACKET_BLOCK = re.compile(r'\([^()]*\)|\[[^\[\]]*\]|【[^【】]*】|〔[^〔〕]*〕|\{[^{}]*\}')
_FEAT = re.compile(r'\s(?:feat|ft)\.?\s.*$')


def key(text):
    """歌名比较用:去掉括号里的版本标注、" - xxx" 后缀和 feat.,只留文字本身。"""
    t = _base(text)
    t = _BRACKET_BLOCK.sub(' ', t)
    t = re.sub(r'\s+[-–—]\s+.*$', '', t)
    t = _FEAT.sub('', t)
    return re.sub(r'[\W_]+', '', t)


def words(text):
    """歌手比较用:保留词边界。"""
    return ' ' + re.sub(r'[\W_]+', ' ', _base(text)).strip() + ' '


# ---- 标题解析 --------------------------------------------------------------------

_QUOTES = [('《', '》'), ('「', '」'), ('『', '』'), ('“', '”'), ('"', '"'), ('〈', '〉'), ('‘', '’')]

# 视频标题里常见的装饰词(不是歌名的一部分)
_NOISE = re.compile(
    r'official\s*(?:music\s*)?(?:video|audio|mv|pv|lyric video)?|music\s*video|lyric(?:s)?\s*video|'
    r'\bmv\b|\bpv\b|\bhd\b|\bhq\b|\b4k\b|\b1080p\b|\bhi-?res\b|\bflac\b|\blyrics?\b|full\s*ver(?:sion)?\.?|'
    r'中字|中日字幕|中文字幕|双语字幕|歌词版?|动态歌词|高音质|无损音质?|无损|完整版|官方|公式|'
    r'百万级录音棚|录音棚', re.I)

# 这些视频不是原曲:用原曲的专辑封面反而是错的,直接用视频封面
_SKIP = re.compile(
    r'翻唱|翻弹|cover|歌ってみた|弾いてみた|叩いてみた|演奏してみた|踊ってみた|钢琴|鋼琴|piano|吉他|guitar|'
    r'伴奏|karaoke|カラオケ|\blive\b|现场|現場|演唱会|演唱會|concert|串烧|串燒|medley|合集|nightcore|'
    r'reaction|教学|教學|tutorial|一小时|1小时|10小时|1\s*hour|循环|循環|纯享|純享', re.I)

# 曲库结果里的这些标注说明是另一个版本(翻唱、伴奏、钢琴版…),除非视频本身也是
_VARIANT = re.compile(
    r'live|现场|演唱会|cover|翻唱|翻自|原唱|remix|inst\b|instrumental|off vocal|伴奏|karaoke|カラオケ|'
    r'piano|钢琴|guitar|吉他|acoustic|女声|男声|\bdj\b|dj版|demo|试听|片段|sped up|speed up|slowed|'
    r'\b8d\b|music box|八音盒|オルゴール|纯音乐|版\)|\bmix\b|extended|rework|bootleg|mashup', re.I)

_SEP_DASH = re.compile(r'\s+[-–—~]\s+')
_SEP_SLASH = re.compile(r'\s*/\s*|\s*\|\s*')


class Candidate:
    __slots__ = ('track', 'artist')

    def __init__(self, track, artist=''):
        self.track = track.strip()
        self.artist = (artist or '').strip()

    def __eq__(self, other):
        return (key(self.track), key(self.artist)) == (key(other.track), key(other.artist))

    def __repr__(self):
        return f"Candidate({self.track!r}, {self.artist!r})"


def should_skip(title, duration=0):
    if duration and duration > MAX_LOOKUP_DURATION:
        return True
    return bool(_SKIP.search(unicodedata.normalize('NFKC', title or '')))


def _clean(text):
    text = _BRACKET_BLOCK.sub(' ', text)
    text = _NOISE.sub(' ', text)
    return re.sub(r'\s+', ' ', text).strip(' -–—~|/:：·・,，')


def parse_title(title):
    """从视频标题里猜歌名(和可能的歌手),按可信度排序。"""
    t = unicodedata.normalize('NFKC', title or '')
    t = t.replace('|', ' | ')
    cands = []

    # 1. 书名号 / 引号里的内容几乎总是歌名;引号前紧挨着的短文本可能是歌手
    for open_, close in _QUOTES:
        for m in re.finditer(re.escape(open_) + r'([^' + re.escape(close) + r']{1,80})' + re.escape(close), t):
            track = _clean(m.group(1)) or m.group(1).strip()
            before = _clean(_BRACKET_BLOCK.sub(' ', t[:m.start()]))
            before = re.split(r'[\s|/:：]+', before)[-1] if before else ''
            cands.append(Candidate(track))
            if before and len(before) <= 24 and len(before) >= (3 if before.isascii() else 2):
                cands.append(Candidate(track, before))

    # 2. 去掉【】[]()标签和装饰词后,按分隔符拆 "歌手 - 歌名" / "歌名 / 歌手"
    #    (有书名号时其余部分基本是描述,不再拆)
    rest = '' if cands else _clean(t)
    if rest:
        parts = [p.strip() for p in _SEP_DASH.split(rest) if p.strip()]
        if len(parts) >= 2:
            cands += [Candidate(parts[1], parts[0]), Candidate(parts[0], parts[1])]
        else:
            parts = [p.strip() for p in _SEP_SLASH.split(rest) if p.strip()]
            if len(parts) >= 2:
                cands += [Candidate(parts[0], parts[1]), Candidate(parts[1], parts[0])]
            else:
                halves = rest.split(' ')
                if len(halves) == 2 and re.search(r'[\u3040-\u30ff\u4e00-\u9fff]', rest):
                    # "周杰伦 晴天":中日文标题常用一个空格隔开歌手和歌名
                    cands += [Candidate(halves[1], halves[0]), Candidate(halves[0], halves[1])]
                cands.append(Candidate(rest))

    out = []
    for c in cands:
        if key(c.track) and c not in out:
            out.append(c)
    return out


# ---- 匹配校验 --------------------------------------------------------------------

def _artist_names(artists):
    """"A & B" "A, B feat. C" "纪钧瀚 (Bryan Chi)" 拆成单个名字。"""
    names = []
    for a in artists or []:
        for part in re.split(r'\s*(?:&|,|、|/|;|\bfeat\.?|\bft\.?|\bwith\b|×|\sx\s)\s*', a or '', flags=re.I):
            part = part.strip()
            if not part:
                continue
            names.append(part)
            names += [p.strip() for p in re.split(r'[()（）]', part) if p.strip() and p.strip() != part]
    return names


def artist_in(artists, haystack_words):
    """歌手名(任意一位)是否出现在标题/简介/标签里。英文按整词,中日文按子串。"""
    for name in _artist_names(artists):
        w = words(name)
        stripped = w.strip()
        if not stripped:
            continue
        if stripped.isascii():
            if len(stripped.replace(' ', '')) >= 2 and w in haystack_words:
                return True
        elif len(stripped.replace(' ', '')) >= 2 and stripped in haystack_words:
            return True
    return False


def is_variant(result_title, video_title):
    m = _VARIANT.search(_base(result_title))
    return bool(m) and not re.search(re.escape(m.group(0)), _base(video_title))


def choose_match(results, cand, haystack, duration, video_title):
    """results: [{title, artists, album, duration, image, ...}]。返回可信的那条或 None。"""
    want = key(cand.track)
    # 歌名本身不能当成歌手的证据(歌手叫 "Lemon" 的乐队唱的也叫 Lemon)
    hay = haystack.replace(' ' + words(cand.track).strip() + ' ', ' ')
    exact = [r for r in results
             if key(r.get('title')) == want and not is_variant(r.get('title', ''), video_title)]
    strong = [r for r in exact if artist_in(r.get('artists'), hay)]
    if strong:
        # 曲库自己的排序大体是热度:优先有专辑名、时长几乎一致的,其次按原顺序
        def rank(r):
            close = bool(duration and r.get('duration') and abs(r['duration'] - duration) <= 3)
            extra = bool(_BRACKET_BLOCK.search(_base(r.get('title'))))  # "(Remastered)" 之类
            return (not r.get('album'), not close, extra)
        return sorted(strong, key=rank)[0]
    if duration:
        near = [r for r in exact
                if r.get('duration') and abs(r['duration'] - duration) <= DURATION_TOLERANCE]
        singers = {tuple(sorted(key(a) for a in r.get('artists') or [])) for r in near}
        if len(singers) == 1:
            return near[0]
    return None


# ---- 曲库 ------------------------------------------------------------------------

_HEADERS = {'User-Agent': 'Mozilla/5.0'}
_last_call = {}
_blocked_until = {}


def _throttle(name, gap):
    wait = _last_call.get(name, 0) + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[name] = time.monotonic()


def _get_json(name, url, gap, **kwargs):
    import requests
    if time.monotonic() < _blocked_until.get(name, 0):
        return None
    _throttle(name, gap)
    try:
        r = requests.get(url, headers=kwargs.pop('headers', _HEADERS), timeout=8, **kwargs)
        if r.status_code in (403, 429) or r.status_code >= 500:
            _blocked_until[name] = time.monotonic() + 120
            return None
        return r.json()
    except ValueError:
        # iTunes 被限流时回 200 + 空内容
        _blocked_until[name] = time.monotonic() + 120
        return None
    except Exception as e:
        log.debug("artwork: %s request failed: %s", name, e)
        return None


_NETEASE_HEADERS = {'Referer': 'https://music.163.com/', 'User-Agent': 'Mozilla/5.0'}


def search_netease(query):
    data = _get_json('netease', 'https://music.163.com/api/search/get', 0.5,
                     params={'s': query, 'type': 1, 'limit': 10}, headers=_NETEASE_HEADERS)
    songs = ((data or {}).get('result') or {}).get('songs') or []
    return [{
        'title': s.get('name') or '',
        'artists': [a.get('name') or '' for a in s.get('artists') or []],
        'album': (s.get('album') or {}).get('name') or '',
        'duration': (s.get('duration') or 0) / 1000,
        'image': None,  # 搜索结果不带封面地址,选中后再查详情
        'netease_id': s.get('id'),
        'source': 'netease',
    } for s in songs]


def _netease_cover(song_id):
    import json
    data = _get_json('netease', 'https://music.163.com/api/v3/song/detail', 0.5,
                     params={'c': json.dumps([{'id': song_id}])}, headers=_NETEASE_HEADERS)
    songs = (data or {}).get('songs') or []
    pic = ((songs[0].get('al') or {}).get('picUrl') if songs else None) or ''
    return f"{pic}?param={ALBUM_SIZE}y{ALBUM_SIZE}" if pic else None


def search_itunes(query):
    country = (var.config.get('bot', 'artwork_itunes_country', fallback='JP') if var.config else 'JP') or 'JP'
    data = _get_json('itunes', 'https://itunes.apple.com/search', 3.5,
                     params={'term': query, 'media': 'music', 'entity': 'song', 'limit': 10,
                             'country': country})
    return [{
        'title': r.get('trackName') or '',
        'artists': [r.get('artistName') or ''],
        'album': r.get('collectionName') or '',
        'duration': (r.get('trackTimeMillis') or 0) / 1000,
        'image': (r.get('artworkUrl100') or '').replace('100x100bb', f'{ALBUM_SIZE}x{ALBUM_SIZE}bb') or None,
        'source': 'itunes',
    } for r in (data or {}).get('results') or []]


def search_deezer(query):
    data = _get_json('deezer', 'https://api.deezer.com/search', 0.3, params={'q': query, 'limit': 10})
    return [{
        'title': r.get('title') or '',
        'artists': [(r.get('artist') or {}).get('name') or ''],
        'album': (r.get('album') or {}).get('title') or '',
        'duration': r.get('duration') or 0,
        'image': (r.get('album') or {}).get('cover_xl'),
        'source': 'deezer',
    } for r in (data or {}).get('data') or [] if isinstance(r, dict)]


# 网易云:中日文歌名是原文、收录多;iTunes:有版权的华语/日语正版;Deezer:欧美曲库全、不怎么限流。
# 英文歌名先查 Deezer / iTunes(网易云的欧美歌经常排在前面的是合辑)
PROVIDERS = [search_netease, search_itunes, search_deezer]
PROVIDERS_LATIN = [search_deezer, search_itunes, search_netease]

_COMPILATION = re.compile(
    r'greatest hits|best of|\bhits\b|\bvol\.?\s*\d|collection|anthology|now that|various|精选|精選|合辑|合集|'
    r'群星|金曲|热歌|榜|经典|ベスト', re.I)


def is_compilation(match):
    return bool(_COMPILATION.search(match.get('album') or '')) or \
        any(_COMPILATION.search(a or '') for a in match.get('artists') or [])


def find_album(meta, providers=None):
    """meta: title, duration, uploader, tags, description, track, artist。
    返回 {title, artists, album, image, source} 或 None。providers 可注入(测试用)。"""
    title = meta.get('title') or ''
    duration = meta.get('duration') or 0
    cands = []
    if meta.get('track'):
        # YouTube Music 自动生成的视频带官方的歌名/歌手,最可信
        cands.append(Candidate(meta['track'], meta.get('artist') or ''))
    elif should_skip(title, duration):
        return None
    for c in parse_title(title):
        if c not in cands:
            cands.append(c)
    if not cands:
        return None

    haystack = words(' '.join([
        title, meta.get('uploader') or '', meta.get('artist') or '',
        ' '.join(meta.get('tags') or []), (meta.get('description') or '')[:800],
    ]))
    calls = itertools.count()
    queries_done = set()
    fallback = None  # 合辑封面:实在找不到原专辑时才用
    for cand in cands[:4]:
        order = providers or (PROVIDERS_LATIN if cand.track.isascii() else PROVIDERS)
        queries = [cand.track] + ([f"{cand.artist} {cand.track}"] if cand.artist else [])
        for q in queries:
            if q.lower() in queries_done:
                continue
            queries_done.add(q.lower())
            for provider in order:
                if next(calls) >= MAX_CALLS_PER_ITEM:
                    return _with_image(fallback)
                try:
                    results = provider(q) or []
                except Exception:
                    log.debug("artwork: provider failed", exc_info=True)
                    continue
                match = choose_match(results, cand, haystack, duration, title)
                if not match:
                    continue
                if is_compilation(match):
                    fallback = fallback or match
                    continue
                if _with_image(match):
                    return match
    return _with_image(fallback)


def _with_image(match):
    if match and not match.get('image') and match.get('netease_id'):
        match['image'] = _netease_cover(match['netease_id'])
    return match if match and match.get('image') else None


# ---- 下载 ------------------------------------------------------------------------

def _fetch_image(url):
    import requests
    from PIL import Image
    headers = dict(_HEADERS)
    if 'hdslb.com' in url or 'bilibili' in url:
        headers['Referer'] = 'https://www.bilibili.com/'
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    im = Image.open(BytesIO(r.content))
    im.load()
    return im


def _video_thumbnail_urls(info, item):
    urls = []
    if info.get('thumbnail'):
        urls.append(info['thumbnail'])
    thumbs = [t for t in info.get('thumbnails') or [] if t.get('url')]
    thumbs.sort(key=lambda t: (t.get('preference') or 0, (t.get('width') or 0) * (t.get('height') or 0)),
                reverse=True)
    urls += [t['url'] for t in thumbs]
    m = re.search(r'(?:v=|youtu\.be/|shorts/)([\w-]{11})', getattr(item, 'url', '') or '')
    if m:
        urls += [f'https://i.ytimg.com/vi/{m.group(1)}/maxresdefault.jpg',
                 f'https://i.ytimg.com/vi/{m.group(1)}/hqdefault.jpg']
    return list(dict.fromkeys(u for u in urls if u.startswith('http')))[:4]


def _extract_info(url):
    """老条目(缓存里已经下好、没走校验)没有 yt-dlp 信息:只为拿视频封面读一次。"""
    import yt_dlp
    opts = {'quiet': True, 'no_warnings': True, 'skip_download': True, 'noplaylist': True}
    cookie = var.config.get('youtube_dl', 'cookie_file', fallback='') if var.config else ''
    if cookie:
        opts['cookiefile'] = cookie
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False, process=False) or {}


# ---- 后台任务 --------------------------------------------------------------------

_INFO_KEYS = ('title', 'duration', 'uploader', 'channel', 'tags', 'description', 'track', 'artist',
              'artists', 'creator', 'album', 'thumbnail', 'thumbnails')

_queue = queue.PriorityQueue()
_seq = itertools.count()
_queued = set()
_active = set()
_infos = {}
_lock = threading.Lock()
_worker = None


def lookup_enabled():
    return var.config.getboolean('bot', 'artwork_lookup', fallback=True) if var.config else True


def ensure(item, info=None, urgent=False):
    """需要时在后台给条目准备高清封面。可以随便多次调用:已有/排队中的直接返回。
    urgent:有人正在看(播放页请求了高清图),插到队首。"""
    if item is None or getattr(item, 'type', '') not in URL_TYPES + ('file',):
        return
    state = getattr(item, 'artwork', '')
    if state in DONE_STATES and has_hd(item.id):
        return
    if state == 'none':
        return
    with _lock:
        if info:
            _infos[item.id] = {k: info.get(k) for k in _INFO_KEYS if info.get(k) is not None}
        if item.id in _active or (item.id in _queued and not urgent):
            return
        _queued.add(item.id)
        _queue.put((0 if urgent else 1, next(_seq), item))


def start():
    """启动后台线程(bot 启动时调用;单测不调用,所以不会真的去联网)。"""
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name='Artwork', daemon=True)
            _worker.start()


def _run():
    while True:
        _, _, item = _queue.get()
        with _lock:
            if item.id not in _queued:
                continue  # 同一条目 urgent 时又排了一次,已经处理过
            _queued.discard(item.id)
            _active.add(item.id)
            info = _infos.pop(item.id, None)
        try:
            resolve(item, info)
            _persist(item)
        except Exception:
            log.warning("artwork: failed for %s", getattr(item, 'id', '?'), exc_info=True)
        finally:
            with _lock:
                _active.discard(item.id)


def _persist(item):
    """后台改完直接存库:wrapper 只在校验/下载时同步版本,封面常常在那之后才好。
    条目已经不在缓存里(被删了)就不写,免得把删掉的记录又写回去。"""
    cache = getattr(var, 'cache', None)
    if cache is not None and getattr(cache, 'get', lambda _id: None)(item.id) is item:
        cache.save_item(item)


def _apply(item, im, state, match=None):
    item.thumbnail = save(item.id, im)
    item.artwork = state
    if match:
        item.artist = ', '.join(a for a in match.get('artists') or [] if a)
        item.album = match.get('album') or ''
    item.version += 1  # 通知 wrapper 存库
    log.info("artwork: %s cover for %s%s", state, item.format_title(),
             f" ({match['source']}: {', '.join(match['artists'])} - {match['title']})" if match else '')


def resolve(item, info=None, providers=None):
    if item.type == 'file':
        return _resolve_file(item, providers)
    info = info or {}
    meta = {
        'title': info.get('title') or item.title,
        'duration': info.get('duration') or item.duration,
        'uploader': info.get('uploader') or info.get('channel') or '',
        'tags': info.get('tags') or [],
        'description': info.get('description') or '',
        'track': info.get('track') or '',
        'artist': info.get('artist') or ', '.join(info.get('artists') or []) or info.get('creator') or '',
    }
    # "xxx - Topic" 是 YouTube 自动生成的歌手频道
    meta['uploader'] = re.sub(r'\s*-\s*topic$|vevo$', '', meta['uploader'], flags=re.I)

    if lookup_enabled() and meta['title']:
        match = find_album(meta, providers)
        if match:
            try:
                return _apply(item, _fetch_image(match['image']), 'album', match)
            except Exception:
                log.debug("artwork: album image download failed", exc_info=True)

    # 退回视频封面
    tmp_cover = os.path.join(var.tmp_folder or '', item.id + '.jpg')
    if not info and os.path.isfile(tmp_cover):
        from PIL import Image
        try:
            with Image.open(tmp_cover) as im:
                im.load()
                return _apply(item, im, 'video')
        except Exception:
            pass
    if not info.get('thumbnail') and not info.get('thumbnails') and getattr(item, 'url', ''):
        try:
            info = dict(_extract_info(item.url), **info)
        except Exception:
            log.debug("artwork: could not read info for %s", item.url, exc_info=True)
    for url in _video_thumbnail_urls(info, item):
        try:
            return _apply(item, _fetch_image(url), 'video')
        except Exception:
            continue
    item.artwork = 'none'
    item.version += 1


def _resolve_file(item, providers=None):
    im = item.cover_image()
    if im is not None:
        return _apply(item, im, 'embedded')
    if lookup_enabled() and item.title and item.artist:
        meta = {'title': item.title, 'duration': item.duration, 'track': item.title, 'artist': item.artist}
        match = find_album(meta, providers)
        if match:
            try:
                return _apply(item, _fetch_image(match['image']), 'album', dict(match, artists=[item.artist]))
            except Exception:
                pass
    item.artwork = 'none'
    item.version += 1
