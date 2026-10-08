"""SponsorBlock:播放时跳过视频里的非音乐片段(MV 片头片尾的说话、赞助口播等)。

数据来源:
  YouTube   https://sponsor.ajay.app      (官方 SponsorBlock)
  Bilibili  https://bsbsb.top             (BilibiliSponsorBlock,接口与官方兼容,按 BV 号查)

用哈希前缀接口 /api/skipSegments/<sha256 前 4 位>:服务器只看到 4 位前缀,
不知道具体在听哪个视频(两边的文档都推荐这种方式)。

不改下载文件,而是在播放时遇到片段就让 ffmpeg 从片段结尾重新开始(输入端 seek
很快),所以已经缓存的歌、边下边播的歌都能用,也不需要重新下载。
"""

import hashlib
import json
import logging
import re
import threading
import time
from urllib.parse import parse_qs, urlparse

import requests

log = logging.getLogger("bot")

SERVERS = {
    'youtube': 'https://sponsor.ajay.app',
    'bilibili': 'https://bsbsb.top',
}
CACHE_TTL = 24 * 3600
TIMEOUT = 6
MIN_SEGMENT = 1.0  # 太短的片段跳了反而更突兀

_YT_RE = re.compile(r'(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})')
_BV_RE = re.compile(r'BV[0-9A-Za-z]{10}')
_AV_RE = re.compile(r'/av(\d+)', re.IGNORECASE)

_cache = {}
_lock = threading.Lock()

# B 站 av/BV 互转(与 util.bv_to_av 是同一套算法的反向)
_XOR = 23442827791579
_MAX_AID = 1 << 51
_ALPHABET = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"


def av_to_bv(aid):
    chars = list('BV1000000000')
    idx = len(chars) - 1
    tmp = (_MAX_AID | int(aid)) ^ _XOR
    while tmp:
        chars[idx] = _ALPHABET[tmp % 58]
        tmp //= 58
        idx -= 1
    chars[3], chars[9] = chars[9], chars[3]
    chars[4], chars[7] = chars[7], chars[4]
    return ''.join(chars)


def video_ref(url):
    """(service, video_id, part) 或 None。part 是 B 站分 P 序号(没有则 None)。"""
    if not url:
        return None
    host = (urlparse(url).hostname or '').lower()
    if host.endswith('youtube.com') or host.endswith('youtu.be') or host.endswith('youtube-nocookie.com'):
        m = _YT_RE.search(url)
        return ('youtube', m.group(1), None) if m else None
    if host.endswith('bilibili.com'):
        part = parse_qs(urlparse(url).query).get('p', [None])[0]
        m = _BV_RE.search(url)
        if m:
            return ('bilibili', m.group(0), part)
        m = _AV_RE.search(url)
        if m:
            return ('bilibili', av_to_bv(m.group(1)), part)
    return None


def merge_segments(segments):
    """排序并合并重叠/相邻片段,丢掉过短的。"""
    merged = []
    for start, end in sorted(segments):
        if end - start < MIN_SEGMENT:
            continue
        if merged and start <= merged[-1][1] + 0.5:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(s) for s in merged]


def _query(service, video_id, categories):
    prefix = hashlib.sha256(video_id.encode()).hexdigest()[:4]
    url = f"{SERVERS[service]}/api/skipSegments/{prefix}"
    params = {'categories': json.dumps(list(categories)), 'actionTypes': json.dumps(['skip'])}
    resp = requests.get(url, params=params, timeout=TIMEOUT,
                        headers={'User-Agent': 'Mumble-DJBot (sponsorblock)'})
    if resp.status_code == 404:
        return []  # 没人标注过
    resp.raise_for_status()
    for video in resp.json():
        if video.get('videoID') == video_id:
            return video.get('segments') or []
    return []


def fetch_segments(url, categories):
    """返回 [(start, end), ...];查不到 / 网络出错都返回 [],从不抛异常。"""
    ref = video_ref(url)
    if not ref or not categories:
        return []
    service, video_id, part = ref
    key = (service, video_id, part, tuple(sorted(categories)))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    try:
        raw = _query(service, video_id, categories)
    except Exception as e:
        log.info("sponsorblock: lookup failed for %s %s: %s", service, video_id, e)
        return []
    segments = []
    cids = {s.get('cid') for s in raw if s.get('cid')}
    if service == 'bilibili' and len(cids) > 1:
        # 多 P 视频:没法把 p 序号对应到 cid,宁可不跳也不要跳错位置
        raw = []
    elif service == 'bilibili' and part not in (None, '1') and cids:
        raw = []
    for s in raw:
        if s.get('actionType', 'skip') != 'skip':
            continue
        try:
            start, end = float(s['segment'][0]), float(s['segment'][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if end > start:
            segments.append((start, end))
    segments = merge_segments(segments)
    with _lock:
        _cache[key] = (now, segments)
    if segments:
        log.info("sponsorblock: %d segment(s) to skip in %s %s", len(segments), service, video_id)
    return segments


def segment_at(segments, playhead, tolerance=0.5):
    """playhead 落在某个片段里(且离片段结尾还有 tolerance 秒以上)就返回该片段。"""
    for start, end in segments:
        if start - 0.05 <= playhead < end - tolerance:
            return start, end
    return None
