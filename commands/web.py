# coding=utf-8
import secrets
import datetime
import json

from constants import tr_cli as tr
import interface
import util
import variables as var



def cmd_web_access(bot, user, text, command, parameter):
    auth_method = var.config.get("webinterface", "auth_method")

    if auth_method == 'token':
        interface.banned_ip = []
        interface.bad_access_count = {}

        user_info = var.db.get("user", user, fallback='{}')
        user_dict = json.loads(user_info)
        if 'token' in user_dict:
            var.db.remove_option("web_token", user_dict['token'])

        token = secrets.token_urlsafe(5)
        user_dict['token'] = token
        user_dict['token_created'] = str(datetime.datetime.now())
        user_dict['last_ip'] = ''
        var.db.set("web_token", token, user)
        var.db.set("user", user, json.dumps(user_dict))

        access_address = var.config.get("webinterface", "access_address") + "/?token=" + token
    else:
        access_address = var.config.get("webinterface", "access_address")

    bot.send_msg(tr('webpage_address', address=access_address), text)


def cmd_user_password(bot, user, text, command, parameter):
    if not parameter:
        bot.send_msg(tr('bad_parameter', command=command), text)
        return

    user_info = var.db.get("user", user, fallback='{}')
    user_dict = json.loads(user_info)
    user_dict['password'], user_dict['salt'] = util.get_salted_password_hash(parameter)

    var.db.set("user", user, json.dumps(user_dict))

    bot.send_msg(tr('user_password_set'), text)


def cmd_web_user_add(bot, user, text, command, parameter):
    if not parameter:
        bot.send_msg(tr('bad_parameter', command=command), text)
        return

    auth_method = var.config.get("webinterface", "auth_method")

    if auth_method == 'password':
        web_users = json.loads(var.db.get("privilege", "web_access", fallback='[]'))
        if parameter not in web_users:
            web_users.append(parameter)
        var.db.set("privilege", "web_access", json.dumps(web_users))
        bot.send_msg(tr('web_user_list', users=", ".join(web_users)), text)
    else:
        bot.send_msg(tr('command_disabled', command=command), text)


def cmd_web_user_remove(bot, user, text, command, parameter):
    if not parameter:
        bot.send_msg(tr('bad_parameter', command=command), text)
        return

    auth_method = var.config.get("webinterface", "auth_method")

    if auth_method == 'password':
        web_users = json.loads(var.db.get("privilege", "web_access", fallback='[]'))
        if parameter in web_users:
            web_users.remove(parameter)
        var.db.set("privilege", "web_access", json.dumps(web_users))
        bot.send_msg(tr('web_user_list', users=", ".join(web_users)), text)
    else:
        bot.send_msg(tr('command_disabled', command=command), text)


def cmd_web_user_list(bot, user, text, command, parameter):
    auth_method = var.config.get("webinterface", "auth_method")

    if auth_method == 'password':
        web_users = json.loads(var.db.get("privilege", "web_access", fallback='[]'))
        bot.send_msg(tr('web_user_list', users=", ".join(web_users)), text)
    else:
        bot.send_msg(tr('command_disabled', command=command), text)


