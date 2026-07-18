# coding=utf-8

from constants import tr_cli as tr
import variables as var
from database import SettingsDatabase, MusicDatabase
from media.item import dict_to_item, dicts_to_items
from media.cache import get_cached_wrapper_from_scrap, get_cached_wrapper_by_id, get_cached_wrappers_by_tags, \
    get_cached_wrapper

from . import _shared
from ._shared import (log, ITEMS_PER_PAGE, send_multi_lines,
                      send_multi_lines_in_channel, send_item_added_message)


def cmd_play_tags(bot, user, text, command, parameter):
    if not parameter:
        bot.send_msg(tr('bad_parameter', command=command), text)
        return

    msgs = [tr('multiple_file_added') + "<ul>"]
    count = 0

    tags = parameter.split(",")
    tags = list(map(lambda t: t.strip(), tags))
    music_wrappers = get_cached_wrappers_by_tags(tags, user)
    for music_wrapper in music_wrappers:
        count += 1
        log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
        msgs.append("<li><b>{}</b> (<i>{}</i>)</li>".format(music_wrapper.item().title, ", ".join(music_wrapper.item().tags)))

    if count != 0:
        msgs.append("</ul>")
        var.playlist.extend(music_wrappers)
        send_multi_lines_in_channel(bot, msgs, "")
    else:
        bot.send_msg(tr("no_file"), text)


def cmd_add_tag(bot, user, text, command, parameter):
    params = parameter.split(" ", 1)
    index = 0
    tags = []

    if len(params) == 2 and params[0].isdigit():
        index = params[0]
        tags = list(map(lambda t: t.strip(), params[1].split(",")))
    elif len(params) == 2 and params[0] == "*":
        index = "*"
        tags = list(map(lambda t: t.strip(), params[1].split(",")))
    else:
        index = str(var.playlist.current_index + 1)
        tags = list(map(lambda t: t.strip(), parameter.split(",")))

    if tags[0]:
        if index.isdigit() and 1 <= int(index) <= len(var.playlist):
            var.playlist[int(index) - 1].add_tags(tags)
            log.info(f"cmd: add tags {', '.join(tags)} to song {var.playlist[int(index) - 1].format_debug_string()}")
            bot.send_msg(tr("added_tags",
                                      tags=", ".join(tags),
                                      song=var.playlist[int(index) - 1].format_title()), text)
            return

        elif index == "*":
            for item in var.playlist:
                item.add_tags(tags)
                log.info(f"cmd: add tags {', '.join(tags)} to song {item.format_debug_string()}")
            bot.send_msg(tr("added_tags_to_all", tags=", ".join(tags)), text)
            return

    bot.send_msg(tr('bad_parameter', command=command), text)


def cmd_remove_tag(bot, user, text, command, parameter):
    params = parameter.split(" ", 1)
    index = 0
    tags = []

    if len(params) == 2 and params[0].isdigit():
        index = params[0]
        tags = list(map(lambda t: t.strip(), params[1].split(",")))
    elif len(params) == 2 and params[0] == "*":
        index = "*"
        tags = list(map(lambda t: t.strip(), params[1].split(",")))
    else:
        index = str(var.playlist.current_index + 1)
        tags = list(map(lambda t: t.strip(), parameter.split(",")))

    if tags[0]:
        if index.isdigit() and 1 <= int(index) <= len(var.playlist):
            if tags[0] != "*":
                var.playlist[int(index) - 1].remove_tags(tags)
                log.info(f"cmd: remove tags {', '.join(tags)} from song {var.playlist[int(index) - 1].format_debug_string()}")
                bot.send_msg(tr("removed_tags",
                                          tags=", ".join(tags),
                                          song=var.playlist[int(index) - 1].format_title()), text)
                return
            else:
                var.playlist[int(index) - 1].clear_tags()
                log.info(f"cmd: clear tags from song {var.playlist[int(index) - 1].format_debug_string()}")
                bot.send_msg(tr("cleared_tags",
                                          song=var.playlist[int(index) - 1].format_title()), text)
                return

        elif index == "*":
            if tags[0] != "*":
                for item in var.playlist:
                    item.remove_tags(tags)
                    log.info(f"cmd: remove tags {', '.join(tags)} from song {item.format_debug_string()}")
                bot.send_msg(tr("removed_tags_from_all", tags=", ".join(tags)), text)
                return
            else:
                for item in var.playlist:
                    item.clear_tags()
                    log.info(f"cmd: clear tags from song {item.format_debug_string()}")
                bot.send_msg(tr("cleared_tags_from_all"), text)
                return

    bot.send_msg(tr('bad_parameter', command=command), text)


def cmd_find_tagged(bot, user, text, command, parameter):

    if not parameter:
        bot.send_msg(tr('bad_parameter', command=command), text)
        return

    msgs = [tr('multiple_file_found') + "<ul>"]
    count = 0

    tags = parameter.split(",")
    tags = list(map(lambda t: t.strip(), tags))

    music_dicts = var.music_db.query_music_by_tags(tags)
    _shared.song_shortlist = music_dicts

    for i, music_dict in enumerate(music_dicts):
        item = dict_to_item(music_dict)
        count += 1
        if count > ITEMS_PER_PAGE:
            break
        msgs.append("<li><b>{:d}</b> - <b>{}</b> (<i>{}</i>)</li>".format(i + 1, item.title, ", ".join(item.tags)))

    if count != 0:
        msgs.append("</ul>")
        if count > ITEMS_PER_PAGE:
            msgs.append(tr("records_omitted"))
        msgs.append(tr("shortlist_instruction"))
        send_multi_lines(bot, msgs, text, "")
    else:
        bot.send_msg(tr("no_file"), text)


def cmd_search_library(bot, user, text, command, parameter):
    if not parameter:
        bot.send_msg(tr('bad_parameter', command=command), text)
        return

    msgs = [tr('multiple_file_found') + "<ul>"]
    count = 0

    _keywords = parameter.split(" ")
    keywords = []
    for kw in _keywords:
        if kw:
            keywords.append(kw)

    music_dicts = var.music_db.query_music_by_keywords(keywords)
    if music_dicts:
        items = dicts_to_items(music_dicts)
        _shared.song_shortlist = music_dicts

        if len(items) == 1:
            music_wrapper = get_cached_wrapper(items[0], user)
            var.playlist.append(music_wrapper)
            log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
            send_item_added_message(bot, music_wrapper, len(var.playlist) - 1, text)
        else:
            for item in items:
                count += 1
                if count > ITEMS_PER_PAGE:
                    break
                if len(item.tags) > 0:
                    msgs.append("<li><b>{:d}</b> - [{}] <b>{}</b> (<i>{}</i>)</li>".format(count, item.display_type(), item.title, ", ".join(item.tags)))
                else:
                    msgs.append("<li><b>{:d}</b> - [{}] <b>{}</b> </li>".format(count, item.display_type(), item.title, ", ".join(item.tags)))

            if count != 0:
                msgs.append("</ul>")
                if count > ITEMS_PER_PAGE:
                    msgs.append(tr("records_omitted"))
                msgs.append(tr("shortlist_instruction"))
                send_multi_lines(bot, msgs, text, "")
            else:
                bot.send_msg(tr("no_file"), text)
    else:
        bot.send_msg(tr("no_file"), text)


def cmd_shortlist(bot, user, text, command, parameter):
    if parameter.strip() == "*":
        msgs = [tr('multiple_file_added') + "<ul>"]
        music_wrappers = []
        for kwargs in _shared.song_shortlist:
            kwargs['user'] = user
            music_wrapper = get_cached_wrapper_from_scrap(**kwargs)
            music_wrappers.append(music_wrapper)
            log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
            msgs.append("<li>[{}] <b>{}</b></li>".format(music_wrapper.item().type, music_wrapper.item().title))

        var.playlist.extend(music_wrappers)

        msgs.append("</ul>")
        send_multi_lines_in_channel(bot, msgs, "")
        return

    try:
        indexes = [int(i) for i in parameter.split(" ")]
    except ValueError:
        bot.send_msg(tr('bad_parameter', command=command), text)
        return

    if len(indexes) > 1:
        msgs = [tr('multiple_file_added') + "<ul>"]
        music_wrappers = []
        for index in indexes:
            if 1 <= index <= len(_shared.song_shortlist):
                kwargs = _shared.song_shortlist[index - 1]
                kwargs['user'] = user
                music_wrapper = get_cached_wrapper_from_scrap(**kwargs)
                music_wrappers.append(music_wrapper)
                log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
                msgs.append("<li>[{}] <b>{}</b></li>".format(music_wrapper.item().type, music_wrapper.item().title))
            else:
                var.playlist.extend(music_wrappers)
                bot.send_msg(tr('bad_parameter', command=command), text)
                return

        var.playlist.extend(music_wrappers)

        msgs.append("</ul>")
        send_multi_lines_in_channel(bot, msgs, "")
        return
    elif len(indexes) == 1:
        index = indexes[0]
        if 1 <= index <= len(_shared.song_shortlist):
            kwargs = _shared.song_shortlist[index - 1]
            kwargs['user'] = user
            music_wrapper = get_cached_wrapper_from_scrap(**kwargs)
            var.playlist.append(music_wrapper)
            log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
            send_item_added_message(bot, music_wrapper, len(var.playlist) - 1, text)
            return

    bot.send_msg(tr('bad_parameter', command=command), text)


def cmd_delete_from_library(bot, user, text, command, parameter):
    if not var.config.getboolean("bot", "delete_allowed"):
        bot.mumble.users[text.actor].send_text_message(tr('not_admin'))
        return

    try:
        indexes = [int(i) for i in parameter.split(" ")]
    except ValueError:
        bot.send_msg(tr('bad_parameter', command=command), text)
        return

    if len(indexes) > 1:
        msgs = [tr('multiple_file_added') + "<ul>"]
        count = 0
        for index in indexes:
            if 1 <= index <= len(_shared.song_shortlist):
                music_dict = _shared.song_shortlist[index - 1]
                if 'id' in music_dict:
                    music_wrapper = get_cached_wrapper_by_id(music_dict['id'], user)
                    log.info("cmd: remove from library: " + music_wrapper.format_debug_string())
                    msgs.append("<li>[{}] <b>{}</b></li>".format(music_wrapper.item().type, music_wrapper.item().title))
                    var.playlist.remove_by_id(music_dict['id'])
                    var.cache.free_and_delete(music_dict['id'])
                    count += 1
            else:
                bot.send_msg(tr('bad_parameter', command=command), text)
                return

        if count == 0:
            bot.send_msg(tr('bad_parameter', command=command), text)
            return

        msgs.append("</ul>")
        send_multi_lines_in_channel(bot, msgs, "")
        return
    elif len(indexes) == 1:
        index = indexes[0]
        if 1 <= index <= len(_shared.song_shortlist):
            music_dict = _shared.song_shortlist[index - 1]
            if 'id' in music_dict:
                music_wrapper = get_cached_wrapper_by_id(music_dict['id'], user)
                bot.send_msg(tr('file_deleted', item=music_wrapper.format_song_string()), text)
                log.info("cmd: remove from library: " + music_wrapper.format_debug_string())
                var.playlist.remove_by_id(music_dict['id'])
                var.cache.free_and_delete(music_dict['id'])
                return

    bot.send_msg(tr('bad_parameter', command=command), text)


def cmd_drop_database(bot, user, text, command, parameter):
    if bot.is_admin(user):
        var.db.drop_table()
        var.db = SettingsDatabase(var.settings_db_path)
        var.music_db.drop_table()
        var.music_db = MusicDatabase(var.settings_db_path)
        log.info("command: database dropped.")
        bot.send_msg(tr('database_dropped'), text)
    else:
        bot.mumble.users[text.actor].send_text_message(tr('not_admin'))


def cmd_refresh_cache(bot, user, text, command, parameter):
    if bot.is_admin(user):
        var.cache.build_dir_cache()
        log.info("command: Local file cache refreshed.")
        bot.send_msg(tr('cache_refreshed'), text)
    else:
        bot.mumble.users[text.actor].send_text_message(tr('not_admin'))


