"""把外部歌单导入成个人歌单:YouTube 播放列表、网易云音乐歌单、Spotify 歌单/专辑。

- YouTube:yt-dlp 平铺抓取,直接存视频链接。
- 网易云音乐:海外 IP(NAS 在日本)基本播不了网易云的音源,所以只读歌名/歌手/时长,
  再去 YouTube 搜同一首歌,存匹配到的 YouTube 链接。
- Spotify:没有可直接下载的音源,同样读元数据后匹配 YouTube(列表用现有的 spotdl 集成读取,
  需要配置 [spotify] client_id/client_secret)。

导入在后台线程里跑(几百首的匹配要一两分钟,超过 Cloudflare 的请求超时),前端轮询进度。
匹配结果存成普通 url 条目,之后播放、缓存、SponsorBlock 都走现有逻辑。
"""

import json
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

import variables as var

log = logging.getLogger("bot")

MATCH_WORKERS = 4
NETEASE_HEADERS = {'Referer': 'https://music.163.com/', 'User-Agent': 'Mozilla/5.0'}
_UNAVAILABLE_TITLES = {'[Deleted video]', '[Private video]'}
# 搜索结果标题里有、原曲标题里没有的这些词,多半不是原曲
_BAD_WORDS = ('live', 'cover', 'remix', 'karaoke', 'instrumental', 'nightcore', 'sped up', 'slowed',
              '8d', 'reaction', 'lyrics video', 'tutorial', 'piano', 'acoustic', '翻唱', '伴奏',
              'カバー', '弾いてみた', '歌ってみた', 'off vocal', 'amv', 'fmv', 'mmd', 'fanmade',
              'fan made', 'mashup', '8 bit', '8bit', '1 hour', '1小时', 'loop', 'tiktok')

_jobs = {}
_jobs_lock = threading.Lock()


# ---- 来源识别 ------------------------------------------------------------------

def detect_source(url):
    host = (urlparse(url).hostname or '').lower()
    if host.endswith('youtube.com') or host == 'youtu.be':
        return 'youtube' if 'list=' in url else None
    if host.endswith('music.163.com') or host == '163cn.tv':
        return 'netease' if _netease_playlist_id(url) else None
    if host == 'open.spotify.com':
        return 'spotify' if re.search(r'/(playlist|album)/', url) else None
    return None


def _netease_playlist_id(url):
    m = re.search(r'playlist[^0-9]*?[?&]id=(\d+)', url) or re.search(r'/playlist/(\d+)', url)
    return m.group(1) if m else None


# ---- 读取各来源的曲目 ---------------------------------------------------------------
# 每个曲目:{title, artist, duration, url(有直链时才有)}

def list_youtube(url):
    import yt_dlp

    opts = {'extract_flat': 'in_playlist', 'skip_download': True, 'quiet': True,
            'no_warnings': True, 'playlistend': _max_items()}
    cookie = var.config.get('youtube_dl', 'cookie_file', fallback='')
    if cookie:
        opts['cookiefile'] = cookie
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    tracks = []
    for e in info.get('entries') or []:
        if not e:
            continue
        title = str(e.get('title') or '').strip()
        if title in _UNAVAILABLE_TITLES or e.get('availability') in ('private', 'needs_auth', 'subscriber_only'):
            continue
        link = e.get('url') or ''
        if not link.startswith('http') and e.get('id'):
            link = f"https://www.youtube.com/watch?v={e['id']}"
        if link:
            tracks.append({'title': title or link, 'artist': '', 'duration': e.get('duration') or 0,
                           'url': link})
    return str(info.get('title') or '').strip(), tracks


def list_netease(url):
    """歌单接口未登录只给前 10 首详情,但给全部曲目 id;剩下的用歌曲详情接口批量补。"""
    if urlparse(url).hostname == '163cn.tv':  # 分享短链
        url = requests.head(url, allow_redirects=True, timeout=10, headers=NETEASE_HEADERS).url
    playlist_id = _netease_playlist_id(url)
    r = requests.get('https://music.163.com/api/v6/playlist/detail', params={'id': playlist_id},
                     headers=NETEASE_HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    playlist = data.get('playlist')
    if data.get('code') != 200 or not playlist:
        raise ValueError(f"netease playlist unavailable (code {data.get('code')})")
    ids = [t['id'] for t in playlist.get('trackIds') or []][:_max_items()]
    songs = {}
    for i in range(0, len(ids), 400):
        batch = ids[i:i + 400]
        r = requests.post('https://music.163.com/api/v3/song/detail',
                          data={'c': json.dumps([{'id': x} for x in batch])},
                          headers=NETEASE_HEADERS, timeout=20)
        r.raise_for_status()
        for s in r.json().get('songs') or []:
            songs[s['id']] = s
    tracks = []
    for song_id in ids:
        s = songs.get(song_id)
        if not s:
            continue
        tracks.append({'title': s.get('name', ''), 'artist': '/'.join(a.get('name', '') for a in s.get('ar') or []),
                       'duration': (s.get('dt') or 0) / 1000.0})
    return playlist.get('name', ''), tracks


def list_spotify(url):
    import media.spotify

    tracks = media.spotify.list_spotify_tracks(url)[:_max_items()]
    return '', [{'title': t.get('name', ''), 'artist': t.get('artist', ''),
                 'duration': t.get('duration') or 0} for t in tracks]


def _max_items():
    from web_users import MAX_ITEMS_PER_PLAYLIST
    return MAX_ITEMS_PER_PLAYLIST


# ---- YouTube 匹配 -----------------------------------------------------------------

def _norm(text):
    return re.sub(r'\s+', ' ', re.sub(r'[\W_]+', ' ', (text or '').lower())).strip()


def score_candidate(track, cand):
    """分数越低越好;None 表示不可接受。"""
    title, ctitle = _norm(track['title']), _norm(cand.get('title'))
    channel = (cand.get('channel') or cand.get('uploader') or '').lower()
    score = 0.0
    want, got = track.get('duration') or 0, cand.get('duration') or 0
    if want and got:
        diff = abs(want - got)
        if diff > 90:
            return None  # 时长差一分半以上,基本不是同一个版本
        # 15 秒以内(MV 片头片尾,SponsorBlock 会跳掉)只算小差别,再往上才明显扣分
        score += diff * 0.3 if diff <= 15 else 4.5 + (diff - 15)
    else:
        score += 20
    if title and title not in ctitle:
        # 原曲名不在结果标题里:按词的重合度扣分
        words = set(title.split())
        overlap = len(words & set(ctitle.split())) / max(1, len(words))
        if overlap < 0.5:
            score += 60
        else:
            score += 20 * (1 - overlap)
    for word in _BAD_WORDS:
        if re.search(r'(?<![a-z])' + re.escape(word) + r'(?![a-z])', ctitle) and word not in title:
            score += 45
    artists = [a for a in re.split(r'[/&,、]| feat\.? ', (track.get('artist') or '').lower()) if a.strip()]
    if channel.endswith(' - topic'):
        score -= 15  # YouTube 自动生成的官方音频频道
    if 'official' in ctitle or 'official' in channel or 'vevo' in channel:
        score -= 12
    if any(a.strip() and a.strip() in channel for a in artists):
        score -= 10
    elif artists and not any(_norm(a) and _norm(a) in ctitle for a in artists):
        score += 15
    return score


def search_youtube(query, limit=6):
    import yt_dlp

    opts = {'extract_flat': True, 'skip_download': True, 'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False) or {}
    return [e for e in info.get('entries') or [] if e and e.get('id')]


def match_on_youtube(track, search=search_youtube):
    """返回 YouTube 链接,没有合适结果返回 None。search 可注入(测试用)。"""
    query = f"{track.get('artist') or ''} {track['title']}".strip()
    if not query:
        return None
    try:
        candidates = search(query)
    except Exception as e:
        log.info("import: youtube search failed for %r: %s", query, e)
        return None
    best, best_score = None, None
    for cand in candidates:
        s = score_candidate(track, cand)
        if s is not None and (best_score is None or s < best_score):
            best, best_score = cand, s
    if best is None:
        return None
    return best.get('url') if str(best.get('url', '')).startswith('http') \
        else f"https://www.youtube.com/watch?v={best['id']}"


# ---- 任务 ----------------------------------------------------------------------

def get_job(job_id):
    return _jobs.get(job_id)


def start_import(owner, url, playlist_id=None, name=None):
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        for old in [k for k, v in _jobs.items() if time.time() - v['started'] > 3600]:
            del _jobs[old]
        _jobs[job_id] = {'id': job_id, 'owner': owner, 'url': url, 'source': detect_source(url),
                         'status': 'listing', 'total': 0, 'processed': 0, 'matched': 0,
                         'started': time.time()}
    threading.Thread(target=run_import, args=(job_id, owner, url, playlist_id, name),
                     name="PlaylistImport", daemon=True).start()
    return job_id


def run_import(job_id, owner, url, playlist_id, name, search=search_youtube):
    from web_users import NAME_MAX_LEN, entry_from_url

    job = _jobs[job_id]
    source = job['source']
    try:
        if source == 'youtube':
            title, tracks = list_youtube(url)
        elif source == 'netease':
            title, tracks = list_netease(url)
        elif source == 'spotify':
            title, tracks = list_spotify(url)
        else:
            raise ValueError('unsupported source')
    except Exception as e:
        log.warning("import: listing %s failed: %s", url, e)
        error = 'spotify_not_configured' if 'not configured' in str(e) else 'list_failed'
        job.update(status='error', error=error, detail=str(e)[-300:])
        return
    if not tracks:
        job.update(status='error', error='no_entries')
        return
    job.update(status='matching', total=len(tracks), source_title=title)

    counter_lock = threading.Lock()

    def label(track):
        text = f"{track['artist']} - {track['title']}" if track.get('artist') else track['title']
        return text[:300]

    def resolve(track):
        link = track.get('url') or match_on_youtube(track, search)
        with counter_lock:
            job['processed'] += 1
            if link:
                job['matched'] += 1
        return entry_from_url(link, label(track), track.get('duration') or 0) if link else None

    if source == 'youtube':
        results = [resolve(t) for t in tracks]
    else:
        with ThreadPoolExecutor(max_workers=MATCH_WORKERS) as pool:
            results = list(pool.map(resolve, tracks))  # map 保持原歌单顺序
    entries = [e for e in results if e]
    unmatched = [label(t) for t, e in zip(tracks, results) if e is None][:50]
    try:
        if playlist_id is None:
            playlist_id = var.user_db.create_playlist(owner, (name or title or 'Imported')[:NAME_MAX_LEN])
        added = var.user_db.add_items(owner, playlist_id, entries)
    except ValueError:
        job.update(status='error', error='too_many_playlists')
        return
    if added is None:
        job.update(status='error', error='playlist_not_found')
        return
    job.update(status='done', playlist_id=playlist_id, added=added, unmatched=unmatched)
    log.info("import: %s imported %d/%d from %s (%s)", owner, added, len(tracks), url, source)
