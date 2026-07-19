# coding=utf-8
import threading
import time

from constants import tr_cli as tr
import util
import variables as var
import media.livestream
import media.playlist
from media.cache import get_cached_wrapper_from_scrap

from ._shared import (log, _spotify_available, send_multi_lines_in_channel, send_item_added_message)

try:
    import media.spotify
except ImportError:
    # Optional feature; cmd_play_spotify guards on _spotify_available.
    pass


# Rate-limiting for !spotify: untrusted users must not be able to spawn
# unbounded spotdl downloads (each one also runs yt-dlp + ffmpeg). At most
# _SPOTIFY_MAX_CONCURRENT run at once, and each user waits between requests.
_SPOTIFY_MAX_CONCURRENT = 2
_SPOTIFY_COOLDOWN = 5  # seconds between !spotify requests, per user
_spotify_download_sem = threading.Semaphore(_SPOTIFY_MAX_CONCURRENT)
_spotify_last_request = {}
_spotify_request_lock = threading.Lock()




def cmd_play_bilibili(bot, user, text, command, parameter):
    url = util.get_bilibili_url_from_input(parameter)
    if url:
        music_wrapper = get_cached_wrapper_from_scrap(type='url', url=url, user=user)
        var.playlist.append(music_wrapper)

        log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
        send_item_added_message(bot, music_wrapper, len(var.playlist) - 1, text)

        if len(var.playlist) == 2:
            # If I am the second item on the playlist. (I am the next one!)
            bot.async_download_next()
    else:
        bot.send_msg(tr('bad_parameter', command=command), text)


def _enqueue_live(bot, user, url, title, text):
    music_wrapper = get_cached_wrapper_from_scrap(
        type='livestream', url=url, title=title, user=user)
    var.playlist.append(music_wrapper)
    log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
    send_item_added_message(bot, music_wrapper, len(var.playlist) - 1, text)


def cmd_play_live(bot, user, text, command, parameter):
    """!live <url | keywords> - queue a live audio stream (YouTube etc.).

    With a URL the stream is queued directly (validated in the download
    thread). With keywords, a background thread searches YouTube for a
    currently-live stream and queues the first hit - handy when you have no
    idea what to listen to."""
    parameter = parameter.strip()
    if not parameter:
        bot.send_msg(tr('bad_parameter', command=command), text)
        return

    url = util.get_url_from_input(parameter)
    if url:
        _enqueue_live(bot, user, url, '', text)
        return

    # keyword search hits the network - never block the mumble loop thread
    bot.send_msg(tr('live_searching', query=parameter), text)

    def search_and_enqueue():
        try:
            lives = media.livestream.search_live_streams(parameter)
        except Exception:
            log.exception("cmd: live stream search failed")
            lives = []
        if not lives:
            bot.send_channel_msg(tr('live_no_match', query=parameter))
            return
        _enqueue_live(bot, user, lives[0]['url'], lives[0]['title'], text)

    threading.Thread(target=search_and_enqueue, name="LiveSearch", daemon=True).start()


# --- Spotify playlist lazy-loading -------------------------------------------
# A feeder thread downloads a Spotify playlist a few tracks at a time and
# keeps the playback queue topped up, instead of downloading everything up
# front. Feeders are cancelled when the queue is cleared or a new playlist
# is started.
_spotify_feeder_lock = threading.Lock()
_spotify_feeders = []          # list[threading.Event]


def _cancel_spotify_feeders():
    with _spotify_feeder_lock:
        for cancel_event in _spotify_feeders:
            cancel_event.set()
        _spotify_feeders.clear()


def _spotify_feeder_loop(bot, user, url, cancel_event):
    # Resolve the whole track list first (metadata only, no audio), then
    # download a few tracks ahead and keep refilling as they are consumed.
    buffer_target = 5
    poll = 6

    tracks = media.spotify.list_spotify_tracks(url)
    if not tracks:
        bot.send_channel_msg(tr('spotify_no_match', url=url))
        return
    bot.send_channel_msg(tr('spotify_playlist_loaded', count=len(tracks)))

    fed = 0
    hard_failures = 0
    while fed < len(tracks):
        if cancel_event.is_set() or bot.exit or not bot.mumble.is_alive():
            log.info("cmd: spotify feeder stopped (cancelled or bot exiting)")
            return

        # buffer_depth = ready tracks queued after the current one. This is
        # valid across every playback mode (in one-shot, current_index stays
        # 0 and played items are removed, so it still equals len - 1 - index).
        with var.playlist.playlist_lock:
            playlist_len = len(var.playlist)
            current_index = var.playlist.current_index
        buffer_depth = playlist_len - 1 - current_index

        if buffer_depth >= buffer_target:
            cancel_event.wait(poll)
            continue

        track = tracks[fed]
        try:
            paths = media.spotify.download_tracks([track['url']])
        except media.spotify.SpotifyError as e:
            hard_failures += 1
            log.warning("cmd: spotify feeder download failed (%s)" % e)
            if hard_failures >= 3:
                log.error("cmd: spotify feeder aborting after repeated failures")
                bot.send_channel_msg(tr('spotify_error', url=url))
                return
            fed += 1
            continue

        hard_failures = 0
        if cancel_event.is_set():
            return
        if paths:
            for path in paths:
                music_wrapper = get_cached_wrapper_from_scrap(type='file', path=path, user=user)
                var.playlist.append(music_wrapper)
                log.info("cmd: spotify feeder added: " + music_wrapper.format_debug_string())
        else:
            log.info("cmd: spotify feeder found no match for '%s', skipping"
                     % track.get('name', ''))
        fed += 1

    log.info("cmd: spotify feeder finished (%d track(s) processed)" % fed)


def _start_spotify_feeder(bot, user, url, text):
    # A new playlist takes over the feed: cancel any feeder already running.
    _cancel_spotify_feeders()

    cancel_event = threading.Event()
    with _spotify_feeder_lock:
        _spotify_feeders.append(cancel_event)

    bot.send_msg(tr('spotify_playlist_loading'), text)

    def run():
        try:
            _spotify_feeder_loop(bot, user, url, cancel_event)
        except Exception:
            log.exception("cmd: spotify feeder crashed")
            bot.send_channel_msg(tr('spotify_error', url=url))
        finally:
            with _spotify_feeder_lock:
                if cancel_event in _spotify_feeders:
                    _spotify_feeders.remove(cancel_event)

    threading.Thread(target=run, name="SpotifyFeeder", daemon=True).start()


def cmd_play_spotify(bot, user, text, command, parameter):
    parameter = parameter.strip()
    if not parameter:
        bot.send_msg(tr('bad_parameter', command=command), text)
        return

    if not _spotify_available:
        bot.send_msg(tr('spotify_unavailable'), text)
        return

    # Per-user cooldown so a single user can't spam downloads.
    now = time.time()
    with _spotify_request_lock:
        if now - _spotify_last_request.get(user, 0) < _SPOTIFY_COOLDOWN:
            bot.send_msg(tr('spotify_rate_limited'), text)
            return
        _spotify_last_request[user] = now

    if not var.config.get('spotify', 'client_id', fallback='').strip() \
            or not var.config.get('spotify', 'client_secret', fallback='').strip():
        bot.send_msg(tr('spotify_not_configured'), text)
        return

    # The parameter may be a Spotify link or free-text keywords.
    url = util.get_url_from_input(parameter)
    is_spotify_link = bool(url) and media.spotify.is_spotify_url(url)

    # A playlist / album / artist link is lazy-loaded: the first track starts
    # playing as soon as it is ready, and a feeder thread keeps the queue
    # topped up instead of downloading the whole thing up front.
    if is_spotify_link and media.spotify.is_spotify_collection(url):
        _start_spotify_feeder(bot, user, url, text)
        return

    # A single track link, or free-text keywords: fetch just one song.
    query = url if is_spotify_link else parameter
    bot.send_msg(tr('download_in_progress', item=query), text)

    # spotdl downloads matching audio from YouTube, which can be slow. Run it
    # in a separate thread so the bot stays responsive to other commands.
    def fetch_and_enqueue():
        try:
            paths = media.spotify.download_tracks([query])
        except media.spotify.SpotifyError as e:
            log.error("cmd: spotify download failed: %s" % e)
            bot.send_channel_msg(tr('spotify_error', url=query))
            return
        except Exception:
            log.exception("cmd: unexpected error while downloading from Spotify")
            bot.send_channel_msg(tr('spotify_error', url=query))
            return

        if not paths:
            bot.send_channel_msg(tr('spotify_no_match', url=query))
            return

        music_wrappers = []
        for path in paths:
            music_wrapper = get_cached_wrapper_from_scrap(type='file', path=path, user=user)
            music_wrappers.append(music_wrapper)
            log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())

        var.playlist.extend(music_wrappers)

        if len(music_wrappers) == 1:
            bot.send_channel_msg(tr('file_added', item=music_wrappers[0].format_song_string()))
        else:
            msgs = [tr('multiple_file_added')]
            for music_wrapper in music_wrappers:
                msgs.append("<b>{}</b>".format(music_wrapper.format_title()))
            send_multi_lines_in_channel(bot, msgs)

        bot.async_download_next()

    def _fetch_with_limit():
        # Cap concurrent downloads; reject (don't queue) when at capacity so a
        # burst of !spotify commands can't pile up spotdl/yt-dlp/ffmpeg jobs.
        if not _spotify_download_sem.acquire(blocking=False):
            bot.send_channel_msg(tr('spotify_busy'))
            return
        try:
            fetch_and_enqueue()
        finally:
            _spotify_download_sem.release()

    threading.Thread(target=_fetch_with_limit, name="SpotifyFetch", daemon=True).start()


