# coding=utf-8
import logging

from constants import tr_cli as tr
import util
import variables as var
import media.playlist

from ._shared import send_multi_lines
from .streaming import _cancel_spotify_feeders

log = logging.getLogger("bot")


# Accepted !mode arguments -> canonical playlist mode. Lets users type short
# forms (e.g. "1" or "loop") instead of the full "one-shot"/"repeat" names.
# The canonical values on the right are the only ones media.playlist understands.
MODE_ALIASES = {
    "1": "one-shot", "one": "one-shot", "once": "one-shot",
    "oneshot": "one-shot", "one-shot": "one-shot",
    "2": "repeat", "loop": "repeat", "repeat": "repeat",
    "3": "random", "rand": "random", "shuffle": "random", "random": "random",
    "4": "autoplay", "auto": "autoplay", "autoplay": "autoplay",
}



def cmd_play(bot, user, text, command, parameter):
    params = parameter.split()
    index = -1
    start_at = 0
    if len(params) > 0:
        if params[0].isdigit() and 1 <= int(params[0]) <= len(var.playlist):
            index = int(params[0])
        else:
            bot.send_msg(tr('invalid_index', index=parameter), text)
            return

        if len(params) > 1:
            try:
                start_at = util.parse_time(params[1])
            except ValueError:
                bot.send_msg(tr('bad_parameter', command=command), text)
                return

    if len(var.playlist) > 0:
        if index != -1:
            bot.play(int(index) - 1, start_at)

        elif bot.is_pause:
            bot.resume()
        else:
            bot.send_msg(var.playlist.current_item().format_current_playing(), text)
    else:
        bot.is_pause = False
        bot.send_msg(tr('queue_empty'), text)


def cmd_pause(bot, user, text, command, parameter):
    bot.pause()
    bot.send_channel_msg(tr('paused'))



def cmd_stop(bot, user, text, command, parameter):
    if var.config.getboolean("bot", "clear_when_stop_in_oneshot") \
            and var.playlist.mode == 'one-shot':
        cmd_clear(bot, user, text, command, parameter)
    else:
        bot.stop()
    bot.send_msg(tr('stopped'), text)


def cmd_clear(bot, user, text, command, parameter):
    _cancel_spotify_feeders()
    bot.clear()
    bot.send_msg(tr('cleared'), text)



def cmd_stop_and_getout(bot, user, text, command, parameter):
    _cancel_spotify_feeders()
    bot.stop()
    if var.playlist.mode == "one-shot":
        var.playlist.clear()

    bot.join_channel()



def cmd_current_music(bot, user, text, command, parameter):
    if len(var.playlist) > 0:
        bot.send_msg(var.playlist.current_item().format_current_playing(), text)
    else:
        bot.send_msg(tr('not_playing'), text)


def cmd_skip(bot, user, text, command, parameter):
    # statistics: an explicit skip flags the current play as skipped
    if var.play_history is not None and not bot.is_pause:
        try:
            current = var.playlist.current_item()
            if current:
                var.play_history.mark_skipped(current.id)
        except Exception:
            pass

    if not bot.is_pause:
        bot.interrupt()
    else:
        var.playlist.next()
        bot.wait_for_ready = True

    if len(var.playlist) == 0:
        bot.send_msg(tr('queue_empty'), text)


def cmd_last(bot, user, text, command, parameter):
    if len(var.playlist) > 0:
        bot.interrupt()
        var.playlist.point_to(len(var.playlist) - 1 - 1)
    else:
        bot.send_msg(tr('queue_empty'), text)


def cmd_remove(bot, user, text, command, parameter):
    # Allow to remove specific music into the queue with a number
    if parameter and parameter.isdigit() and 0 < int(parameter) <= len(var.playlist):

        index = int(parameter) - 1

        if index == var.playlist.current_index:
            removed = var.playlist[index]
            bot.send_msg(tr('removing_item',
                                      item=removed.format_title()), text)
            log.info("cmd: delete from playlist: " + removed.format_debug_string())

            var.playlist.remove(index)

            if index < len(var.playlist):
                if not bot.is_pause:
                    bot.interrupt()
                    var.playlist.current_index -= 1
                    # then the bot will move to next item

            else:  # if item deleted is the last item of the queue
                var.playlist.current_index -= 1
                if not bot.is_pause:
                    bot.interrupt()
        else:
            var.playlist.remove(index)

    else:
        bot.send_msg(tr('bad_parameter', command=command), text)



def cmd_queue(bot, user, text, command, parameter):
    if len(var.playlist) == 0:
        msg = tr('queue_empty')
        bot.send_msg(msg, text)
    else:
        msgs = [tr('queue_contents')]
        for i, music in enumerate(var.playlist):
            tags = ''
            if len(music.item().tags) > 0:
                tags = "<sup>{}</sup>".format(", ".join(music.item().tags))
            if i == var.playlist.current_index:
                newline = "<b style='color:orange'>{} ({}) {} </b> {}".format(i + 1, music.display_type(),
                                                                              music.format_title(), tags)
            else:
                newline = '<b>{}</b> ({}) {} {}'.format(i + 1, music.display_type(),
                                                        music.format_title(), tags)

            msgs.append(newline)

        send_multi_lines(bot, msgs, text)


def cmd_random(bot, user, text, command, parameter):
    bot.interrupt()
    var.playlist.randomize()


def cmd_repeat(bot, user, text, command, parameter):
    repeat = 1
    if parameter and parameter.isdigit():
        repeat = int(parameter)

    music = var.playlist.current_item()
    if music:
        for _ in range(repeat):
            var.playlist.insert(
                var.playlist.current_index + 1,
                music
            )
            log.info("bot: add to playlist: " + music.format_debug_string())

        bot.send_channel_msg(tr("repeat", song=music.format_song_string(), n=str(repeat)))
    else:
        bot.send_msg(tr("queue_empty"), text)


def cmd_mode(bot, user, text, command, parameter):
    if not parameter:
        bot.send_msg(tr("current_mode", mode=var.playlist.mode), text)
        return
    mode = MODE_ALIASES.get(parameter.strip().lower())
    if mode is None:
        bot.send_msg(tr('unknown_mode', mode=parameter), text)
    else:
        var.db.set('playlist', 'playback_mode', mode)
        var.playlist = media.playlist.get_playlist(mode, var.playlist)
        log.info(f"command: playback mode changed to {mode}.")
        bot.send_msg(tr("change_mode", mode=var.playlist.mode,
                                  user=bot.mumble.users[text.actor].name), text)
        if mode == "random":
            bot.interrupt()


