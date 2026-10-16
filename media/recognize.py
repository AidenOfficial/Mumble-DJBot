"""视频标题对不上曲库时,认歌的两个补充来源(由 media/artwork.py 调用):

  1. YouTube 简介里的「Music」卡片:Content ID 认出来的歌名 / 歌手 / 专辑。
     yt-dlp 只认旧版布局(metadataRowContainer)和 Topic 频道的自动简介,现在的卡片
     (horizontalCardListRenderer → videoAttributeViewModel)它读不到,这里自己读。
     走 innertube 的 next 接口(就是网页里 ytInitialData 那份数据),直接是 JSON。
     卡片不全可信,校验在 artwork.music_candidate。
  2. Shazam 听歌识曲:从已经下载好的音频里截 12 秒(避开 SponsorBlock 片段),本地算指纹
     (shazamio-core,Rust 实现),再把指纹发给 Shazam。接口是非官方的,随时可能失效,
     失败时只是少一个来源,不影响播放。
"""

import asyncio
import logging
import re
import shutil
import subprocess
import uuid

import variables as var
from media import sponsorblock

log = logging.getLogger("bot")


# ---- YouTube「Music」卡片 ---------------------------------------------------------

NEXT_URL = 'https://www.youtube.com/youtubei/v1/next?prettyPrint=false'
NEXT_GAP = 2.0  # 连着请求时 YouTube 会回空内容,_get_json 会因此停用两分钟
# hl 只影响区块标题("Music" / "音乐"),卡片里的歌名歌手不随它变;认区块看的是 MUSIC 图标。
# clientVersion 旧一点没关系,YouTube 长期兼容
NEXT_CLIENT = {'clientName': 'WEB', 'clientVersion': '2.20251001.00.00', 'hl': 'en', 'gl': 'US'}


def _text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        if 'content' in value or 'simpleText' in value:
            return str(value.get('content') or value.get('simpleText') or '').strip()
        return ''.join(r.get('text') or '' for r in value.get('runs') or []).strip()
    return ''


def _is_music(section):
    icon = (((section.get('footerButton') or {}).get('buttonViewModel') or {}).get('iconName') or '')
    title = _text((((section.get('header') or {}).get('richListHeaderRenderer') or {}).get('title')))
    return icon == 'MUSIC' or title == 'Music'


def parse_music_section(data):
    """next 接口的返回 → [{title, artist, album}],没有「Music」区块时是 []。"""
    cards = []
    for panel in (data or {}).get('engagementPanels') or []:
        content = ((panel.get('engagementPanelSectionListRenderer') or {}).get('content') or {})
        for section in (content.get('structuredDescriptionContentRenderer') or {}).get('items') or []:
            h = section.get('horizontalCardListRenderer')
            if not h or not _is_music(h):
                continue
            for card in h.get('cards') or []:
                vm = card.get('videoAttributeViewModel')
                if vm:
                    cards.append({'title': _text(vm.get('title')), 'artist': _text(vm.get('subtitle')),
                                  'album': _text(vm.get('secondarySubtitle'))})
    return cards


def youtube_music(url):
    """YouTube 视频简介里的「Music」卡片;不是 YouTube 链接 / 没有卡片 / 请求失败都返回 []。"""
    ref = sponsorblock.video_ref(url)
    if not ref or ref[0] != 'youtube':
        return []
    from media import artwork
    data = artwork._get_json('youtube', NEXT_URL, NEXT_GAP, method='POST',
                             json={'context': {'client': NEXT_CLIENT}, 'videoId': ref[1]})
    return parse_music_section(data)


# ---- Shazam --------------------------------------------------------------------

SHAZAM_URL = ('https://amp.shazam.com/discovery/v5/{lang}/{country}/iphone/-/tag/{u1}/{u2}'
              '?sync=true&webv3=true&sampling=true&connected=&shazamapiversion=v3&sharehub=true'
              '&hubv5minorversion=v5.1&hidelb=true&video=v3')
SHAZAM_HEADERS = {'X-Shazam-Platform': 'IPHONE', 'X-Shazam-AppVersion': '14.1.0', 'Accept': '*/*',
                  'User-Agent': 'Shazam/3685 CFNetwork/1485 Darwin/23.1.0'}
SHAZAM_GAP = 3.5      # 两次请求的间隔(秒);Shazam 大约每分钟 20 次就开始限流
CLIP_SECONDS = 12     # Shazam 只看 12 秒
MAX_TRIES = 2         # 每首歌最多截几段去认
COVER_SIZE = 1000
# 曲目详情里专辑那一行的标题按请求语言本地化
_ALBUM_LABELS = {'album', '专辑', '專輯', 'アルバム', '앨범'}

_available = None


class ShazamUnavailable(Exception):
    """网络出错 / 被限流:这次没认成,下次再试(和"认不出来"区分开)。"""


def shazam_available():
    """装了 shazamio-core 和 ffmpeg 才能用(只检查一次)。"""
    global _available
    if _available is None:
        try:
            import shazamio_core  # noqa: F401
            _available = shutil.which('ffmpeg') is not None
        except ImportError:
            _available = False
        if not _available:
            log.info("recognize: shazamio-core or ffmpeg missing, Shazam lookup disabled")
    return _available


def clip_offsets(duration, segments=(), count=MAX_TRIES):
    """截取起点:避开 SponsorBlock 片段,优先取 35% 和 65% 处(片头片尾常是说话/空白)。"""
    if not duration:
        return [30.0]  # 不知道时长(老条目):跳过前奏试一段
    if duration < CLIP_SECONDS + 2:
        return [0.0]
    free, cursor = [], 0.0
    for start, end in sorted(segments):
        if start > cursor:
            free.append((cursor, min(start, duration)))
        cursor = max(cursor, end)
    if cursor < duration:
        free.append((cursor, duration))
    free = [(a, b) for a, b in free if b - a >= CLIP_SECONDS]
    offsets = []
    for frac in (0.35, 0.65)[:count]:
        t = duration * frac
        a, b = min(free, key=lambda iv: 0 if iv[0] <= t <= iv[1] else min(abs(t - iv[0]), abs(t - iv[1])),
                   default=(None, None))
        if a is None:
            break
        start = round(float(min(max(t, a), b - CLIP_SECONDS)), 1)
        if all(abs(start - o) >= CLIP_SECONDS for o in offsets):
            offsets.append(start)
    return offsets


def _clip(path, start):
    """截一段 16kHz 单声道 WAV(Rust 那边解不了 Opus,先让 ffmpeg 转好)。"""
    cmd = ['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(start), '-t', str(CLIP_SECONDS), '-i', path,
           '-vn', '-ac', '1', '-ar', '16000', '-f', 'wav', 'pipe:1']
    return subprocess.run(cmd, capture_output=True, check=True, timeout=60).stdout


def _signature(wav):
    from shazamio_core import Recognizer, SearchParams

    async def run():  # 接口是 async 的,要在事件循环里调用
        recognizer = Recognizer(segment_duration_seconds=CLIP_SECONDS)
        return await recognizer.recognize_bytes(wav, SearchParams(segment_duration_seconds=CLIP_SECONDS))
    sig = asyncio.run(run())
    return sig.signature.uri, sig.signature.samples, sig.timestamp


def _language():
    lang = (var.config.get('bot', 'artwork_shazam_language', fallback='zh-CN') if var.config else 'zh-CN')
    lang = (lang or 'zh-CN').strip()
    country = lang.split('-')[1].upper() if '-' in lang else 'US'
    return lang, country


def parse_shazam(data):
    """Shazam 的返回 → {title, artists, album, image, source} 或 None(没认出来)。"""
    track = (data or {}).get('track') or {}
    if not (data or {}).get('matches') or not track.get('title'):
        return None
    album = ''
    for section in track.get('sections') or []:
        for row in section.get('metadata') or []:
            if (row.get('title') or '').strip().lower() in _ALBUM_LABELS:
                album = (row.get('text') or '').strip()
                break
        if album:
            break
    images = track.get('images') or {}
    image = images.get('coverarthq') or images.get('coverart') or ''
    # Apple 的图床按路径里的尺寸出图,换成大图
    image = re.sub(r'/\d+x\d+(\w*)\.jpg$', rf'/{COVER_SIZE}x{COVER_SIZE}\1.jpg', image)
    return {
        'title': track['title'].strip(),
        'artists': [(track.get('subtitle') or '').strip()],
        'album': album,
        'duration': 0,
        'image': image or None,
        'source': 'shazam',
    }


def shazam(path, duration=0, url=''):
    """听歌识曲:path 是下载好的音频。返回同 parse_shazam;认不出 / 没装依赖是 None,
    请求失败抛 ShazamUnavailable。"""
    if not shazam_available():
        return None
    from media import artwork
    segments = sponsorblock.fetch_segments(url, sponsorblock.configured_categories()) if url else []
    lang, country = _language()
    for start in clip_offsets(duration, segments):
        try:
            uri, samplems, timestamp = _signature(_clip(path, start))
        except Exception as e:
            log.debug("recognize: no signature for %s at %.0fs: %s", path, start, e)
            continue
        body = {'timezone': 'Asia/Tokyo', 'signature': {'uri': uri, 'samplems': samplems},
                'timestamp': timestamp, 'context': {}, 'geolocation': {}}
        data = artwork._get_json('shazam', SHAZAM_URL.format(lang=lang, country=country,
                                                             u1=str(uuid.uuid4()).upper(),
                                                             u2=str(uuid.uuid4()).upper()),
                                 SHAZAM_GAP, method='POST', json=body,
                                 headers=dict(SHAZAM_HEADERS, **{'Accept-Language': lang}))
        if data is None:
            raise ShazamUnavailable()  # 网络出错 / 被限流:别接着试
        match = parse_shazam(data)
        if match:
            log.info("recognize: shazam matched %s at %.0fs: %s - %s",
                     path, start, match['artists'][0], match['title'])
            return match
    return None
