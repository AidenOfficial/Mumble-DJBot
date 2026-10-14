"""曲库 API(/api/library/*):Library 页的检索与加入队列。

取代旧 jQuery 界面的 /library/info、/library(action=query)和 /post(add_item_*)。
"""

import logging
import math
import sqlite3
import time

from flask import Blueprint, abort, jsonify, request

import util
import variables as var
from database import Condition
from media.cache import get_cached_wrapper_by_id
from media.item import dicts_to_items
from web_users import current_user_name, enqueue

log = logging.getLogger("bot")

ITEM_PER_PAGE = 10
ITEM_TYPES = ('file', 'url', 'radio')


def _payload():
    return request.get_json(silent=True) or request.form or {}


def get_all_dirs():
    """曲库里出现过的所有目录('.' 为根)。"""
    dirs = ["."]
    for path in var.music_db.query_all_paths():
        pos = 0
        while True:
            pos = path.find("/", pos + 1)
            if pos == -1:
                break
            folder = path[:pos]
            if folder not in dirs:
                dirs.append(folder)
    return dirs


def build_query_condition(payload):
    """type(逗号分隔)/ dir(仅 type=file)/ tags(逗号分隔,全部命中)/ keywords(空格分隔,全部命中)。"""
    types = [t for t in str(payload.get('type') or ','.join(ITEM_TYPES)).split(',') if t]
    if not types or any(t not in ITEM_TYPES for t in types):
        abort(400)
    condition = Condition()
    sub_cond = Condition()
    for item_type in types:
        sub_cond.or_equal("type", item_type)
    condition.and_sub_condition(sub_cond)

    if types == ['file']:
        folder = str(payload.get('dir') or '')
        if folder == ".":
            folder = ""
        if folder and not folder.endswith('/'):
            folder += '/'
        condition.and_like('path', folder + '%')

    for tag in str(payload.get('tags') or '').split(","):
        if tag:
            condition.and_like("tags", f"%{tag},%", case_sensitive=False)

    for keyword in str(payload.get('keywords') or '').split(" "):
        if keyword:
            condition.and_like("keywords", f"%{keyword}%", case_sensitive=False)

    condition.order_by('create_at', desc=True)
    return condition


def _item_payload(item):
    result = {'id': item.id, 'title': item.title, 'type': item.display_type(),
              'tags': list(item.tags), 'thumb': ''}
    if item.type != 'radio' and item.thumbnail:
        result['thumb'] = f"data:image/PNG;base64,{item.thumbnail}"
    if item.type == 'file':
        result['path'] = item.path
        result['artist'] = item.artist
    else:
        result['path'] = item.url
        result['artist'] = ''
    return result


def create_blueprint(requires_auth):
    api = Blueprint('library', __name__, url_prefix='/api/library')

    @api.route('/info', methods=['GET'])
    @requires_auth
    def library_info():
        # 重新扫描曲库期间目录/标签不完整,等它扫完
        while var.cache.dir_lock.locked():
            time.sleep(0.1)
        return jsonify({
            'dirs': get_all_dirs(),
            'tags': var.music_db.query_all_tags(),
            'upload_enabled': var.config.getboolean("webinterface", "upload_enabled"),
            'max_upload_file_size': util.parse_file_size(
                var.config.get("webinterface", "max_upload_file_size")),
        })

    @api.route('/query', methods=['POST'])
    @requires_auth
    def library_query():
        payload = _payload()
        condition = build_query_condition(payload)

        total_count = 0
        try:
            total_count = var.music_db.query_music_count(condition)
        except sqlite3.OperationalError:
            pass
        if not total_count:
            return jsonify({'items': [], 'total_pages': 0, 'active_page': 0})

        page_count = math.ceil(total_count / ITEM_PER_PAGE)
        try:
            current_page = int(payload.get('page') or 1)
        except (TypeError, ValueError):
            abort(400)
        if not 1 <= current_page <= page_count:
            current_page = 1
        condition.offset((current_page - 1) * ITEM_PER_PAGE)
        condition.limit(ITEM_PER_PAGE)

        items = dicts_to_items(var.music_db.query_music(condition))
        return jsonify({
            'items': [_item_payload(item) for item in items],
            'total_pages': page_count,
            'active_page': current_page,
        })

    @api.route('/enqueue', methods=['POST'])
    @requires_auth
    def library_enqueue():
        """把曲库条目加入队列。Body: {"id": ..., "mode": "append"|"next"}。"""
        payload = _payload()
        mode = payload.get('mode') or 'append'
        if mode not in ('append', 'next'):
            abort(400)
        music_wrapper = get_cached_wrapper_by_id(str(payload.get('id') or ''), current_user_name())
        if not music_wrapper:
            abort(404)
        log.info(f"web: add to playlist({mode}): {music_wrapper.format_debug_string()}")
        enqueue([music_wrapper], mode)
        return jsonify({'ok': True})

    return api
