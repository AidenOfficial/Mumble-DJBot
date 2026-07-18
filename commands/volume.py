# coding=utf-8
import logging

from constants import tr_cli as tr
import variables as var

log = logging.getLogger("bot")



def cmd_volume(bot, user, text, command, parameter):
    # The volume is a percentage
    max_vol = min(int(var.config.getfloat('bot', 'max_volume') * 100), 100.0)
    if var.db.has_option('bot', 'max_volume'):
        max_vol = float(var.db.get('bot', 'max_volume')) * 100.0
    if parameter and parameter.isdigit() and 0 <= int(parameter) <= 100:
        if int(parameter) <= max_vol:
            vol = int(parameter)
            bot.send_msg(tr('change_volume', volume=int(parameter), user=bot.mumble.users[text.actor].name), text)
        else:
            vol = max_vol
            bot.send_msg(tr('max_volume', max=int(vol)), text)
        bot.volume_helper.set_volume(float(vol) / 100.0)
        var.db.set('bot', 'volume', str(float(vol) / 100.0))
        log.info(f'cmd: volume set to {float(vol) / 100.0}')
    else:
        bot.send_msg(tr('current_volume', volume=int(bot.volume_helper.plain_volume_set * 100)), text)

def cmd_max_volume(bot, user, text, command, parameter):
    
    if parameter and parameter.isdigit() and 0 <= int(parameter) <= 100:
        max_vol = float(parameter) / 100.0
        var.db.set('bot', 'max_volume', float(parameter) / 100.0)
        bot.send_msg(tr('change_max_volume', max=parameter, user=bot.mumble.users[text.actor].name), text)
        if int(bot.volume_helper.plain_volume_set) > max_vol:
            bot.volume_helper.set_volume(max_vol)
        log.info(f'cmd: max volume set to {max_vol}')
    else:
        max_vol = var.config.getfloat('bot', 'max_volume') * 100.0
        if var.db.has_option('bot', 'max_volume'):
            max_vol = var.db.getfloat('bot', 'max_volume') * 100.0
        bot.send_msg(tr('current_max_volume', max=int(max_vol)), text)
        
def cmd_ducking(bot, user, text, command, parameter):
    if parameter == "" or parameter == "on":
        bot.is_ducking = True
        var.db.set('bot', 'ducking', True)
        bot.mumble.callbacks.sound_received.set_handler(bot.ducking_sound_received)
        bot.set_receive_sound(True)
        log.info('cmd: ducking is on')
        msg = "Ducking on."
        bot.send_msg(msg, text)
    elif parameter == "off":
        bot.is_ducking = False
        bot.set_receive_sound(False)
        var.db.set('bot', 'ducking', False)
        msg = "Ducking off."
        log.info('cmd: ducking is off')
        bot.send_msg(msg, text)


def cmd_ducking_threshold(bot, user, text, command, parameter):
    if parameter and parameter.isdigit():
        bot.ducking_threshold = int(parameter)
        var.db.set('bot', 'ducking_threshold', str(bot.ducking_threshold))
        msg = f"Ducking threshold set to {bot.ducking_threshold}."
        bot.send_msg(msg, text)
    else:
        msg = f"Current ducking threshold is {bot.ducking_threshold}. " \
              f"Loudest sound heard recently: {bot._max_rms}."
        bot.send_msg(msg, text)


def cmd_ducking_delay(bot, user, text, command, parameter):
    try:
        delay = float(parameter)
    except (TypeError, ValueError):
        delay = None

    if delay is not None and delay >= 0:
        bot.ducking_delay = delay
        var.db.set('bot', 'ducking_delay', str(delay))
        msg = f"Ducking now triggers after {delay:.2f}s of sustained noise."
        bot.send_msg(msg, text)
    else:
        msg = f"Ducking currently triggers after {bot.ducking_delay:.2f}s of sustained noise."
        bot.send_msg(msg, text)


def cmd_ducking_volume(bot, user, text, command, parameter):
    # The volume is a percentage
    if parameter and parameter.isdigit() and 0 <= int(parameter) <= 100:
        bot.volume_helper.set_ducking_volume(float(parameter) / 100.0)
        bot.send_msg(tr('change_ducking_volume', volume=parameter, user=bot.mumble.users[text.actor].name), text)
        var.db.set('bot', 'ducking_volume', float(parameter) / 100.0)
        log.info(f'cmd: volume on ducking set to {parameter}')
    else:
        bot.send_msg(tr('current_ducking_volume', volume=int(bot.volume_helper.plain_ducking_volume_set * 100)), text)


