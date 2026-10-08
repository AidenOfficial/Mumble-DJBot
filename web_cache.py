"""缓存管理 API(/api/cache/*),逻辑都在 bot/cache_store.py。"""

import logging

from flask import Blueprint, abort, jsonify, request

from bot import cache_store
from web_users import current_user_name

log = logging.getLogger("bot")


def _payload():
    return request.get_json(silent=True) or request.form or {}


def _item_id():
    item_id = str(_payload().get('id') or '')
    if not cache_store.HEX_ID_RE.match(item_id):
        abort(400)
    return item_id


def create_blueprint(requires_auth):
    api = Blueprint('cache', __name__, url_prefix='/api/cache')

    @api.route('', methods=['GET'])
    @requires_auth
    def cache_overview():
        entries = cache_store.list_entries()
        return jsonify({'summary': cache_store.summary(entries), 'entries': entries})

    @api.route('/pin', methods=['POST'])
    @requires_auth
    def cache_pin():
        item_id = _item_id()
        pinned = bool(_payload().get('pinned', True))
        cache_store.set_pinned(item_id, pinned)
        log.info("web: %s %s cache item %s", current_user_name(), 'pinned' if pinned else 'unpinned', item_id)
        return jsonify({'id': item_id, 'pinned': pinned})

    @api.route('/delete', methods=['POST'])
    @requires_auth
    def cache_delete():
        item_id = _item_id()
        freed = cache_store.delete_entry(item_id, force=bool(_payload().get('force')))
        if freed is None:
            # 正在下载 / 在队列里(未 force)
            return jsonify({'error': 'busy'}), 409
        return jsonify({'freed': freed})

    @api.route('/cleanup', methods=['POST'])
    @requires_auth
    def cache_cleanup():
        """mode: limit(压到容量上限) / expired(跑一次按天数过期的清理) / unpinned(清空未固定的)"""
        import variables as var

        mode = _payload().get('mode', 'limit')
        before = cache_store.summary()['total_bytes']
        if mode == 'limit':
            cache_store.enforce_size_limit()
        elif mode == 'expired':
            if var.cleaner is None:
                abort(503)
            var.cleaner.run_once()
        elif mode == 'unpinned':
            cache_store.clear_unpinned()
        else:
            abort(400)
        after = cache_store.summary()['total_bytes']
        log.info("web: %s ran cache cleanup (%s), freed %d bytes", current_user_name(), mode, before - after)
        return jsonify({'freed': max(0, before - after)})

    @api.route('/save', methods=['POST'])
    @requires_auth
    def cache_save():
        item_id = _item_id()
        try:
            rel = cache_store.save_to_library(item_id)
        except OSError as e:
            log.warning("web: could not save %s to library: %s", item_id, e)
            return jsonify({'error': 'io'}), 500
        if rel is None:
            return jsonify({'error': 'incomplete'}), 409
        return jsonify({'path': rel})

    return api
