"""JSON API blueprint for the new web interface.

All endpoints live under /api/ and return plain JSON (no HTML fragments,
unlike the legacy routes in interface.py). The blueprint is created via
create_blueprint() so interface.py can inject its requires_auth decorator
without a circular import.
"""

import base64
import time

from flask import Blueprint, Response, abort, jsonify

import variables as var


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


def create_blueprint(requires_auth):
    api = Blueprint('api', __name__, url_prefix='/api')

    @api.route('/status', methods=['GET'])
    @requires_auth
    def api_status():
        """Lightweight polling endpoint: everything the Now Playing screen
        needs except the thumbnail (fetched separately, keyed by item id,
        so polls stay small)."""
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
        return jsonify(payload)

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
