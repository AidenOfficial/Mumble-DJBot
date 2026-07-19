"""Live audio streams resolved through yt-dlp (YouTube live and friends).

A LiveStreamItem never downloads anything: validate() only confirms the URL
is a currently-running live stream and grabs its metadata; uri() resolves a
fresh direct audio URL (HLS/DASH) right before ffmpeg starts, because those
URLs expire after a while. Playback is ffmpeg reading the stream directly,
exactly like a radio station.
"""
import hashlib
import logging

import yt_dlp as youtube_dl

from constants import tr_cli as tr
from media.item import (BaseItem, item_builders, item_loaders,
                        item_id_generators, ValidationFailedError)
import variables as var

log = logging.getLogger("bot")


def livestream_item_builder(**kwargs):
    return LiveStreamItem(kwargs['url'], title=kwargs.get('title', ''))


def livestream_item_loader(_dict):
    return LiveStreamItem("", from_dict=_dict)


def livestream_item_id_generator(**kwargs):
    # "live:" prefix so the same URL can also exist as a plain url item
    # (e.g. a stream VOD added with !url after the broadcast ended).
    return hashlib.md5(('live:' + kwargs['url']).encode()).hexdigest()


item_builders['livestream'] = livestream_item_builder
item_loaders['livestream'] = livestream_item_loader
item_id_generators['livestream'] = livestream_item_id_generator


def _base_ydl_opts():
    opts = {'noplaylist': True, 'quiet': True, 'no_warnings': True}
    cookie = var.config.get('youtube_dl', 'cookie_file')
    if cookie:
        opts['cookiefile'] = cookie
    user_agent = var.config.get('youtube_dl', 'user_agent')
    if user_agent:
        youtube_dl.utils.std_headers['User-Agent'] = user_agent
    return opts


def search_live_streams(keywords, limit=10):
    """Search YouTube for currently-live streams matching `keywords`.
    Returns a list of {'url', 'title'} dicts (possibly empty)."""
    opts = _base_ydl_opts()
    opts['extract_flat'] = True
    with youtube_dl.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{keywords}", download=False)
    return pick_live_entries(info)


def pick_live_entries(search_info):
    """Pure helper: filter a flat ytsearch result down to live entries."""
    lives = []
    for entry in (search_info or {}).get('entries') or []:
        if not entry:
            continue
        if entry.get('live_status') == 'is_live' or entry.get('is_live'):
            url = entry.get('url') or entry.get('webpage_url')
            if url:
                lives.append({'url': url, 'title': entry.get('title') or url})
    return lives


class LiveStreamItem(BaseItem):
    def __init__(self, url, title="", from_dict=None):
        if from_dict is None:
            super().__init__()
            self.url = url
            self.title = title
            self.thumbnail = ""
            self.id = livestream_item_id_generator(url=url)
        else:
            super().__init__(from_dict)
            self.url = from_dict['url']
            self.thumbnail = from_dict.get('thumbnail', '')
            # a saved live stream may have ended long ago - force a fresh
            # validation before it is played again
            if self.ready == 'yes':
                self.ready = 'pending'

        self.type = "livestream"

    def validate(self):
        if self.ready == 'yes':
            return True
        info = self._fetch_info()
        if not (info.get('is_live') or info.get('live_status') == 'is_live'):
            self.ready = 'failed'
            raise ValidationFailedError(tr('live_not_live', item=self.format_title()))
        self.title = (info.get('title') or self.url).strip()
        self.keywords = self.title
        self.thumbnail = ''  # thumbnails of live pages expire; not worth caching
        self.ready = 'yes'
        self.version += 1
        return True

    def _fetch_info(self):
        self.log.info("live: fetching metadata of stream %s" % self.url)
        try:
            with youtube_dl.YoutubeDL(_base_ydl_opts()) as ydl:
                return ydl.extract_info(self.url, download=False)
        except youtube_dl.utils.DownloadError:
            self.ready = 'failed'
            raise ValidationFailedError(tr('unable_download', item=self.format_title()))

    def uri(self):
        """Resolve a fresh direct audio URL. Called right before ffmpeg is
        launched (stream URLs expire, so never reuse an old one)."""
        opts = _base_ydl_opts()
        opts['format'] = 'bestaudio/best'
        with youtube_dl.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(self.url, download=False)
        stream_url = info.get('url')
        if not stream_url:
            for fmt in reversed(info.get('formats') or []):
                if fmt.get('url'):
                    stream_url = fmt['url']
                    break
        if not stream_url:
            raise ValidationFailedError(tr('unable_download', item=self.format_title()))
        return stream_url

    def to_dict(self):
        d = super().to_dict()
        d['url'] = self.url
        d['thumbnail'] = self.thumbnail
        return d

    def format_debug_string(self):
        return "[livestream] {title} ({url})".format(title=self.title, url=self.url)

    def format_song_string(self, user):
        return tr("livestream_item", url=self.url,
                  title=self.title or self.url, user=user)

    def format_current_playing(self, user):
        return tr("now_playing", item=self.format_song_string(user))

    def format_title(self):
        return self.title if self.title else self.url

    def display_type(self):
        return tr("livestream")
