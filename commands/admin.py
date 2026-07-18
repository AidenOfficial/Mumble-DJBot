# coding=utf-8

from constants import tr_cli as tr
import util
import variables as var
from media.item import item_id_generators



def cmd_joinme(bot, user, text, command, parameter):
    bot.mumble.users.myself.move_in(
        bot.mumble.users[text.actor].channel_id, token=parameter)


def cmd_user_ban(bot, user, text, command, parameter):
    if parameter:
        var.db.set("user_ban", parameter, None)
        bot.send_msg(tr("user_ban_success", user=parameter), text)
    else:
        ban_list = "<ul>"
        for i in var.db.items("url_ban"):
            ban_list += "<li>" + i[0] + "</li>"
        ban_list += "</ul>"
        bot.send_msg(tr("user_ban_list", list=ban_list), text)


def cmd_user_unban(bot, user, text, command, parameter):
    if parameter and var.db.has_option("user_ban", parameter):
        var.db.remove_option("user_ban", parameter)
        bot.send_msg(tr("user_unban_success", user=parameter), text)


def cmd_url_ban(bot, user, text, command, parameter):
    url = util.get_url_from_input(parameter)
    if url:
        _id = item_id_generators['url'](url=url)
        var.cache.free_and_delete(_id)
        var.playlist.remove_by_id(_id)
    else:
        if var.playlist.current_item() and var.playlist.current_item().type == 'url':
            item = var.playlist.current_item().item()
            url = item.url
            var.cache.free_and_delete(item.id)
            var.playlist.remove_by_id(item.id)
        else:
            bot.send_msg(tr('bad_parameter', command=command), text)
            return

    # Remove from the whitelist first
    if var.db.has_option('url_whitelist', url):
        var.db.remove_option("url_whitelist", url)
        bot.send_msg(tr("url_unwhitelist_success", url=url), text)

    if not var.db.has_option('url_ban', url):
        var.db.set("url_ban", url, None)
    bot.send_msg(tr("url_ban_success", url=url), text)


def cmd_url_ban_list(bot, user, text, command, parameter):
    ban_list = "<ul>"
    for i in var.db.items("url_ban"):
        ban_list += "<li>" + i[0] + "</li>"
    ban_list += "</ul>"

    bot.send_msg(tr("url_ban_list", list=ban_list), text)


def cmd_url_unban(bot, user, text, command, parameter):
    url = util.get_url_from_input(parameter)
    if url:
        var.db.remove_option("url_ban", url)
        bot.send_msg(tr("url_unban_success", url=url), text)
    else:
        bot.send_msg(tr('bad_parameter', command=command), text)


def cmd_url_whitelist(bot, user, text, command, parameter):
    url = util.get_url_from_input(parameter)
    if url:
        # Unban first
        if var.db.has_option('url_ban', url):
            var.db.remove_option("url_ban", url)
            bot.send_msg(tr("url_unban_success"), text)

        # Then add to whitelist
        if not var.db.has_option('url_whitelist', url):
            var.db.set("url_whitelist", url, None)
        bot.send_msg(tr("url_whitelist_success", url=url), text)
    else:
        bot.send_msg(tr('bad_parameter', command=command), text)


def cmd_url_whitelist_list(bot, user, text, command, parameter):
    ban_list = "<ul>"
    for i in var.db.items("url_whitelist"):
        ban_list += "<li>" + i[0] + "</li>"
    ban_list += "</ul>"

    bot.send_msg(tr("url_whitelist_list", list=ban_list), text)


def cmd_url_unwhitelist(bot, user, text, command, parameter):
    url = util.get_url_from_input(parameter)
    if url:
        var.db.remove_option("url_whitelist", url)
        bot.send_msg(tr("url_unwhitelist_success"), text)
    else:
        bot.send_msg(tr('bad_parameter', command=command), text)



def cmd_help(bot, user, text, command, parameter):
    bot.send_msg(tr('help'), text)
    if bot.is_admin(user):
        bot.send_msg(tr('admin_help'), text)



def cmd_kill(bot, user, text, command, parameter):
    bot.pause()
    bot.exit = True


def cmd_update(bot, user, text, command, parameter):
    if bot.is_admin(user):
        bot.mumble.users[text.actor].send_text_message(
            tr('start_updating'))
        msg = util.update(bot.version)
        bot.mumble.users[text.actor].send_text_message(msg)
    else:
        bot.mumble.users[text.actor].send_text_message(
            tr('not_admin'))



def cmd_version(bot, user, text, command, parameter):
    bot.send_msg(tr('report_version', version=bot.get_version()), text)


# Just for debug use
def cmd_real_time_rms(bot, user, text, command, parameter):
    bot._display_rms = not bot._display_rms


def cmd_loop_state(bot, user, text, command, parameter):
    print(bot._loop_status)


def cmd_item(bot, user, text, command, parameter):
    var.playlist._debug_print()

