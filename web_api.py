"""JSON API blueprint for the new web interface.

All endpoints live under /api/ and return plain JSON (no HTML fragments,
unlike the legacy routes in interface.py). The blueprint is created via
create_blueprint() so interface.py can inject its requires_auth decorator
without a circular import.
"""

import base64
import time

from flask import Blueprint, Response, abort, jsonify, request

import media.playlist
import util
import variables as var
import web_search
from media.cache import get_cached_wrapper_from_scrap


def _current_wrapper():
    if len(var.playlist) == 0:
        return None
    wrapper = var.playlist.current_item()
    return wrapper or None


def _item_summary(wrapper, index):
    item = wrapper.item()
    summary = {
        'id': item.id,
        'index': index,
        'type': item.display_type(),
        'title': item.format_title(),
        'artist': getattr(item, 'artist', '') or '',
        'url': getattr(item, 'url', '') or '',
        'duration': getattr(item, 'duration', 0) or 0,
        'has_thumbnail': bool(getattr(item, 'thumbnail', None)),
    }
    return summary


def _status_payload():
    bot = var.bot
    current = _current_wrapper()
    payload = {
        'version': var.playlist.version,
        'empty': current is None,
        'play': not bot.is_pause,
        'mode': var.playlist.mode,
        'volume': bot.volume_helper.plain_volume_set,
        'ducking': bool(getattr(bot, 'is_ducking', False)
                        and getattr(bot, 'on_ducking', False)),
        'playhead': bot.playhead if current is not None else 0,
        'queue_length': len(var.playlist),
        'current_index': var.playlist.current_index,
        'server_time': time.time(),
        'current': None,
    }
    if current is not None:
        try:
            payload['current'] = _item_summary(
                current, var.playlist.current_index)
        except Exception:
            # an item mid-eviction must not break the poll
            payload['current'] = None
    return payload


def _set_volume(value):
    value = max(0.0, min(1.0, round(float(value), 2)))
    var.bot.volume_helper.set_volume(value)
    var.db.set('bot', 'volume', str(var.bot.volume_helper.plain_volume_set))


def _set_mode(mode):
    """Same semantics as the legacy /post 'action' mode switches."""
    if mode not in ('one-shot', 'repeat', 'random', 'autoplay'):
        abort(400)
    if mode == 'random':
        if var.playlist.mode != 'random':
            var.playlist = media.playlist.get_playlist('random', var.playlist)
        else:
            var.playlist.randomize()
        var.bot.interrupt()
    else:
        var.playlist = media.playlist.get_playlist(mode, var.playlist)
    var.db.set('playlist', 'playback_mode', mode)


def _skip():
    """Same semantics as the legacy /post action=next."""
    if not var.bot.is_pause:
        var.bot.interrupt()
    else:
        var.playlist.next()
        var.bot.wait_for_ready = True


def _stop():
    if var.config.getboolean('bot', 'clear_when_stop_in_oneshot') \
            and var.playlist.mode == 'one-shot':
        var.bot.clear()
    else:
        var.bot.stop()


def _queue_move(src, dst):
    playlist = var.playlist
    with playlist.playlist_lock:
        n = len(playlist)
        if not (0 <= src < n and 0 <= dst < n):
            abort(400)
        if src == dst:
            return
        wrapper = list.pop(playlist, src)
        list.insert(playlist, dst, wrapper)
        ci = playlist.current_index
        if src == ci:
            playlist.current_index = dst
        elif src < ci <= dst:
            playlist.current_index = ci - 1
        elif dst <= ci < src:
            playlist.current_index = ci + 1
        playlist.version += 1


def _queue_remove(index):
    """Same semantics as the legacy /post delete_music."""
    playlist = var.playlist
    if not (0 <= index < len(playlist)):
        abort(400)
    if index == playlist.current_index:
        playlist.remove(index)
        if index < len(playlist):
            if not var.bot.is_pause:
                var.bot.interrupt()
                playlist.current_index -= 1
        else:
            playlist.current_index -= 1
            if not var.bot.is_pause:
                var.bot.interrupt()
    else:
        playlist.remove(index)


def create_blueprint(requires_auth):
    api = Blueprint('api', __name__, url_prefix='/api')

    @api.route('/status', methods=['GET'])
    @requires_auth
    def api_status():
        """Lightweight polling endpoint: everything the Now Playing screen
        needs except the thumbnail (fetched separately, keyed by item id,
        so polls stay small)."""
        return jsonify(_status_payload())

    @api.route('/controls', methods=['POST'])
    @requires_auth
    def api_controls():
        """Transport and bot controls. Body: {"action": ..., ...extras}.
        Mirrors the semantics of the legacy /post endpoint so both UIs
        behave identically."""
        payload = request.get_json(silent=True) or request.form
        action = payload.get('action') if payload else None
        if not action:
            abort(400)
        if action == 'pause':
            var.bot.pause()
        elif action == 'resume':
            var.bot.resume()
        elif action == 'skip':
            _skip()
        elif action == 'stop':
            _stop()
        elif action == 'clear':
            var.bot.clear()
        elif action == 'mode':
            _set_mode(payload.get('mode'))
        elif action == 'volume':
            try:
                _set_volume(payload.get('volume'))
            except (TypeError, ValueError):
                abort(400)
        else:
            abort(400)
        return jsonify(_status_payload())

    @api.route('/queue', methods=['GET'])
    @requires_auth
    def api_queue():
        items = []
        with var.playlist.playlist_lock:
            wrappers = list(var.playlist)
            current_index = var.playlist.current_index
        for index, wrapper in enumerate(wrappers):
            try:
                summary = _item_summary(wrapper, index)
            except Exception:
                continue
            summary['is_current'] = index == current_index
            items.append(summary)
        return jsonify({
            'items': items,
            'current_index': current_index,
            'version': var.playlist.version,
        })

    @api.route('/queue', methods=['POST'])
    @requires_auth
    def api_queue_edit():
        """Queue edits. Body: {"action": "move"|"top"|"remove"|"play"|"clear",
        with "index" (and "to" for move)}."""
        payload = request.get_json(silent=True) or request.form
        action = payload.get('action') if payload else None
        if not action:
            abort(400)
        try:
            index = int(payload.get('index', -1))
            to = int(payload.get('to', -1))
        except (TypeError, ValueError):
            abort(400)
        if action == 'move':
            _queue_move(index, to)
        elif action == 'top':
            # insert right after the currently playing item
            dst = min(max(var.playlist.current_index + 1, 0),
                      max(len(var.playlist) - 1, 0))
            _queue_move(index, dst)
        elif action == 'remove':
            _queue_remove(index)
        elif action == 'play':
            if not (0 <= index < len(var.playlist)):
                abort(400)
            var.bot.play(index)
        elif action == 'clear':
            var.bot.clear()
        else:
            abort(400)
        return jsonify(_status_payload())

    @api.route('/search', methods=['GET'])
    @requires_auth
    def api_search():
        query = (request.args.get('q') or '').strip()[:200]
        if len(query) < 2:
            abort(400)
        try:
            limit = min(12, max(1, int(request.args.get('limit', 6))))
        except (TypeError, ValueError):
            limit = 6
        results, failed = web_search.unified_search(query, limit)
        return jsonify({'query': query, 'results': results, 'failed': failed})

    @api.route('/search/add', methods=['POST'])
    @requires_auth
    def api_search_add():
        """Add a search result to the queue. Bilibili results go through the
        av/BV normalization every other entry point uses."""
        payload = request.get_json(silent=True) or request.form
        if not payload:
            abort(400)
        source = payload.get('source')
        url = (payload.get('url') or '').strip()
        if source == 'bilibili':
            url = util.get_bilibili_url_from_input(payload.get('id') or url)
        if not url or not url.lower().startswith(('http://', 'https://')):
            abort(400)
        music_wrapper = get_cached_wrapper_from_scrap(
            type='url', url=url, user='Web Search')
        var.playlist.append(music_wrapper)
        if len(var.playlist) == 2:
            # mirror the legacy add_url behavior: if this became the next
            # item, start downloading right away
            var.bot.async_download_next()
        return jsonify(_status_payload())

    @api.route('/thumbnail/<item_id>', methods=['GET'])
    @requires_auth
    def api_thumbnail(item_id):
        """Raw JPEG for an item's thumbnail. Cacheable: the content for a
        given id never changes, so the frontend fetches it once per song."""
        item = var.cache.get_item_by_id(item_id)
        thumbnail = getattr(item, 'thumbnail', None) if item else None
        if not thumbnail:
            abort(404)
        try:
            data = base64.b64decode(thumbnail)
        except Exception:
            abort(404)
        return Response(data, mimetype='image/jpeg',
                        headers={'Cache-Control': 'private, max-age=86400'})

    return api
