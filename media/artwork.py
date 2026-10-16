"""高清封面。

播放页的封面框是 288px(高分屏要 ~600px),而条目里存的 thumbnail 只有 100px
(给 Mumble 聊天消息和曲库列表用),直接拉伸就是糊的。这里另存一份高清图:

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
DURATION_TOLERANCE = 1.5   # 只靠时长确认时允许的误差(秒)
MAX_CALLS_PER_ITEM = 10    # 每首歌最多查几次曲库

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


def full_key(text):
    """同 key(),但保留括号里的内容:"Right Now (Na Na Na)" 的括号是歌名的一部分,
    "I Knew You Were Trouble (Taylor's Version)" 要和原版区分开。"""
    t = _base(text)
    t = re.sub(r'\s+[-–—]\s+.*$', '', t)
    t = _FEAT.sub('', t)
    t = re.sub(r'[(\[](?:feat|ft)\.?\s[^)\]]*[)\]]', ' ', t)
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
    r'\b8d\b|music box|八音盒|オルゴール|纯音乐|版\)|\bmix\b|extended|rework|bootleg|mashup|reimagined|'
    r're-?recorded|recorded at|\bsessions?\b|unplugged|stripped|orchestral|symphonic|lullaby|8-?bit|'
    r'\bcovers?\b|tribute|\bvs\.?\b', re.I)

# "t.A.T.u.- Loves me not":横杠一边有空格就算分隔符("A-ha" 这种两边都没空格的不拆)
_SEP_DASH = re.compile(r'\s+[-–—~]\s*|\s*[-–—~]\s+')

# 曲库结果的歌手/专辑名里出现这些,是卡拉 OK / 模仿版厂牌(歌手名里的 DJ 之类不算)
_FAKE = re.compile(r'karaoke|カラオケ|in the style of|made famous|originally performed|tribute|'
                   r'sound-?alike|翻唱|翻自|伴奏', re.I)
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


_TAG_BLOCK = re.compile(r'\[[^\[\]]*\]|【[^【】]*】|〔[^〔〕]*〕|\{[^{}]*\}')
_PAREN_BLOCK = re.compile(r'\(([^()]*)\)')
_PAREN_NOISE = re.compile(r'video|audio|visuali[sz]er|lyric|remaster|\bfrom\b|\bost\b|主题曲|片尾|片头|エンディング|'
                          r'オープニング|主題歌', re.I)


def _clean(text):
    # 【】[] 基本都是标签;() 里只有装饰词才去掉,"(Taylor's Version)" "(Na Na Na)" 留着
    text = _TAG_BLOCK.sub(' ', text)
    text = _PAREN_BLOCK.sub(lambda m: ' ' if _PAREN_NOISE.search(m.group(1)) or _NOISE.search(m.group(1))
                            else m.group(0), text)
    text = _NOISE.sub(' ', text)
    text = re.sub(r'\(\s*\)', ' ', text)
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


def is_variant(result, video_title):
    """result 是曲库结果(dict)或歌名。歌名/专辑名带版本标注、歌手是卡拉 OK 厂牌,都算另一个版本。"""
    if isinstance(result, str):
        result = {'title': result}
    video = _base(video_title)
    for text in (result.get('title'), result.get('album')):
        m = _VARIANT.search(_base(text))
        if m and m.group(0) not in video:
            return True
    for text in [result.get('album')] + list(result.get('artists') or []):
        m = _FAKE.search(_base(text))
        if m and m.group(0) not in video:
            return True
    return False


def choose_match(results, cand, haystack, duration, video_title):
    """results: [{title, artists, album, duration, image, ...}]。返回可信的那条或 None。"""
    want, want_full = key(cand.track), full_key(cand.track)
    # 歌名本身不能当成歌手的证据(歌手叫 "Lemon" 的乐队唱的也叫 Lemon)
    hay = haystack.replace(' ' + words(cand.track).strip() + ' ', ' ')
    exact = [r for r in results
             if want in (key(r.get('title')), full_key(r.get('title'))) or want_full == full_key(r.get('title'))]
    exact = [r for r in exact if not is_variant(r, video_title)]
    strong = [r for r in exact if artist_in(r.get('artists'), hay)]
    if strong:
        # 曲库自己的排序大体是热度:优先括号也对得上的("Taylor's Version" 不配原版),
        # 再优先有专辑名、时长几乎一致的,其次按原顺序
        def rank(r):
            close = bool(duration and r.get('duration') and abs(r['duration'] - duration) <= 3)
            extra = bool(_BRACKET_BLOCK.search(_base(r.get('title'))))  # "(Remastered)" 之类
            return (full_key(r.get('title')) != want_full, not r.get('album'), not close, extra)
        return sorted(strong, key=rank)[0]
    if duration:
        def conflicts(r):
            # 标题里写明了歌手,结果的歌手和它是同一种文字却对不上 → 是别人的同名歌
            # ("Charlie Puth - We Don't Talk Anymore" 不能配 Pablo Cepeda)。
            # 文字不同(周杰伦 / Jay Chou)才可能是曲库本地化,交给时长判断
            return bool(cand.artist) and cand.artist.isascii() == ' '.join(r.get('artists') or []).isascii()
        near = [r for r in exact
                if r.get('duration') and abs(r['duration'] - duration) <= DURATION_TOLERANCE
                and full_key(r.get('title')) == want_full and not conflicts(r)]
        singers = {tuple(sorted(key(a) for a in r.get('artists') or [])) for r in near}
        if len(singers) == 1:
            return near[0]
    return None


def music_candidate(cards, title, haystack):
    """YouTube「Music」卡片(recognize.youtube_music)→ Candidate 或 None。
    卡片是 Content ID 的认领结果,不全可信。实测:MV 配成别人的同名歌("El Amante" → Thiago Matheus
    的 "O Amante")、配成合唱/Live 版("Perfect" → "Perfect Duet (with Beyoncé)")、歌词视频列一串
    不相干的歌、卡片歌名就是视频标题。所以:
      - 只有一张卡片才用(多张是串烧/合集或 BGM 列表);
      - 卡片上的歌手要在标题/简介/标签/UP 主里出现过;
      - 标题里能拆出同一种文字的歌名时,要和卡片歌名对得上(一个包含另一个);
        文字不同("Fireworks" / 打上花火)可能是译名,信卡片;
      - 对得上时用两者里短的那个:多出来的部分是版本标注,播放的是视频里那一版。"""
    if len(cards) != 1:
        return None
    track, artist = (cards[0].get('title') or '').strip(), (cards[0].get('artist') or '').strip()
    want = key(track)
    if not want or not artist or words(artist) in words(track):  # "YOASOBI - 群青 / THE FIRST TAKE"
        return None
    if not artist_in([artist], haystack):
        return None
    same_script = False
    for c in parse_title(title):
        k = key(c.track)
        if not k:
            continue
        if want in k or (k in want and len(k) >= (3 if k.isascii() else 2)):
            return Candidate(min(track, c.track, key=len), artist)
        same_script = same_script or k.isascii() == want.isascii()
    return None if same_script else Candidate(track, artist)


# ---- 曲库 ------------------------------------------------------------------------

_HEADERS = {'User-Agent': 'Mozilla/5.0'}
_last_call = {}
_blocked_until = {}


def _throttle(name, gap):
    wait = _last_call.get(name, 0) + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[name] = time.monotonic()


def _get_json(name, url, gap, method='GET', **kwargs):
    import requests
    if time.monotonic() < _blocked_until.get(name, 0):
        return None
    _throttle(name, gap)
    try:
        r = requests.request(method, url, headers=kwargs.pop('headers', _HEADERS), timeout=8, **kwargs)
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


def _language(query):
    """按查询文字的语种决定曲库返回哪种语言的名字。不指定时 Deezer 按 IP 所在地本地化,
    在日本会把 "Bon Jovi - It's My Life" 变成「ボン・ジョヴィ - イッツ・マイ・ライフ」,歌名就对不上了。"""
    if re.search(r'[\u3040-\u30ff]', query):
        return 'ja'
    if re.search(r'[\uac00-\ud7af]', query):
        return 'ko'
    if re.search(r'[\u4e00-\u9fff]', query):
        return 'zh'
    return 'en'


def search_itunes(query):
    country = (var.config.get('bot', 'artwork_itunes_country', fallback='JP') if var.config else 'JP') or 'JP'
    params = {'term': query, 'media': 'music', 'entity': 'song', 'limit': 10, 'country': country}
    if _language(query) == 'en':
        # 英文歌查美区:JP 商店会把欧美歌手名翻成片假名
        params.update(country='US', lang='en_us')
    data = _get_json('itunes', 'https://itunes.apple.com/search', 3.5, params=params)
    return [{
        'title': r.get('trackName') or '',
        'artists': [r.get('artistName') or ''],
        'album': r.get('collectionName') or '',
        'duration': (r.get('trackTimeMillis') or 0) / 1000,
        'image': (r.get('artworkUrl100') or '').replace('100x100bb', f'{ALBUM_SIZE}x{ALBUM_SIZE}bb') or None,
        'source': 'itunes',
    } for r in (data or {}).get('results') or []]


def search_deezer(query):
    data = _get_json('deezer', 'https://api.deezer.com/search', 0.3, params={'q': query, 'limit': 10},
                     headers=dict(_HEADERS, **{'Accept-Language': _language(query)}))
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
    r'群星|金曲|热歌|榜|经典|ベスト|\bparty\b|workout|fitness|running|\btop\s*\d|\bcharts?\b|ultimate|'
    r'essentials?\b|playlist|throwback|wedding|\bkids\b', re.I)


def is_compilation(match):
    return bool(_COMPILATION.search(match.get('album') or '')) or \
        any(_COMPILATION.search(a or '') for a in match.get('artists') or [])


def _haystack(meta):
    """确认歌手用的文字:标题、UP 主、平台给的歌手、标签、简介开头。"""
    return words(' '.join([
        meta.get('title') or '', meta.get('uploader') or '', meta.get('artist') or '',
        ' '.join(meta.get('tags') or []), (meta.get('description') or '')[:800],
    ]))


def find_album(meta, providers=None, guess=True):
    """meta: title, duration, uploader, tags, description, track, artist, music。
    music 是校验过的 YouTube「Music」卡片(music_candidate)。guess=False 时不再从视频标题猜歌名
    (听歌识曲已经认出了是哪首)。返回 {title, artists, album, image, source} 或 None。
    providers 可注入(测试用)。"""
    title = meta.get('title') or ''
    duration = meta.get('duration') or 0
    cands = []
    if meta.get('track'):
        # YouTube Music 自动生成的视频带官方的歌名/歌手,最可信
        cands.append(Candidate(meta['track'], meta.get('artist') or ''))
    elif should_skip(title, duration):
        return None
    elif meta.get('music'):
        cands.append(meta['music'])
    for c in parse_title(title) if guess else []:
        if c not in cands:
            cands.append(c)
    if not cands:
        return None

    haystack = _haystack(meta)
    if meta.get('music'):
        haystack += words(meta['music'].artist)
    calls = itertools.count()
    queries_done = set()
    fallback = None  # 合辑封面:实在找不到原专辑时才用
    # 先把每个候选的歌名单独查一轮,再带上歌手查:"Passenger | Let Her Go" 拆不出谁是歌手,
    # 不能让反过来的那个候选("Passenger" 当歌名)先把查询次数用光
    plan = [(c, c.track) for c in cands[:4]] + [(c, f"{c.artist} {c.track}") for c in cands[:4] if c.artist]
    for cand, q in plan:
        if q.lower() in queries_done:
            continue
        queries_done.add(q.lower())
        order = providers or (PROVIDERS_LATIN if cand.track.isascii() else PROVIDERS)
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
_urgent = set()
_active = set()
_quick = set()
_infos = {}
_lock = threading.Lock()
_apply_lock = threading.Lock()
_worker = None


def lookup_enabled():
    return var.config.getboolean('bot', 'artwork_lookup', fallback=True) if var.config else True


def shazam_enabled():
    return lookup_enabled() and (var.config.getboolean('bot', 'artwork_shazam', fallback=True)
                                 if var.config else True)


def _audio_ready(item):
    path = getattr(item, 'path', '') or ''
    return getattr(item, 'ready', '') == 'yes' and not getattr(item, 'downloading', False) \
        and bool(path) and os.path.isfile(path)


def _done(item):
    """曲库已经查过(查到/没查到都算)。上一版没有 artwork_done 字段:
    专辑/内嵌封面就算查过了,视频封面的再按新规则查一次。
    更早的专辑封面条目没存正式歌名(track 是 None):再查一次补上。
    没有 shazam 字段的是加「Music」卡片和听歌识曲之前查的:没找到专辑封面的按新规则再查一次。
    等着听歌识曲('pending')的,音频下载好了就接着认。"""
    if getattr(item, 'type', '') in URL_TYPES:
        if getattr(item, 'artwork', '') == 'album' and getattr(item, 'track', '') is None:
            return False
        state = getattr(item, 'shazam', None)
        if state is None and getattr(item, 'artwork_done', False) and getattr(item, 'artwork', '') != 'album':
            return False
        if state == 'pending' and _audio_ready(item) and shazam_enabled():
            return False
    return bool(getattr(item, 'artwork_done', False)) or getattr(item, 'artwork', '') in ('album', 'embedded')


def ensure(item, info=None, urgent=False):
    """需要时给条目准备高清封面,可以随便多次调用。分两步:
      1. 视频封面:拿到 yt-dlp 信息或有人正在看时,马上另起线程下载(不排队,几百毫秒就有图);
      2. 专辑封面:排进后台队列慢慢查(曲库有限流),查到了再替换。
    urgent:正在播放 / 有人在看,插到队首。"""
    if item is None or getattr(item, 'type', '') not in URL_TYPES + ('file',):
        return
    hd = has_hd(item.id)
    if _done(item) and (hd or getattr(item, 'artwork', '') == 'none'):
        return
    with _lock:
        if info:
            _infos[item.id] = {k: info.get(k) for k in _INFO_KEYS if info.get(k) is not None}
        if not hd and item.type in URL_TYPES and (info or urgent) and item.id not in _quick:
            _quick.add(item.id)
            threading.Thread(target=_quick_video, args=(item, _infos.get(item.id)),
                             name='Thumb-' + item.id[:7], daemon=True).start()
        if item.id in _active:
            return
        if item.id in _queued and (not urgent or item.id in _urgent):
            return
        _queued.add(item.id)
        if urgent:
            _urgent.add(item.id)
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
            _urgent.discard(item.id)
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


def _quick_video(item, info):
    try:
        if not has_hd(item.id) and _video_step(item, info or {}):
            _persist(item)
    except Exception:
        log.debug("artwork: video cover failed for %s", item.id, exc_info=True)
    finally:
        with _lock:
            _quick.discard(item.id)


def _persist(item):
    """后台改完直接存库:wrapper 只在校验/下载时同步版本,封面常常在那之后才好。
    条目已经不在缓存里(被删了)就不写,免得把删掉的记录又写回去。"""
    cache = getattr(var, 'cache', None)
    if cache is not None and getattr(cache, 'get', lambda _id: None)(item.id) is item:
        cache.save_item(item)


def _apply(item, im, state, match=None):
    with _apply_lock:
        if state == 'video' and getattr(item, 'artwork', '') in ('album', 'embedded') and has_hd(item.id):
            return False  # 专辑封面已经先到了,别被视频封面盖掉
        item.thumbnail = save(item.id, im)
        item.artwork = state
        if match:
            item.artist = ', '.join(a for a in match.get('artists') or [] if a)
            item.album = match.get('album') or ''
            if item.type in URL_TYPES:
                item.track = match.get('title') or ''
        item.version += 1  # 通知 wrapper 存库
    log.info("artwork: %s cover for %s%s", state, item.format_title(),
             f" ({match['source']}: {', '.join(match['artists'])} - {match['title']})" if match else '')
    return True


def _video_step(item, info):
    """下载视频封面;成功返回 True。"""
    from PIL import Image
    tmp_cover = os.path.join(var.tmp_folder or '', item.id + '.jpg')
    if not info and os.path.isfile(tmp_cover):  # 上一版下载时存的原图
        try:
            with Image.open(tmp_cover) as im:
                im.load()
                return _apply(item, im, 'video')
        except Exception:
            pass
    urls = _video_thumbnail_urls(info, item)
    if not urls and getattr(item, 'url', ''):
        try:
            urls = _video_thumbnail_urls(_extract_info(item.url), item)
        except Exception:
            log.debug("artwork: could not read info for %s", item.url, exc_info=True)
    for url in urls:
        try:
            return _apply(item, _fetch_image(url), 'video')
        except Exception:
            continue
    return False


def resolve(item, info=None, providers=None):
    """找封面和正式歌名,依次:平台元数据 / YouTube「Music」卡片 → 按标题查曲库 → 视频封面;
    都没有专辑封面时,等音频下载好再听歌识曲(_shazam_step)。"""
    if item.type == 'file':
        return _resolve_file(item, providers)
    if getattr(item, 'artwork_done', False) and getattr(item, 'shazam', None) == 'pending':
        if _audio_ready(item):
            _shazam_step(item, providers)  # 前面几步早就做完了,只差听歌识曲
        return
    info = info or {}
    if lookup_enabled() and not info.get('uploader') and not info.get('tags') and getattr(item, 'url', ''):
        # 从上次的队列恢复的条目没有 yt-dlp 信息:读一次,UP 主 / 标签 / 简介是确认歌手的主要依据
        try:
            info = dict(_extract_info(item.url), **info)
        except Exception:
            log.debug("artwork: could not read info for %s", item.url, exc_info=True)
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

    skipped = should_skip(meta['title'], meta['duration'])
    known_album = info.get('album') or ''
    if lookup_enabled() and meta['title'] and not meta['track'] and not skipped and youtube_music_enabled():
        from media import recognize
        cards = recognize.youtube_music(getattr(item, 'url', ''))
        meta['music'] = music_candidate(cards, meta['title'], _haystack(meta))
        if meta['music']:
            # 歌名换成了标题里的(去掉了版本标注)时,卡片上的专辑不一定是这一版的
            known_album = cards[0]['album'] if meta['music'].track == cards[0]['title'] else ''
            log.info("artwork: YouTube music card for %s: %s - %s",
                     item.format_title(), meta['music'].artist, meta['music'].track)

    found = False
    if lookup_enabled() and meta['title']:
        match = find_album(meta, providers)
        if match:
            try:
                found = _apply(item, _fetch_image(match['image']), 'album', match)
            except Exception:
                log.debug("artwork: album image download failed", exc_info=True)
    if not found:
        # 曲库里没有,但平台 / Content ID 已经说了是哪首:至少把歌名记下来
        known = Candidate(meta['track'], meta['artist']) if meta['track'] else meta.get('music')
        if known and not getattr(item, 'track', ''):
            item.track, item.artist, item.album = known.track, known.artist, known_album
        if not (item.artwork == 'video' and has_hd(item.id)):
            _video_step(item, info)  # 查不到专辑封面:用视频封面
    if not has_hd(item.id):
        item.artwork = 'none'
    if getattr(item, 'track', '') is None:
        item.track = ''  # 查过了,没有正式歌名,别再为它重查
    # 标题明说是翻唱/现场/合集的不去认:认出来的是原曲,封面反而贴错
    item.shazam = 'pending' if not found and not skipped and shazam_enabled() else ''
    item.artwork_done = True
    item.version += 1
    if item.shazam == 'pending' and _audio_ready(item):
        _shazam_step(item, providers)  # 已经缓存过的歌不用等下载


def youtube_music_enabled():
    return var.config.getboolean('bot', 'artwork_youtube_music', fallback=True) if var.config else True


def _shazam_step(item, providers=None):
    """听歌识曲,认出来后先按认出的歌名查曲库(歌手名和曲库其他歌一致),查不到用 Shazam 自己的封面。"""
    from media import recognize
    item.shazam = 'done'  # 认不出来也只试一次
    item.version += 1
    try:
        found = recognize.shazam(item.path, getattr(item, 'duration', 0) or 0, getattr(item, 'url', ''))
    except recognize.ShazamUnavailable:
        item.shazam = 'pending'  # 没连上:下次校验(播放)时再试
        return False
    if not found:
        return False
    meta = {'title': item.title, 'duration': getattr(item, 'duration', 0) or 0,
            'track': found['title'], 'artist': found['artists'][0]}
    for match in (find_album(meta, providers, guess=False), found):
        if not match or not match.get('image'):
            continue
        try:
            if _apply(item, _fetch_image(match['image']), 'album', match):
                return True
        except Exception:
            log.debug("artwork: image download failed for %s", match.get('source'), exc_info=True)
    # 封面都下不来:歌名还是记下
    item.track, item.artist, item.album = found['title'], found['artists'][0], found['album']
    item.version += 1
    return False


def _resolve_file(item, providers=None):
    im = item.cover_image()
    if im is not None:
        _apply(item, im, 'embedded')
    elif lookup_enabled() and item.title and item.artist:
        meta = {'title': item.title, 'duration': item.duration, 'track': item.title, 'artist': item.artist}
        match = find_album(meta, providers)
        if match:
            try:
                _apply(item, _fetch_image(match['image']), 'album', dict(match, artists=[item.artist]))
            except Exception:
                pass
    if not has_hd(item.id):
        item.artwork = 'none'
    item.artwork_done = True
    item.version += 1
