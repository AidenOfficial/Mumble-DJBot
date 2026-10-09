"""Web 用户身份(Cloudflare Access 邮箱 + 别名)与个人歌单。

身份来源(按优先级):
  1. Cloudflare Access 回源时带的邮箱头(默认 Cf-Access-Authenticated-User-Email);
     配置了 access_team_domain + access_aud 时,改为校验 Cf-Access-Jwt-Assertion
     JWT 并从中取邮箱,校验失败直接 403(防止有人绕过 Tunnel 伪造请求头)。
  2. auth_method = password / token 时的 Web 用户名。
  3. 都没有 → 匿名(可以点歌,但没有个人歌单)。

身份键:邮箱(小写)或 "user:<用户名>"。别名、歌单都挂在身份键上。
数据存在 settings 数据库文件里(save_music_library=False 时 music_db 是
:memory:,不能用),表在首次使用时创建。
"""

import hashlib
import logging
import re
import sqlite3
import threading
import time

from flask import Blueprint, abort, g, jsonify, request

import util
import variables as var

log = logging.getLogger("bot")

ALIAS_MAX_LEN = 24
NAME_MAX_LEN = 60
MAX_PLAYLISTS_PER_USER = 50
MAX_ITEMS_PER_PLAYLIST = 1000
BIND_CODE_TTL = 600
# 别名会被拼进 Mumble 的 HTML 消息里,禁止尖括号等标记字符
_ALIAS_RE = re.compile(r'^[^<>&"\'\x00-\x1f]+$')


# =====================================================================
#  身份解析
# =====================================================================

class Identity:
    def __init__(self, key=None, email=None, source='anonymous', fallback_name='Remote Control'):
        self.key = key
        self.email = email
        self.source = source
        self.fallback_name = fallback_name
        self.alias = None

    @property
    def display_name(self):
        if self.alias:
            return self.alias
        if self.email:
            return self.email.split('@', 1)[0]
        return self.fallback_name

    def to_dict(self):
        return {
            'identity': self.key,
            'email': self.email,
            'alias': self.alias or '',
            'display_name': self.display_name,
            'source': self.source,
            'can_have_playlists': self.key is not None,
        }


_jwks_lock = threading.Lock()
_jwks_clients = {}


def _access_jwt_email(token):
    """校验 Cloudflare Access JWT,返回其中的邮箱;失败抛异常。"""
    import jwt  # PyJWT,只在启用 JWT 校验时才需要

    team = var.config.get('webinterface', 'access_team_domain', fallback='').strip()
    aud = var.config.get('webinterface', 'access_aud', fallback='').strip()
    team = re.sub(r'^https?://', '', team).rstrip('/')
    certs_url = f"https://{team}/cdn-cgi/access/certs"
    with _jwks_lock:
        client = _jwks_clients.get(certs_url)
        if client is None:
            client = jwt.PyJWKClient(certs_url, cache_keys=True)
            _jwks_clients[certs_url] = client
    key = client.get_signing_key_from_jwt(token).key
    claims = jwt.decode(token, key, algorithms=['RS256'], audience=aud,
                        issuer=f"https://{team}")
    return claims.get('email')


def jwt_verification_enabled():
    return bool(var.config.get('webinterface', 'access_team_domain', fallback='').strip()
                and var.config.get('webinterface', 'access_aud', fallback='').strip())


def resolve_identity(auth_user=None):
    """根据当前请求得出身份。auth_user 是 password/token 鉴权得到的用户名。
    启用了 JWT 校验但请求没带合法 JWT 时 abort(403)。"""
    email = None
    source = 'anonymous'
    if jwt_verification_enabled():
        token = request.headers.get('Cf-Access-Jwt-Assertion', '')
        if not token:
            abort(403)
        try:
            email = _access_jwt_email(token)
        except Exception as e:
            log.warning("web: rejected Cloudflare Access JWT from %s: %s", request.remote_addr, e)
            abort(403)
        source = 'cloudflare-jwt'
    else:
        header = var.config.get('webinterface', 'access_email_header',
                                fallback='Cf-Access-Authenticated-User-Email').strip()
        if header:
            email = request.headers.get(header) or None
            if email:
                source = 'cloudflare'

    if email:
        email = email.strip().lower()
        ident = Identity(key=email, email=email, source=source)
    elif auth_user:
        ident = Identity(key=f"user:{auth_user}", source='web-user', fallback_name=auth_user)
    else:
        ident = Identity()

    if ident.key and var.user_db is not None:
        try:
            ident.alias = var.user_db.touch(ident.key, ident.email)
        except Exception:
            log.debug("web: could not load alias for %s", ident.key, exc_info=True)
    return ident


def current_identity():
    ident = getattr(g, 'web_identity', None)
    return ident if ident is not None else Identity()


def current_user_name():
    """点歌人名字:有别名用别名,否则邮箱前缀 / Web 用户名 / 'Remote Control'。"""
    return current_identity().display_name


# =====================================================================
#  数据库
# =====================================================================

class AliasTakenError(Exception):
    pass


class UserDatabase:
    def __init__(self, db_path):
        self.db_path = db_path
        with self._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS web_user ("
                "identity TEXT PRIMARY KEY,"
                "email TEXT,"
                "alias TEXT,"
                "created_at REAL NOT NULL,"
                "last_seen REAL NOT NULL)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_web_user_alias "
                         "ON web_user (alias COLLATE NOCASE) WHERE alias IS NOT NULL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS user_playlist ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "owner TEXT NOT NULL,"
                "name TEXT NOT NULL,"
                "created_at REAL NOT NULL,"
                "updated_at REAL NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_playlist_owner ON user_playlist (owner)")
            # 公开歌单:出现在其他人的歌单页(只读,可播放 / 收藏单曲 / 复制一份)
            columns = {r[1] for r in conn.execute("PRAGMA table_info(user_playlist)")}
            if 'public' not in columns:
                conn.execute("ALTER TABLE user_playlist ADD COLUMN public INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS user_playlist_item ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "playlist_id INTEGER NOT NULL,"
                "position INTEGER NOT NULL,"
                "item_id TEXT NOT NULL,"
                "type TEXT NOT NULL,"
                "title TEXT NOT NULL DEFAULT '',"
                "ref TEXT NOT NULL DEFAULT '',"
                "duration REAL NOT NULL DEFAULT 0,"
                "added_at REAL NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_playlist_item_pl "
                         "ON user_playlist_item (playlist_id, position)")
            # Mumble 账号 <-> Web 身份。一个 Web 身份只绑一个 Mumble 账号
            conn.execute(
                "CREATE TABLE IF NOT EXISTS mumble_link ("
                "mumble_key TEXT PRIMARY KEY,"
                "identity TEXT NOT NULL UNIQUE,"
                "mumble_name TEXT NOT NULL DEFAULT '',"
                "linked_at REAL NOT NULL)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS bind_code ("
                "code TEXT PRIMARY KEY,"
                "identity TEXT NOT NULL,"
                "expires_at REAL NOT NULL)")

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return _Closing(conn)

    # ---- 用户 / 别名 -------------------------------------------------------

    def touch(self, identity, email=None):
        """登记/刷新用户的最后访问时间,返回别名(没有则 None)。
        last_seen 每分钟最多写一次,避免每个轮询请求都写库。"""
        now = time.time()
        with self._conn() as conn:
            row = conn.execute("SELECT alias, last_seen FROM web_user WHERE identity=?",
                               (identity,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO web_user (identity, email, alias, created_at, last_seen) "
                             "VALUES (?, ?, NULL, ?, ?)", (identity, email, now, now))
                return None
            if now - row['last_seen'] > 60:
                conn.execute("UPDATE web_user SET last_seen=?, email=COALESCE(?, email) "
                             "WHERE identity=?", (now, email, identity))
            return row['alias']

    def get_alias(self, identity):
        with self._conn() as conn:
            row = conn.execute("SELECT alias FROM web_user WHERE identity=?", (identity,)).fetchone()
            return row['alias'] if row else None

    def set_alias(self, identity, alias, email=None):
        """alias 为空串/None 表示清除。重名(忽略大小写)抛 AliasTakenError。"""
        alias = alias or None
        now = time.time()
        with self._conn() as conn:
            if alias:
                row = conn.execute("SELECT identity FROM web_user WHERE alias=? COLLATE NOCASE "
                                   "AND identity != ?", (alias, identity)).fetchone()
                if row:
                    raise AliasTakenError(alias)
            conn.execute("INSERT INTO web_user (identity, email, alias, created_at, last_seen) "
                         "VALUES (?, ?, ?, ?, ?) "
                         "ON CONFLICT(identity) DO UPDATE SET alias=excluded.alias, "
                         "last_seen=excluded.last_seen", (identity, email, alias, now, now))

    # ---- Mumble 绑定 ----------------------------------------------------------

    def create_bind_code(self, identity, ttl=BIND_CODE_TTL):
        import secrets

        now = time.time()
        with self._conn() as conn:
            conn.execute("DELETE FROM bind_code WHERE identity=? OR expires_at<?", (identity, now))
            for _ in range(20):
                code = f"{secrets.randbelow(1000000):06d}"
                try:
                    conn.execute("INSERT INTO bind_code (code, identity, expires_at) VALUES (?, ?, ?)",
                                 (code, identity, now + ttl))
                    return code, now + ttl
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError('could not allocate a bind code')

    def consume_bind_code(self, code, mumble_key, mumble_name):
        """校验并消耗绑定码(一次性)。成功返回 identity,否则 None。"""
        now = time.time()
        with self._conn() as conn:
            row = conn.execute("SELECT identity FROM bind_code WHERE code=? AND expires_at>=?",
                               (code, now)).fetchone()
            if row is None:
                return None
            identity = row['identity']
            conn.execute("DELETE FROM bind_code WHERE code=?", (code,))
            conn.execute("DELETE FROM mumble_link WHERE identity=? OR mumble_key=?", (identity, mumble_key))
            conn.execute("INSERT INTO mumble_link (mumble_key, identity, mumble_name, linked_at) "
                         "VALUES (?, ?, ?, ?)", (mumble_key, identity, mumble_name, now))
            return identity

    def identity_for_mumble(self, mumble_key, mumble_name=None):
        """按 Mumble 账号找 Web 身份;顺便更新记住的名字(改名后统计合并用)。"""
        with self._conn() as conn:
            row = conn.execute("SELECT identity, mumble_name FROM mumble_link WHERE mumble_key=?",
                               (mumble_key,)).fetchone()
            if row is None:
                return None
            if mumble_name and row['mumble_name'] != mumble_name:
                conn.execute("UPDATE mumble_link SET mumble_name=? WHERE mumble_key=?", (mumble_name, mumble_key))
            return row['identity']

    def mumble_link_for(self, identity):
        with self._conn() as conn:
            row = conn.execute("SELECT mumble_name, linked_at FROM mumble_link WHERE identity=?",
                               (identity,)).fetchone()
            return dict(row) if row else None

    def unlink_identity(self, identity):
        with self._conn() as conn:
            return conn.execute("DELETE FROM mumble_link WHERE identity=?", (identity,)).rowcount > 0

    def unlink_mumble(self, mumble_key):
        with self._conn() as conn:
            return conn.execute("DELETE FROM mumble_link WHERE mumble_key=?", (mumble_key,)).rowcount > 0

    def requester_display_names(self):
        """{Mumble 名: Web 显示名}:统计页把同一个人在聊天和 Web 上的点歌合并。"""
        with self._conn() as conn:
            rows = conn.execute("SELECT l.mumble_name, u.alias, u.email FROM mumble_link l "
                                "LEFT JOIN web_user u ON u.identity = l.identity").fetchall()
        names = {}
        for r in rows:
            display = r['alias'] or (r['email'].split('@', 1)[0] if r['email'] else None)
            if r['mumble_name'] and display:
                names[r['mumble_name']] = display
        return names

    # ---- 歌单 ---------------------------------------------------------------

    def list_playlists(self, owner):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT p.id, p.name, p.created_at, p.updated_at, p.public, "
                "COUNT(i.id) AS count, COALESCE(SUM(i.duration), 0) AS total_duration "
                "FROM user_playlist p LEFT JOIN user_playlist_item i ON i.playlist_id = p.id "
                "WHERE p.owner=? GROUP BY p.id ORDER BY p.updated_at DESC", (owner,)).fetchall()
            return [_playlist_row(r) for r in rows]

    def list_public_playlists(self, exclude_owner=None):
        """其他人公开的歌单(空歌单不列),带上主人的显示名;不暴露邮箱。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT p.id, p.name, p.created_at, p.updated_at, p.public, p.owner, "
                "u.alias, u.email, "
                "COUNT(i.id) AS count, COALESCE(SUM(i.duration), 0) AS total_duration "
                "FROM user_playlist p "
                "JOIN user_playlist_item i ON i.playlist_id = p.id "
                "LEFT JOIN web_user u ON u.identity = p.owner "
                "WHERE p.public = 1 AND p.owner != ? "
                "GROUP BY p.id ORDER BY p.updated_at DESC", (exclude_owner or '',)).fetchall()
            return [_playlist_row(r, with_owner=True) for r in rows]

    def get_playlist(self, owner, playlist_id):
        """主人视角:只能拿到自己的歌单。"""
        return self._get(playlist_id, owner=owner)

    def get_visible_playlist(self, viewer, playlist_id):
        """自己的,或别人公开的;都不是返回 None。结果带 mine / owner_name。"""
        return self._get(playlist_id, viewer=viewer)

    def _get(self, playlist_id, owner=None, viewer=None):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT p.id, p.name, p.created_at, p.updated_at, p.public, p.owner, u.alias, u.email "
                "FROM user_playlist p LEFT JOIN web_user u ON u.identity = p.owner WHERE p.id=?",
                (playlist_id,)).fetchone()
            if row is None:
                return None
            mine = row['owner'] == (owner if owner is not None else viewer)
            if owner is not None and not mine:
                return None
            if owner is None and not mine and not row['public']:
                return None
            items = conn.execute(
                "SELECT id, item_id, type, title, ref, duration, added_at FROM user_playlist_item "
                "WHERE playlist_id=? ORDER BY position, id", (playlist_id,)).fetchall()
            result = _playlist_row(row, with_owner=not mine)
            result['mine'] = mine
            result['items'] = [dict(i) for i in items]
            return result

    def create_playlist(self, owner, name):
        now = time.time()
        with self._conn() as conn:
            (count,) = conn.execute("SELECT COUNT(*) FROM user_playlist WHERE owner=?",
                                    (owner,)).fetchone()
            if count >= MAX_PLAYLISTS_PER_USER:
                raise ValueError('too many playlists')
            cur = conn.execute("INSERT INTO user_playlist (owner, name, created_at, updated_at) "
                               "VALUES (?, ?, ?, ?)", (owner, name, now, now))
            return cur.lastrowid

    def set_public(self, owner, playlist_id, public):
        with self._conn() as conn:
            cur = conn.execute("UPDATE user_playlist SET public=?, updated_at=? WHERE id=? AND owner=?",
                               (1 if public else 0, time.time(), playlist_id, owner))
            return cur.rowcount > 0

    def rename_playlist(self, owner, playlist_id, name):
        with self._conn() as conn:
            cur = conn.execute("UPDATE user_playlist SET name=?, updated_at=? WHERE id=? AND owner=?",
                               (name, time.time(), playlist_id, owner))
            return cur.rowcount > 0

    def delete_playlist(self, owner, playlist_id):
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM user_playlist WHERE id=? AND owner=?", (playlist_id, owner))
            if cur.rowcount:
                conn.execute("DELETE FROM user_playlist_item WHERE playlist_id=?", (playlist_id,))
            return cur.rowcount > 0

    def _owns(self, conn, owner, playlist_id):
        return conn.execute("SELECT 1 FROM user_playlist WHERE id=? AND owner=?",
                            (playlist_id, owner)).fetchone() is not None

    def add_items(self, owner, playlist_id, entries):
        """entries: [{item_id, type, title, ref, duration}]。同一首歌在同一歌单里只保留一份。
        返回实际新增条数;歌单不存在返回 None。"""
        now = time.time()
        with self._conn() as conn:
            if not self._owns(conn, owner, playlist_id):
                return None
            existing = {r[0] for r in conn.execute(
                "SELECT item_id FROM user_playlist_item WHERE playlist_id=?", (playlist_id,))}
            (pos,) = conn.execute("SELECT COALESCE(MAX(position), -1) FROM user_playlist_item "
                                  "WHERE playlist_id=?", (playlist_id,)).fetchone()
            added = 0
            for e in entries:
                if e['item_id'] in existing:
                    continue
                if len(existing) >= MAX_ITEMS_PER_PLAYLIST:
                    break
                pos += 1
                conn.execute(
                    "INSERT INTO user_playlist_item (playlist_id, position, item_id, type, title, ref, "
                    "duration, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (playlist_id, pos, e['item_id'], e['type'], e.get('title') or '',
                     e.get('ref') or '', e.get('duration') or 0, now))
                existing.add(e['item_id'])
                added += 1
            conn.execute("UPDATE user_playlist SET updated_at=? WHERE id=?", (now, playlist_id))
            return added

    def remove_item(self, owner, playlist_id, row_id):
        with self._conn() as conn:
            if not self._owns(conn, owner, playlist_id):
                return False
            cur = conn.execute("DELETE FROM user_playlist_item WHERE id=? AND playlist_id=?",
                               (row_id, playlist_id))
            conn.execute("UPDATE user_playlist SET updated_at=? WHERE id=?", (time.time(), playlist_id))
            return cur.rowcount > 0

    def move_item(self, owner, playlist_id, src, dst):
        """按当前顺序把第 src 条移到第 dst 条,然后重排 position。"""
        with self._conn() as conn:
            if not self._owns(conn, owner, playlist_id):
                return False
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM user_playlist_item WHERE playlist_id=? ORDER BY position, id",
                (playlist_id,))]
            if not (0 <= src < len(ids) and 0 <= dst < len(ids)):
                return False
            ids.insert(dst, ids.pop(src))
            conn.executemany("UPDATE user_playlist_item SET position=? WHERE id=?",
                             [(i, row_id) for i, row_id in enumerate(ids)])
            conn.execute("UPDATE user_playlist SET updated_at=? WHERE id=?", (time.time(), playlist_id))
            return True


def owner_display_name(identity, alias=None, email=None):
    """歌单主人的显示名:别名 > 邮箱前缀 > Web 用户名。不返回完整邮箱。"""
    if alias:
        return alias
    if email:
        return email.split('@', 1)[0]
    if identity and identity.startswith('user:'):
        return identity[5:]
    return (identity or '?').split('@', 1)[0]


def _playlist_row(row, with_owner=False):
    data = {k: row[k] for k in ('id', 'name', 'created_at', 'updated_at') if k in row.keys()}
    for k in ('count', 'total_duration'):
        if k in row.keys():
            data[k] = row[k]
    data['public'] = bool(row['public'])
    if with_owner:
        data['owner_name'] = owner_display_name(row['owner'], row['alias'], row['email'])
    return data


class _Closing:
    """sqlite3 连接的 with 包装:成功提交、异常回滚,最后总是关闭。"""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, *_):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()


# =====================================================================
#  队列条目 <-> 歌单条目
# =====================================================================

URL_TYPES = ('url', 'url_from_playlist')


def entry_from_item(item):
    """把缓存里的媒体条目快照成歌单条目。只存重建所需的最少信息:
    url 类型存链接,本地文件存相对路径。"""
    item_type = getattr(item, 'type', '')
    if item_type in URL_TYPES or item_type in ('radio', 'livestream'):
        ref = getattr(item, 'url', '') or ''
    else:
        ref = getattr(item, 'path', '') or ''
    if item_type == 'url_from_playlist':
        item_type = 'url'
        item_id = hashlib.md5(ref.encode()).hexdigest()  # 与 url 条目同一 id
    else:
        item_id = item.id
    return {
        'item_id': item_id,
        'type': item_type,
        'title': item.format_title() if hasattr(item, 'format_title') else getattr(item, 'title', ''),
        'ref': ref,
        'duration': getattr(item, 'duration', 0) or 0,
    }


def entry_from_url(url, title='', duration=0):
    return {
        'item_id': hashlib.md5(url.encode()).hexdigest(),
        'type': 'url',
        'title': title or url,
        'ref': url,
        'duration': duration or 0,
    }


def wrapper_from_entry(entry, user):
    """歌单条目 -> 可放进播放队列的 CachedItemWrapper;重建失败返回 None。"""
    from media.cache import get_cached_wrapper_by_id, get_cached_wrapper_from_scrap

    entry_type, ref = entry['type'], entry['ref']
    try:
        if entry_type == 'url' and ref:
            return get_cached_wrapper_from_scrap(type='url', url=ref, user=user)
        if entry_type == 'radio' and ref:
            return get_cached_wrapper_from_scrap(type='radio', url=ref, name=entry.get('title', ''),
                                                 user=user)
        if entry_type == 'livestream' and ref:
            return get_cached_wrapper_from_scrap(type='livestream', url=ref,
                                                 title=entry.get('title', ''), user=user)
        wrapper = get_cached_wrapper_by_id(entry['item_id'], user)
        if wrapper:
            return wrapper
        if entry_type == 'file' and ref:
            return get_cached_wrapper_from_scrap(type='file', path=ref, user=user)
    except Exception:
        log.warning("web: could not rebuild playlist entry %s", entry.get('title'), exc_info=True)
    return None


# =====================================================================
#  API
# =====================================================================

def _validate_alias_text(alias):
    """不合法抛 ValueError;空串表示清除别名。"""
    alias = (alias or '').strip()
    if alias and (len(alias) > ALIAS_MAX_LEN or not _ALIAS_RE.match(alias)):
        raise ValueError(alias)
    return alias


def _validate_alias(alias):
    try:
        return _validate_alias_text(alias)
    except ValueError:
        abort(400)


def _validate_name(name):
    name = (name or '').strip()
    if not name or len(name) > NAME_MAX_LEN:
        abort(400)
    return name


def _payload():
    return request.get_json(silent=True) or request.form or {}


def _owner():
    ident = current_identity()
    if not ident.key or var.user_db is None:
        abort(403)
    return ident.key


def _visible(playlist_id):
    """自己的歌单或别人公开的歌单(只读);都不是就 404。"""
    if var.user_db is None:
        abort(404)
    playlist = var.user_db.get_visible_playlist(current_identity().key, playlist_id)
    if playlist is None:
        abort(404)
    return playlist


def _enqueue(wrappers, mode):
    """mode: append(加到队尾) / next(插到当前曲之后) / replace(清空队列后立即播放)。"""
    if not wrappers:
        return
    if mode == 'replace':
        # clear() 会 var.cache.free_all(),把刚为这些 wrapper 注册的条目也清掉,
        # 队列里就全是悬空引用(ItemNotCachedError,一首首被跳过)。
        # 先取出条目,清空后再放回缓存。
        items = [w.item() for w in wrappers]
        var.bot.clear()
        for item in items:
            var.cache[item.id] = item
        var.playlist.extend(wrappers)
        # 不走 resume():current_index 为 -1 时它会先 next() 一次,
        # 主循环再 next() 一次,第一首就被跳过了
        var.bot.is_pause = False
    elif mode == 'next':
        index = var.playlist.current_index + 1
        for offset, wrapper in enumerate(wrappers):
            var.playlist.insert(index + offset, wrapper)
    else:
        var.playlist.extend(wrappers)
    # 与旧接口一致:成了"下一首"的话立刻开始预下载
    try:
        var.bot.async_download_next()
    except Exception:
        log.debug("web: async_download_next failed", exc_info=True)


def create_blueprint(requires_auth):
    api = Blueprint('users', __name__, url_prefix='/api')

    def _me_payload(ident):
        data = ident.to_dict()
        data['jwt_verified'] = data['source'] == 'cloudflare-jwt'
        data['mumble'] = var.user_db.mumble_link_for(ident.key) if ident.key and var.user_db else None
        import web_logs
        data['can_view_logs'] = web_logs.can_view()
        return data

    @api.route('/me', methods=['GET'])
    @requires_auth
    def me():
        return jsonify(_me_payload(current_identity()))

    @api.route('/me/bind', methods=['POST'])
    @requires_auth
    def create_bind():
        """生成 6 位一次性绑定码,10 分钟有效;在 Mumble 里发 !bind <码> 完成绑定。"""
        ident = current_identity()
        if not ident.key or var.user_db is None:
            abort(403)
        code, expires_at = var.user_db.create_bind_code(ident.key)
        symbol = var.config.get('commands', 'command_symbol', fallback='!')[:1] or '!'
        name = var.config.get('commands', 'bind', fallback='bind').split(',')[0].strip()
        return jsonify({'code': code, 'expires_at': expires_at, 'command': f"{symbol}{name} {code}"})

    @api.route('/me/bind', methods=['DELETE'])
    @requires_auth
    def remove_bind():
        ident = current_identity()
        if not ident.key or var.user_db is None:
            abort(403)
        var.user_db.unlink_identity(ident.key)
        return jsonify(_me_payload(ident))

    @api.route('/me', methods=['POST'])
    @requires_auth
    def set_me():
        ident = current_identity()
        if not ident.key or var.user_db is None:
            abort(403)
        alias = _validate_alias(_payload().get('alias'))
        try:
            var.user_db.set_alias(ident.key, alias, ident.email)
        except AliasTakenError:
            return jsonify({'error': 'alias_taken'}), 409
        ident.alias = alias or None
        log.info("web: %s set alias to %r", ident.key, alias)
        return jsonify(_me_payload(ident))

    @api.route('/playlists', methods=['GET'])
    @requires_auth
    def playlists():
        return jsonify({'playlists': var.user_db.list_playlists(_owner())})

    @api.route('/playlists/public', methods=['GET'])
    @requires_auth
    def public_playlists():
        """其他人公开的歌单;访客(没有身份)也能看、能播放。"""
        if var.user_db is None:
            return jsonify({'playlists': []})
        return jsonify({'playlists': var.user_db.list_public_playlists(current_identity().key)})

    @api.route('/playlists', methods=['POST'])
    @requires_auth
    def create_playlist():
        owner = _owner()
        name = _validate_name(_payload().get('name'))
        try:
            playlist_id = var.user_db.create_playlist(owner, name)
        except ValueError:
            return jsonify({'error': 'too_many_playlists'}), 409
        return jsonify(var.user_db.get_playlist(owner, playlist_id))

    @api.route('/playlists/import', methods=['POST'])
    @requires_auth
    def import_playlist():
        """Body: {url, playlist_id?(导入到已有歌单), name?(新歌单名,默认用来源标题)}
        支持 YouTube 播放列表、网易云音乐 / QQ 音乐歌单、Spotify 歌单/专辑。"""
        import playlist_import

        owner = _owner()
        payload = _payload()
        url = (payload.get('url') or '').strip()
        if not url.lower().startswith(('http://', 'https://')) or len(url) > 2048:
            abort(400)
        if playlist_import.detect_source(url) is None:
            return jsonify({'error': 'unsupported_source'}), 400
        playlist_id = payload.get('playlist_id')
        if playlist_id is not None:
            try:
                playlist_id = int(playlist_id)
            except (TypeError, ValueError):
                abort(400)
            if var.user_db.get_playlist(owner, playlist_id) is None:
                abort(404)
        name = (payload.get('name') or '').strip()[:NAME_MAX_LEN] or None
        return jsonify({'job_id': playlist_import.start_import(owner, url, playlist_id, name)})

    @api.route('/playlists/import/<job_id>', methods=['GET'])
    @requires_auth
    def import_status(job_id):
        import playlist_import

        job = playlist_import.get_job(job_id)
        if job is None or job['owner'] != _owner():
            abort(404)
        return jsonify({k: v for k, v in job.items() if k != 'owner'})

    @api.route('/playlists/<int:playlist_id>', methods=['GET'])
    @requires_auth
    def get_playlist(playlist_id):
        return jsonify(_visible(playlist_id))

    @api.route('/playlists/<int:playlist_id>/visibility', methods=['POST'])
    @requires_auth
    def set_visibility(playlist_id):
        owner = _owner()
        public = bool(_payload().get('public'))
        if not var.user_db.set_public(owner, playlist_id, public):
            abort(404)
        playlist = var.user_db.get_playlist(owner, playlist_id)
        log.info("web: %s made playlist %r %s", current_user_name(), playlist['name'],
                 'public' if public else 'private')
        return jsonify(playlist)

    @api.route('/playlists/<int:playlist_id>/copy', methods=['POST'])
    @requires_auth
    def copy_playlist(playlist_id):
        """把(别人公开的或自己的)歌单复制一份到我的歌单里,副本默认私人。"""
        owner = _owner()
        source = _visible(playlist_id)
        name = (_payload().get('name') or '').strip()[:NAME_MAX_LEN] or source['name']
        try:
            new_id = var.user_db.create_playlist(owner, name)
        except ValueError:
            return jsonify({'error': 'too_many_playlists'}), 409
        var.user_db.add_items(owner, new_id, source['items'])
        log.info("web: %s copied playlist %r (%d)", current_user_name(), source['name'], playlist_id)
        return jsonify(var.user_db.get_playlist(owner, new_id))

    @api.route('/playlists/<int:playlist_id>', methods=['DELETE'])
    @requires_auth
    def delete_playlist(playlist_id):
        if not var.user_db.delete_playlist(_owner(), playlist_id):
            abort(404)
        return jsonify({'ok': True})

    @api.route('/playlists/<int:playlist_id>/rename', methods=['POST'])
    @requires_auth
    def rename_playlist(playlist_id):
        owner = _owner()
        if not var.user_db.rename_playlist(owner, playlist_id, _validate_name(_payload().get('name'))):
            abort(404)
        return jsonify(var.user_db.get_playlist(owner, playlist_id))

    @api.route('/playlists/<int:playlist_id>/items', methods=['POST'])
    @requires_auth
    def add_items(playlist_id):
        """Body: {source: current|queue|library|url, index?, item_id?, url?, title?, duration?,
        provider?(bilibili)}"""
        owner = _owner()
        payload = _payload()
        source = payload.get('source')
        entries = []
        if source == 'current':
            wrapper = var.playlist.current_item() if len(var.playlist) else None
            if not wrapper:
                abort(400)
            entries.append(entry_from_item(wrapper.item()))
        elif source == 'queue':
            try:
                index = int(payload.get('index', -1))
            except (TypeError, ValueError):
                abort(400)
            if not 0 <= index < len(var.playlist):
                abort(400)
            entries.append(entry_from_item(var.playlist[index].item()))
        elif source == 'all_queue':
            for wrapper in list(var.playlist):
                try:
                    entries.append(entry_from_item(wrapper.item()))
                except Exception:
                    continue
        elif source == 'library':
            item = var.cache.get_item_by_id(str(payload.get('item_id', '')))
            if not item:
                abort(404)
            entries.append(entry_from_item(item))
        elif source == 'playlist_entry':
            # 从(别人公开的)歌单里收藏一首
            try:
                src_id, row_id = int(payload.get('playlist_id')), int(payload.get('entry'))
            except (TypeError, ValueError):
                abort(400)
            src = _visible(src_id)
            entries = [e for e in src['items'] if e['id'] == row_id]
            if not entries:
                abort(404)
        elif source == 'url':
            url = (payload.get('url') or '').strip()
            if payload.get('provider') == 'bilibili':
                url = util.get_bilibili_url_from_input(payload.get('id') or url)
            if not url or not url.lower().startswith(('http://', 'https://')):
                abort(400)
            try:
                duration = float(payload.get('duration') or 0)
            except (TypeError, ValueError):
                duration = 0
            entries.append(entry_from_url(url, str(payload.get('title') or '')[:300], duration))
        else:
            abort(400)
        added = var.user_db.add_items(owner, playlist_id, entries)
        if added is None:
            abort(404)
        return jsonify({'added': added, 'playlist': var.user_db.get_playlist(owner, playlist_id)})

    @api.route('/playlists/<int:playlist_id>/items/<int:row_id>', methods=['DELETE'])
    @requires_auth
    def remove_item(playlist_id, row_id):
        owner = _owner()
        if not var.user_db.remove_item(owner, playlist_id, row_id):
            abort(404)
        return jsonify(var.user_db.get_playlist(owner, playlist_id))

    @api.route('/playlists/<int:playlist_id>/move', methods=['POST'])
    @requires_auth
    def move_item(playlist_id):
        owner = _owner()
        payload = _payload()
        try:
            src, dst = int(payload.get('index')), int(payload.get('to'))
        except (TypeError, ValueError):
            abort(400)
        if not var.user_db.move_item(owner, playlist_id, src, dst):
            abort(400)
        return jsonify(var.user_db.get_playlist(owner, playlist_id))

    @api.route('/playlists/<int:playlist_id>/play', methods=['POST'])
    @requires_auth
    def play_playlist(playlist_id):
        """Body: {mode: append|next|replace, shuffle?: bool, item?: row id(只放这一首)}"""
        import random
        import web_api

        payload = _payload()
        mode = payload.get('mode', 'append')
        if mode not in ('append', 'next', 'replace'):
            abort(400)
        playlist = _visible(playlist_id)
        entries = playlist['items']
        if payload.get('item') is not None:
            try:
                row_id = int(payload.get('item'))
            except (TypeError, ValueError):
                abort(400)
            entries = [e for e in entries if e['id'] == row_id]
            if not entries:
                abort(404)
        elif payload.get('shuffle'):
            entries = list(entries)
            random.shuffle(entries)
        user = current_user_name()
        wrappers = [w for w in (wrapper_from_entry(e, user) for e in entries) if w]
        _enqueue(wrappers, mode)
        log.info("web: %s queued %d item(s) from playlist %r%s (%s)",
                 user, len(wrappers), playlist['name'],
                 '' if playlist['mine'] else f" by {playlist.get('owner_name')}", mode)
        status = web_api.status_payload()
        status['queued'] = len(wrappers)
        status['skipped'] = len(entries) - len(wrappers)
        return jsonify(status)

    return api
