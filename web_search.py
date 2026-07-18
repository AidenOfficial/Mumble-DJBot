"""Unified media search for the web interface: YouTube via yt-dlp's
ytsearchN: pseudo-URL and Bilibili via its public search API, queried in
parallel with hard timeouts. Each source degrades gracefully: a failing or
slow source just returns no results (with an 'errors' marker) instead of
breaking the whole search.
"""

import concurrent.futures
import logging
import re

import requests

import util
import variables as var

log = logging.getLogger("bot")

SEARCH_TIMEOUT = 8       # seconds per source
DEFAULT_LIMIT = 6        # results per source

_TAG_RE = re.compile(r'<[^>]+>')

_DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/124.0.0.0 Safari/537.36")


def _duration_to_seconds(text):
    """Bilibili gives durations like '4:31' or '1:02:03'."""
    if isinstance(text, (int, float)):
        return int(text)
    try:
        parts = [int(p) for p in str(text).split(':')]
    except ValueError:
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def parse_bilibili_results(data, limit=DEFAULT_LIMIT):
    """Pure parser for the JSON of api.bilibili.com search/type. Returns a
    list of normalized result dicts."""
    results = []
    if not data or data.get('code') != 0:
        return results
    for entry in (data.get('data') or {}).get('result') or []:
        if entry.get('type') != 'video':
            continue
        bvid = entry.get('bvid') or ''
        if not bvid:
            continue
        title = _TAG_RE.sub('', entry.get('title') or '')
        pic = entry.get('pic') or ''
        if pic.startswith('//'):
            pic = 'https:' + pic
        results.append({
            'source': 'bilibili',
            'id': bvid,
            'url': f'https://www.bilibili.com/video/{bvid}',
            'title': title,
            'uploader': entry.get('author') or '',
            'duration': _duration_to_seconds(entry.get('duration') or 0),
            'thumbnail': pic,
        })
        if len(results) >= limit:
            break
    return results


def parse_youtube_entries(info, limit=DEFAULT_LIMIT):
    """Pure parser for yt-dlp's flat-extracted ytsearchN: result."""
    results = []
    for entry in (info or {}).get('entries') or []:
        if not entry:
            continue
        video_id = entry.get('id') or ''
        url = entry.get('url') or (
            f'https://www.youtube.com/watch?v={video_id}' if video_id else '')
        if not url:
            continue
        thumbnail = ''
        thumbnails = entry.get('thumbnails') or []
        if thumbnails:
            thumbnail = thumbnails[-1].get('url') or ''
        elif video_id:
            thumbnail = f'https://i.ytimg.com/vi/{video_id}/mqdefault.jpg'
        results.append({
            'source': 'youtube',
            'id': video_id,
            'url': url,
            'title': entry.get('title') or '',
            'uploader': entry.get('uploader') or entry.get('channel') or '',
            'duration': int(entry.get('duration') or 0),
            'thumbnail': thumbnail,
        })
        if len(results) >= limit:
            break
    return results


def search_youtube(query, limit=DEFAULT_LIMIT):
    import yt_dlp
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'skip_download': True,
        'socket_timeout': SEARCH_TIMEOUT,
    }
    cookie = var.config.get('youtube_dl', 'cookie_file', fallback='')
    if cookie:
        ydl_opts['cookiefile'] = cookie
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f'ytsearch{limit}:{query}', download=False)
    return parse_youtube_entries(info, limit)


def search_bilibili(query, limit=DEFAULT_LIMIT):
    session = requests.Session()
    user_agent = var.config.get('youtube_dl', 'user_agent', fallback='') or _DEFAULT_UA
    session.headers.update({
        'User-Agent': user_agent,
        'Referer': 'https://www.bilibili.com/',
    })
    cookie_file = var.config.get('youtube_dl', 'cookie_file', fallback='')
    if cookie_file:
        try:
            session.cookies.update(util.parse_cookie_file(cookie_file))
        except OSError:
            pass
    if not session.cookies:
        # seed the anti-crawl buvid cookies with a cheap front page hit
        try:
            session.get('https://www.bilibili.com/', timeout=SEARCH_TIMEOUT)
        except requests.exceptions.RequestException:
            pass
    r = session.get(
        'https://api.bilibili.com/x/web-interface/search/type',
        params={'search_type': 'video', 'keyword': query, 'page_size': limit},
        timeout=SEARCH_TIMEOUT)
    r.raise_for_status()
    return parse_bilibili_results(r.json(), limit)


def unified_search(query, limit=DEFAULT_LIMIT):
    """Query both sources in parallel. Returns (results, failed_sources):
    results are interleaved youtube/bilibili to mix the sources fairly."""
    sources = {'youtube': search_youtube, 'bilibili': search_bilibili}
    per_source = {}
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = {name: pool.submit(fn, query, limit)
                   for name, fn in sources.items()}
        for name, future in futures.items():
            try:
                per_source[name] = future.result(timeout=SEARCH_TIMEOUT + 2)
            except Exception as e:
                log.warning('web: %s search failed for %r (%s)', name, query, e)
                per_source[name] = []
                failed.append(name)

    interleaved = []
    lists = [per_source.get('youtube') or [], per_source.get('bilibili') or []]
    for i in range(max(len(x) for x in lists) if any(lists) else 0):
        for lst in lists:
            if i < len(lst):
                interleaved.append(lst[i])
    return interleaved, failed
