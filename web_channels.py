"""频道 API(/api/channels*):频道树、默认频道、跟随设置、立即移动。逻辑在 bot/channels.py。"""

import logging

from flask import Blueprint, abort, jsonify, request

import variables as var
from bot.channels import FOLLOW_MODES
from web_users import current_user_name

log = logging.getLogger("bot")


def _payload():
    return request.get_json(silent=True) or request.form or {}


def overview():
    bot = var.bot
    settings = bot.channel_settings()
    tree = bot.channel_tree()
    humans = sorted({u.name for users in bot.humans_by_channel().values() for u in users},
                    key=str.lower)
    return {
        'tree': tree,
        'current_channel_id': bot.my_channel_id(),
        'settings': {
            'default_path': settings['default'],
            'default_channel_id': bot.find_channel_by_path(settings['default']),
            'follow': settings['follow'],
            'follow_user': settings['follow_user'],
            'return_home': settings['return_home'],
        },
        'online_users': humans,
    }


def create_blueprint(requires_auth):
    api = Blueprint('channels', __name__, url_prefix='/api/channels')

    @api.route('', methods=['GET'])
    @requires_auth
    def channels():
        return jsonify(overview())

    @api.route('/settings', methods=['POST'])
    @requires_auth
    def channel_settings():
        payload = _payload()
        changes = {}
        if 'default_channel_id' in payload:
            try:
                cid = int(payload['default_channel_id'])
            except (TypeError, ValueError):
                abort(400)
            if cid not in var.bot.mumble.channels:
                abort(404)
            changes['default'] = var.bot.channel_path(cid)
        if 'follow' in payload:
            if payload['follow'] not in FOLLOW_MODES:
                abort(400)
            changes['follow'] = payload['follow']
        if 'follow_user' in payload:
            name = str(payload['follow_user'] or '').strip()
            if len(name) > 128:
                abort(400)
            changes['follow_user'] = name
        if 'return_home' in payload:
            changes['return_home'] = bool(payload['return_home'])
        if not changes:
            abort(400)
        var.bot.save_channel_settings(**changes)
        log.info("web: %s changed channel settings: %s", current_user_name(), changes)
        return jsonify(overview())

    @api.route('/join', methods=['POST'])
    @requires_auth
    def channel_join():
        try:
            cid = int(_payload().get('channel_id'))
        except (TypeError, ValueError):
            abort(400)
        if cid not in var.bot.mumble.channels:
            abort(404)
        var.bot.move_to_channel(cid, f"requested by {current_user_name()} on the web")
        return jsonify(overview())

    return api
