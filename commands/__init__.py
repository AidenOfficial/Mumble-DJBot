# coding=utf-8
"""Chat command layer: one module per domain, wired by register_all_commands()."""
from constants import commands

from ._shared import _spotify_available  # noqa: F401  (probed by scripts/smoke_test.py)
from .admin import (
    cmd_help,
    cmd_item,
    cmd_joinme,
    cmd_kill,
    cmd_loop_state,
    cmd_real_time_rms,
    cmd_update,
    cmd_url_ban,
    cmd_url_ban_list,
    cmd_url_unban,
    cmd_url_unwhitelist,
    cmd_url_whitelist,
    cmd_url_whitelist_list,
    cmd_user_ban,
    cmd_user_unban,
    cmd_version,
)
from .library import (
    cmd_add_tag,
    cmd_delete_from_library,
    cmd_drop_database,
    cmd_find_tagged,
    cmd_play_tags,
    cmd_refresh_cache,
    cmd_remove_tag,
    cmd_search_library,
    cmd_shortlist,
)
from .playback import (
    cmd_clear,
    cmd_current_music,
    cmd_last,
    cmd_mode,
    cmd_pause,
    cmd_play,
    cmd_queue,
    cmd_random,
    cmd_remove,
    cmd_repeat,
    cmd_skip,
    cmd_stop,
    cmd_stop_and_getout,
)
from .sources import (
    cmd_list_file,
    cmd_play_file,
    cmd_play_file_match,
    cmd_play_playlist,
    cmd_play_radio,
    cmd_play_url,
    cmd_rb_play,
    cmd_rb_query,
    cmd_yt_play,
    cmd_yt_search,
)
from .streaming import cmd_play_bilibili, cmd_play_live, cmd_play_spotify
from .volume import (
    cmd_ducking,
    cmd_ducking_delay,
    cmd_ducking_threshold,
    cmd_ducking_volume,
    cmd_max_volume,
    cmd_volume,
)
from .web import cmd_user_password, cmd_web_access, cmd_web_user_add, cmd_web_user_list, cmd_web_user_remove


def register_all_commands(bot):
    bot.register_command(commands('add_from_shortlist'), cmd_shortlist)
    bot.register_command(commands('add_tag'), cmd_add_tag)
    bot.register_command(commands('change_user_password'), cmd_user_password, no_partial_match=True)
    bot.register_command(commands('clear'), cmd_clear)
    bot.register_command(commands('current_music'), cmd_current_music)
    bot.register_command(commands('delete_from_library'), cmd_delete_from_library)
    bot.register_command(commands('ducking'), cmd_ducking)
    bot.register_command(commands('ducking_delay'), cmd_ducking_delay)
    bot.register_command(commands('ducking_threshold'), cmd_ducking_threshold)
    bot.register_command(commands('ducking_volume'), cmd_ducking_volume)
    bot.register_command(commands('find_tagged'), cmd_find_tagged)
    bot.register_command(commands('help'), cmd_help, no_partial_match=False, access_outside_channel=True)
    bot.register_command(commands('joinme'), cmd_joinme, access_outside_channel=True)
    bot.register_command(commands('last'), cmd_last)
    bot.register_command(commands('list_file'), cmd_list_file)
    bot.register_command(commands('mode'), cmd_mode)
    bot.register_command(commands('pause'), cmd_pause)
    bot.register_command(commands('play'), cmd_play)
    bot.register_command(commands('play_file'), cmd_play_file)
    bot.register_command(commands('play_file_match'), cmd_play_file_match)
    bot.register_command(commands('play_playlist'), cmd_play_playlist)
    bot.register_command(commands('play_radio'), cmd_play_radio)
    bot.register_command(commands('play_tag'), cmd_play_tags)
    bot.register_command(commands('play_url'), cmd_play_url)
    bot.register_command(commands('play_bilibili'), cmd_play_bilibili)
    bot.register_command(commands('play_live'), cmd_play_live)
    bot.register_command(commands('play_spotify'), cmd_play_spotify)
    bot.register_command(commands('queue'), cmd_queue)
    bot.register_command(commands('random'), cmd_random)
    bot.register_command(commands('rb_play'), cmd_rb_play)
    bot.register_command(commands('rb_query'), cmd_rb_query)
    bot.register_command(commands('remove'), cmd_remove)
    bot.register_command(commands('remove_tag'), cmd_remove_tag)
    bot.register_command(commands('repeat'), cmd_repeat)
    bot.register_command(commands('requests_webinterface_access'), cmd_web_access)
    bot.register_command(commands('rescan'), cmd_refresh_cache, no_partial_match=True)
    bot.register_command(commands('search'), cmd_search_library)
    bot.register_command(commands('skip'), cmd_skip)
    bot.register_command(commands('stop'), cmd_stop)
    bot.register_command(commands('stop_and_getout'), cmd_stop_and_getout)
    bot.register_command(commands('version'), cmd_version, no_partial_match=True)
    bot.register_command(commands('volume'), cmd_volume)
    bot.register_command(commands('yt_play'), cmd_yt_play)
    bot.register_command(commands('yt_search'), cmd_yt_search)

    # admin command
    bot.register_command(commands('add_webinterface_user'), cmd_web_user_add, admin=True)
    bot.register_command(commands('drop_database'), cmd_drop_database, no_partial_match=True, admin=True)
    bot.register_command(commands('kill'), cmd_kill, admin=True)
    bot.register_command(commands('list_webinterface_user'), cmd_web_user_list, admin=True)
    bot.register_command(commands('remove_webinterface_user'), cmd_web_user_remove, admin=True)
    bot.register_command(commands('max_volume'), cmd_max_volume, admin=True)
    bot.register_command(commands('update'), cmd_update, no_partial_match=True, admin=True)
    bot.register_command(commands('url_ban'), cmd_url_ban, no_partial_match=True, admin=True)
    bot.register_command(commands('url_ban_list'), cmd_url_ban_list, no_partial_match=True, admin=True)
    bot.register_command(commands('url_unban'), cmd_url_unban, no_partial_match=True, admin=True)
    bot.register_command(commands('url_unwhitelist'), cmd_url_unwhitelist, no_partial_match=True, admin=True)
    bot.register_command(commands('url_whitelist'), cmd_url_whitelist, no_partial_match=True, admin=True)
    bot.register_command(commands('url_whitelist_list'), cmd_url_whitelist_list, no_partial_match=True, admin=True)
    bot.register_command(commands('user_ban'), cmd_user_ban, no_partial_match=True, admin=True)
    bot.register_command(commands('user_unban'), cmd_user_unban, no_partial_match=True, admin=True)

    # Just for debug use
    bot.register_command('rtrms', cmd_real_time_rms, True)
    # bot.register_command('loop', cmd_loop_state, True)
    # bot.register_command('item', cmd_item, True)


