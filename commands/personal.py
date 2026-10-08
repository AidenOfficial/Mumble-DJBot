# coding=utf-8
"""个人歌单相关的聊天命令:Mumble 账号与 Web 身份绑定后,在聊天里也能用自己的歌单。

  !bind <6位码>      在 Web 右上角头像里生成绑定码,发给 bot 完成绑定(建议私聊发)
  !unbind            解除绑定
  !mylist            列出我的歌单
  !mylist <名字|序号> [shuffle]   把歌单加进播放队列
  !fav [歌单名]       把当前这首加入歌单(默认 "Favorites",没有就自动建)
"""
import re
import time

from constants import tr_cli as tr
import variables as var
import web_users

from ._shared import log

FAV_DEFAULT = 'Favorites'
MAX_FAILURES = 5
LOCKOUT_SECONDS = 600
_failures = {}  # mumble_key -> [失败时间戳]


def mumble_key(bot, text):
    """Mumble 账号的稳定标识:注册用户用 user_id,否则用证书指纹,最后才退回名字。"""
    try:
        user = bot.mumble.users[text.actor]
    except Exception:
        return None, ''
    name = getattr(user, 'name', '') or ''
    if getattr(user, 'user_id', None):
        return f"uid:{user.user_id}", name
    if getattr(user, 'cert_hash', None):
        return f"cert:{user.cert_hash}", name
    return f"name:{name}", name


def _identity(bot, text):
    key, name = mumble_key(bot, text)
    if not key or var.user_db is None:
        return None
    return var.user_db.identity_for_mumble(key, name)


def _require_identity(bot, text):
    identity = _identity(bot, text)
    if identity is None:
        bot.send_msg(tr('bind_required'), text)
    return identity


def _locked(key, now):
    recent = [t for t in _failures.get(key, []) if now - t < LOCKOUT_SECONDS]
    _failures[key] = recent
    return len(recent) >= MAX_FAILURES


def cmd_bind(bot, user, text, command, parameter):
    code = re.sub(r'\D', '', parameter or '')
    if len(code) != 6:
        bot.send_msg(tr('bind_usage'), text)
        return
    key, name = mumble_key(bot, text)
    if not key or var.user_db is None:
        bot.send_msg(tr('bind_failed'), text)
        return
    now = time.time()
    if _locked(key, now):
        bot.send_msg(tr('bind_locked'), text)
        return
    identity = var.user_db.consume_bind_code(code, key, name)
    if identity is None:
        _failures.setdefault(key, []).append(now)
        bot.send_msg(tr('bind_failed'), text)
        return
    _failures.pop(key, None)
    # 还没设别名的话,用 Mumble 名当别名:这样聊天点歌和网页点歌显示的是同一个名字
    if not var.user_db.get_alias(identity):
        try:
            var.user_db.set_alias(identity, web_users._validate_alias_text(name))
        except Exception:
            pass
    log.info("cmd: %s (%s) bound to web identity %s", name, key, identity)
    bot.send_msg(tr('bind_success', account=identity.replace('user:', '')), text)


def cmd_unbind(bot, user, text, command, parameter):
    key, _ = mumble_key(bot, text)
    if key and var.user_db is not None and var.user_db.unlink_mumble(key):
        bot.send_msg(tr('unbind_success'), text)
    else:
        bot.send_msg(tr('bind_required'), text)


def _find_playlist(playlists, query):
    query = query.strip()
    if query.isdigit() and 1 <= int(query) <= len(playlists):
        return playlists[int(query) - 1]
    lowered = query.lower()
    for pl in playlists:
        if pl['name'].lower() == lowered:
            return pl
    matches = [pl for pl in playlists if pl['name'].lower().startswith(lowered)]
    return matches[0] if len(matches) == 1 else None


def cmd_my_playlist(bot, user, text, command, parameter):
    identity = _require_identity(bot, text)
    if identity is None:
        return
    playlists = var.user_db.list_playlists(identity)
    parameter = (parameter or '').strip()
    if not parameter:
        if not playlists:
            bot.send_msg(tr('mylist_empty'), text)
            return
        lines = [f"{i}. <b>{pl['name']}</b> ({pl['count']})" for i, pl in enumerate(playlists, 1)]
        bot.send_msg(tr('mylist_header') + '<br>' + '<br>'.join(lines), text)
        return

    shuffle = False
    m = re.match(r'^(.*?)\s+(shuffle|random|随机|シャッフル)$', parameter, flags=re.IGNORECASE)
    if m:
        parameter, shuffle = m.group(1), True
    pl = _find_playlist(playlists, parameter)
    if pl is None:
        bot.send_msg(tr('mylist_not_found', name=parameter), text)
        return
    entries = var.user_db.get_playlist(identity, pl['id'])['items']
    if shuffle:
        import random
        random.shuffle(entries)
    wrappers = [w for w in (web_users.wrapper_from_entry(e, user) for e in entries) if w]
    if not wrappers:
        bot.send_msg(tr('mylist_empty_playlist', name=pl['name']), text)
        return
    var.playlist.extend(wrappers)
    bot.async_download_next()
    bot.send_msg(tr('mylist_queued', name=pl['name'], count=len(wrappers)), text)


def cmd_favorite(bot, user, text, command, parameter):
    identity = _require_identity(bot, text)
    if identity is None:
        return
    if len(var.playlist) == 0 or not var.playlist.current_item():
        bot.send_msg(tr('queue_empty'), text)
        return
    name = (parameter or '').strip()[:web_users.NAME_MAX_LEN] or FAV_DEFAULT
    playlists = var.user_db.list_playlists(identity)
    target = next((pl for pl in playlists if pl['name'].lower() == name.lower()), None)
    try:
        playlist_id = target['id'] if target else var.user_db.create_playlist(identity, name)
    except ValueError:
        bot.send_msg(tr('mylist_too_many'), text)
        return
    current = var.playlist.current_item()
    entry = web_users.entry_from_item(current.item())
    added = var.user_db.add_items(identity, playlist_id, [entry])
    key = 'fav_added' if added else 'fav_exists'
    bot.send_msg(tr(key, song=current.format_title(), name=target['name'] if target else name), text)
