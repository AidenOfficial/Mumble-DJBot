"""日志面板 API(Settings → Logs)。

GET  /api/logs                 增量拉取:?after=<seq>&level=INFO&q=关键词
POST /api/logs/debug           {enabled: bool} 临时开 / 关调试日志(30 分钟后自动关)
GET  /api/logs/download        导出为纯文本(带版本 / 环境信息),方便贴进 issue
POST /api/logs/client          前端未捕获的 JS 错误上报,记为 bot.webui

可见范围:[webinterface] log_viewers 为空时所有能打开 Web UI 的人都能看
(与设置页其他功能同一信任边界);填了就只有列出的身份(邮箱或 user:<用户名>)能看。
"""

import logging
import platform
import sys
import threading
import time

from flask import Blueprint, Response, abort, jsonify, request

import variables as var
from bot.logbuffer import buffer
from web_users import current_identity

log = logging.getLogger("bot")
client_log = logging.getLogger("bot.webui")

CLIENT_REPORTS_PER_MINUTE = 30
_client_lock = threading.Lock()
_client_window = [0.0, 0]  # [窗口起点, 窗口内条数]


def _viewers():
    raw = var.config.get('webinterface', 'log_viewers', fallback='') if var.config else ''
    return {v.strip().lower() for v in raw.split(',') if v.strip()}


def can_view():
    viewers = _viewers()
    if not viewers:
        return True
    key = current_identity().key
    return bool(key) and key.lower() in viewers


def _require_viewer():
    if not can_view():
        abort(403)


def _int_arg(name, default):
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        abort(400)


def _format_entry(e):
    ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e['ts'])) + f".{int(e['ts'] * 1000) % 1000:03d}"
    line = f"{ts} {e['level']:<8} {e['name']} [{e.get('thread', '')}] {e.get('src', '')}  {e['msg']}"
    if e.get('exc'):
        line += '\n' + '\n'.join('    ' + l for l in e['exc'].rstrip().splitlines())
    return line


def _environment_lines():
    lines = []
    try:
        lines.append(f"version: {var.bot.get_version()}")
    except Exception:
        pass
    lines.append(f"python: {sys.version.split()[0]} · {platform.platform()}")
    try:
        import yt_dlp
        lines.append(f"yt-dlp: {yt_dlp.version.__version__}")
    except Exception:
        pass
    try:
        lines.append(f"playback: mode={var.playlist.mode} queue={len(var.playlist)} "
                     f"index={var.playlist.current_index} paused={var.bot.is_pause}")
    except Exception:
        pass
    return lines


def create_blueprint(requires_auth):
    api = Blueprint('logs', __name__, url_prefix='/api')

    @api.route('/logs', methods=['GET'])
    @requires_auth
    def logs():
        _require_viewer()
        result = buffer.query(after=_int_arg('after', 0),
                              min_level=request.args.get('level', 'DEBUG'),
                              q=request.args.get('q', '')[:200],
                              limit=min(max(_int_arg('limit', 500), 1), 2000))
        result['state'] = buffer.state()
        return jsonify(result)

    @api.route('/logs/debug', methods=['POST'])
    @requires_auth
    def set_debug():
        _require_viewer()
        payload = request.get_json(silent=True) or {}
        enabled = bool(payload.get('enabled'))
        log.info("web: %s turned debug logging %s", current_identity().display_name, 'on' if enabled else 'off')
        buffer.set_debug(enabled)
        return jsonify(buffer.state())

    @api.route('/logs/download', methods=['GET'])
    @requires_auth
    def download():
        _require_viewer()
        level = request.args.get('level', 'DEBUG')
        q = request.args.get('q', '')[:200]
        result = buffer.query(min_level=level, q=q, limit=10 ** 6)
        state = buffer.state()
        header = [
            f"Mumble-DJBot logs · exported {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
            *_environment_lines(),
            f"current run: {state['run']} (started {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state['started_at']))})"
            f" · bot log level: {state['level']}",
            f"filter: level>={level}" + (f" q={q!r}" if q else ''),
            '-' * 72,
        ]
        body = []
        last_run = None
        for e in result['entries']:
            if e.get('run') != last_run:
                if last_run is not None:
                    body.append(f"======== bot restarted (run {e.get('run')}) ========")
                last_run = e.get('run')
            body.append(_format_entry(e))
        text = '\n'.join(header + body) + '\n'
        filename = time.strftime('djbot-logs-%Y%m%d-%H%M%S.txt')
        return Response(text, mimetype='text/plain',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})

    @api.route('/logs/client', methods=['POST'])
    @requires_auth
    def client_error():
        """前端 JS 报错。全局限速,防止某个渲染死循环把日志刷满。"""
        now = time.time()
        with _client_lock:
            if now - _client_window[0] > 60:
                _client_window[0], _client_window[1] = now, 0
            _client_window[1] += 1
            if _client_window[1] > CLIENT_REPORTS_PER_MINUTE:
                return jsonify({'ok': False, 'throttled': True}), 429
        payload = request.get_json(silent=True) or {}
        message = str(payload.get('message') or '')[:1000]
        if not message:
            abort(400)
        stack = str(payload.get('stack') or '')[:4000]
        where = str(payload.get('url') or '')[:300]
        agent = request.headers.get('User-Agent', '')[:200]
        client_log.warning("browser error from %s: %s\n  at %s\n  ua: %s%s",
                           current_identity().display_name, message, where, agent,
                           ('\n' + stack) if stack else '')
        return jsonify({'ok': True})

    return api
