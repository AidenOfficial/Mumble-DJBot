"""大文件分片上传(/api/upload/*)。

旧的 /upload 一次性 multipart 上传:经 Cloudflare Tunnel 时单个请求体超过
100MB(免费/Pro 套餐上限)直接被拦,超长视频根本传不上来。这里改成:

  POST /api/upload/init     {filename, size, targetdir?}  -> {upload_id, chunk_size}
  PUT  /api/upload/<id>?offset=N   请求体 = 原始字节(每片 <= chunk_size)
  POST /api/upload/<id>/finish     -> 后台处理,立即返回
  GET  /api/upload/<id>            -> 进度/结果(status: uploading|processing|done|error)

断点续传:PUT 的 offset 必须等于服务器已收到的字节数;offset 落在已收范围内
且不越界的重复分片直接确认(网络重试安全)。

完成后:视频文件默认只抽出音轨(ffmpeg -c:a copy 进 .mka,不重编码,几 GB 的视频
通常变成几十到几百 MB),然后直接登记进曲库,不用再手动 rescan。
暂存在 <tmp_folder>/.uploads/,超过一天没动的半成品会被清掉。
"""

import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import uuid

from flask import Blueprint, abort, jsonify, request

import util
import variables as var
from web_users import current_user_name

log = logging.getLogger("bot")

CHUNK_SIZE = 32 * 1024 * 1024   # 远低于 Cloudflare 100MB 的请求体上限
STALE_SECONDS = 24 * 3600
_ID_RE = re.compile(r'^[0-9a-f]{32}$')
_lock = threading.Lock()


def staging_dir():
    path = os.path.join(var.tmp_folder, '.uploads')
    os.makedirs(path, exist_ok=True)
    return path


def _meta_path(upload_id):
    return os.path.join(staging_dir(), upload_id + '.json')


def _data_path(upload_id):
    return os.path.join(staging_dir(), upload_id + '.part')


def _load(upload_id):
    if not _ID_RE.match(upload_id or ''):
        abort(404)
    try:
        with open(_meta_path(upload_id), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        abort(404)


def _save(meta):
    tmp = _meta_path(meta['id']) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, _meta_path(meta['id']))


def max_upload_bytes():
    try:
        return util.parse_file_size(var.config.get('webinterface', 'max_upload_file_size'))
    except (ValueError, TypeError):
        return 30 * 1024 * 1024


def clean_filename(name):
    """保留中日文等 Unicode(werkzeug.secure_filename 会把它们全删光),
    只去掉路径分隔符和控制字符。"""
    name = os.path.basename((name or '').replace('\\', '/'))
    name = re.sub(r'[\x00-\x1f<>:"|?*]+', '', name).strip().lstrip('.')
    return name[:200]


def resolve_target_dir(targetdir):
    targetdir = (targetdir or '').strip() or 'uploads/'
    if '..' in targetdir.replace('\\', '/').split('/'):
        abort(403)
    root = os.path.abspath(var.music_folder)
    path = os.path.abspath(os.path.join(root, targetdir))
    if path != root and not path.startswith(root + os.sep):
        abort(403)
    return path


def _media_kind(filename):
    mime = mimetypes.guess_type(filename)[0] or ''
    if mime.startswith('audio/'):
        return 'audio'
    if mime.startswith('video/') or filename.lower().endswith(('.mkv', '.webm', '.flv', '.ts', '.m2ts')):
        return 'video'
    return None


def prune_stale(now=None):
    now = now or time.time()
    folder = staging_dir()
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if now - os.path.getmtime(path) > STALE_SECONDS:
                os.remove(path)
        except OSError:
            continue


def _unique_path(directory, filename):
    base, ext = os.path.splitext(filename)
    path = os.path.join(directory, filename)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(directory, f"{base} ({n}){ext}")
    return path


def extract_audio(src, dst):
    """视频只留音轨,流复制不重编码。成功返回 True。"""
    cmd = ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', src,
           '-vn', '-sn', '-dn', '-map', '0:a:0', '-c:a', 'copy', dst]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("upload: audio extraction failed to run: %s", e)
        return False
    if proc.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        log.warning("upload: audio extraction failed: %s", proc.stderr.decode('utf-8', 'replace')[-500:])
        try:
            os.remove(dst)
        except OSError:
            pass
        return False
    return True


def finalize(upload_id):
    """后台线程:搬进曲库(视频先抽音轨),登记数据库,更新状态。"""
    import media.file  # noqa: F401  注册 file 类型
    from media.item import item_builders

    meta = _load(upload_id)
    data = _data_path(upload_id)
    try:
        target_dir = resolve_target_dir(meta['targetdir'])
        os.makedirs(target_dir, exist_ok=True)
        filename = meta['filename']
        extracted = False
        if meta['kind'] == 'video' and var.config.getboolean('webinterface', 'upload_extract_audio',
                                                             fallback=True):
            meta['status'] = 'processing'
            _save(meta)
            dst = _unique_path(target_dir, os.path.splitext(filename)[0] + '.mka')
            if extract_audio(data, dst):
                os.remove(data)
                extracted = True
        if not extracted:
            dst = _unique_path(target_dir, filename)
            shutil.move(data, dst)
        rel = os.path.relpath(dst, os.path.abspath(var.music_folder))
        item = item_builders['file'](path=rel)
        if not item.title:
            item.title = os.path.splitext(os.path.basename(rel))[0]
            item.keywords = item.title
        var.music_db.insert_music(item.to_dict())
        meta.update(status='done', path=rel, item_id=item.id, title=item.title,
                    duration=item.duration, extracted=extracted,
                    final_size=os.path.getsize(dst))
        log.info("upload: %s finished %s (%s)", meta.get('user'), rel,
                 'audio extracted' if extracted else 'stored as is')
    except Exception as e:
        log.exception("upload: finalizing %s failed", upload_id)
        meta.update(status='error', error=str(e)[:200])
    _save(meta)


def create_blueprint(requires_auth):
    api = Blueprint('upload', __name__, url_prefix='/api/upload')

    def _check_enabled():
        if not var.config.getboolean('webinterface', 'upload_enabled'):
            abort(403)

    @api.route('/init', methods=['POST'])
    @requires_auth
    def upload_init():
        _check_enabled()
        payload = request.get_json(silent=True) or {}
        filename = clean_filename(payload.get('filename'))
        try:
            size = int(payload.get('size'))
        except (TypeError, ValueError):
            abort(400)
        if not filename or size <= 0:
            abort(400)
        kind = _media_kind(filename)
        if kind is None:
            return jsonify({'error': 'unsupported_type'}), 415
        limit = max_upload_bytes()
        if size > limit:
            return jsonify({'error': 'too_large', 'limit': limit}), 413
        resolve_target_dir(payload.get('targetdir'))  # 先校验,防路径穿越
        prune_stale()
        free = shutil.disk_usage(staging_dir()).free
        if size * 1.05 > free:
            return jsonify({'error': 'no_space', 'free': free}), 507
        upload_id = uuid.uuid4().hex
        meta = {'id': upload_id, 'filename': filename, 'size': size, 'kind': kind,
                'targetdir': (payload.get('targetdir') or '').strip() or 'uploads/',
                'received': 0, 'status': 'uploading', 'user': current_user_name(),
                'created_at': time.time()}
        open(_data_path(upload_id), 'wb').close()
        _save(meta)
        log.info("upload: %s started %s (%.1f MB)", meta['user'], filename, size / 1048576)
        return jsonify({'upload_id': upload_id, 'chunk_size': CHUNK_SIZE})

    @api.route('/<upload_id>', methods=['PUT'])
    @requires_auth
    def upload_chunk(upload_id):
        _check_enabled()
        try:
            offset = int(request.args.get('offset', -1))
        except ValueError:
            abort(400)
        with _lock:
            meta = _load(upload_id)
            if meta['status'] != 'uploading':
                abort(409)
            body = request.get_data(cache=False)
            if len(body) > CHUNK_SIZE or offset < 0:
                abort(413 if len(body) > CHUNK_SIZE else 400)
            received = meta['received']
            if offset + len(body) <= received:
                return jsonify({'received': received})  # 重试的旧分片,幂等确认
            if offset != received:
                return jsonify({'error': 'offset_mismatch', 'received': received}), 409
            if received + len(body) > meta['size']:
                abort(413)
            with open(_data_path(upload_id), 'ab') as f:
                f.write(body)
            meta['received'] = received + len(body)
            _save(meta)
            return jsonify({'received': meta['received']})

    @api.route('/<upload_id>/finish', methods=['POST'])
    @requires_auth
    def upload_finish(upload_id):
        _check_enabled()
        with _lock:
            meta = _load(upload_id)
            if meta['status'] != 'uploading':
                return jsonify(meta)
            if meta['received'] != meta['size'] or os.path.getsize(_data_path(upload_id)) != meta['size']:
                return jsonify({'error': 'incomplete', 'received': meta['received']}), 409
            # 按内容再确认一次是音视频(扩展名可以乱写)
            try:
                import magic
                mime = magic.from_file(_data_path(upload_id), mime=True)
            except Exception:
                mime = ''
            if mime and not (mime.startswith('audio/') or mime.startswith('video/')
                             or mime == 'application/octet-stream'):
                meta.update(status='error', error=f'not an audio/video file ({mime})')
                _save(meta)
                os.remove(_data_path(upload_id))
                return jsonify(meta), 415
            meta['status'] = 'processing'
            _save(meta)
        threading.Thread(target=finalize, args=(upload_id,), name="Upload-" + upload_id[:7],
                         daemon=True).start()
        return jsonify(meta)

    @api.route('/<upload_id>', methods=['GET'])
    @requires_auth
    def upload_status(upload_id):
        return jsonify(_load(upload_id))

    @api.route('/<upload_id>', methods=['DELETE'])
    @requires_auth
    def upload_cancel(upload_id):
        meta = _load(upload_id)
        for path in (_data_path(upload_id), _meta_path(upload_id)):
            try:
                os.remove(path)
            except OSError:
                pass
        return jsonify({'id': meta['id'], 'status': 'cancelled'})

    return api
