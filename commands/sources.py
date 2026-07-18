# coding=utf-8
import logging
import re

from constants import tr_cli as tr
import util
import variables as var
from pyradios import RadioBrowser
from database import Condition
from media.item import dict_to_item
from media.cache import get_cached_wrapper_from_scrap, get_cached_wrapper, get_cached_wrapper_from_dict, \
    get_cached_wrappers_from_dicts
from media.url_from_playlist import get_playlist_info

from . import _shared
from ._shared import (ITEMS_PER_PAGE, send_multi_lines, send_multi_lines_in_channel,
                      send_item_added_message)

log = logging.getLogger("bot")


def cmd_play_file(bot, user, text, command, parameter, do_not_refresh_cache=False):
    # assume parameter is a path
    music_wrappers = get_cached_wrappers_from_dicts(var.music_db.query_music(Condition().and_equal('path', parameter)), user)
    if music_wrappers:
        var.playlist.append(music_wrappers[0])
        log.info("cmd: add to playlist: " + music_wrappers[0].format_debug_string())
        send_item_added_message(bot, music_wrappers[0], len(var.playlist) - 1, text)
        return

    # assume parameter is a folder
    music_wrappers = get_cached_wrappers_from_dicts(var.music_db.query_music(Condition()
                                                                             .and_equal('type', 'file')
                                                                             .and_like('path', parameter + '%')), user)
    if music_wrappers:
        msgs = [tr('multiple_file_added')]

        for music_wrapper in music_wrappers:
            log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
            msgs.append("<b>{:s}</b> ({:s})".format(music_wrapper.item().title, music_wrapper.item().path))

        var.playlist.extend(music_wrappers)

        send_multi_lines_in_channel(bot, msgs)
        return

    # try to do a partial match
    matches = var.music_db.query_music(Condition()
                                       .and_equal('type', 'file')
                                       .and_like('path', '%' + parameter + '%', case_sensitive=False))
    if len(matches) == 1:
        music_wrapper = get_cached_wrapper_from_dict(matches[0], user)
        var.playlist.append(music_wrapper)
        log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
        send_item_added_message(bot, music_wrapper, len(var.playlist) - 1, text)
        return
    elif len(matches) > 1:
        _shared.song_shortlist = matches
        # Play the best (first) match right away so the common case is a single
        # command, but still list every candidate so the user can pick a
        # different one with !sl if the guess was wrong.
        music_wrapper = get_cached_wrapper_from_dict(matches[0], user)
        var.playlist.append(music_wrapper)
        log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
        msgs = [tr('played_best_match', item=matches[0]['title'])]
        for index, match in enumerate(matches):
            msgs.append("<b>{:d}</b> - <b>{:s}</b> ({:s})".format(
                index + 1, match['title'], match['path']))
        msgs.append(tr("shortlist_instruction"))
        send_multi_lines(bot, msgs, text)
        return

    if do_not_refresh_cache:
        bot.send_msg(tr("no_file"), text)
    else:
        var.cache.build_dir_cache()
        cmd_play_file(bot, user, text, command, parameter, do_not_refresh_cache=True)


def cmd_play_file_match(bot, user, text, command, parameter, do_not_refresh_cache=False):
    if parameter:
        file_dicts = var.music_db.query_music(Condition().and_equal('type', 'file'))
        msgs = [tr('multiple_file_added') + "<ul>"]
        try:
            count = 0
            music_wrappers = []
            for file_dict in file_dicts:
                file = file_dict['title']
                match = re.search(parameter, file)
                if match and match[0]:
                    count += 1
                    music_wrapper = get_cached_wrapper(dict_to_item(file_dict), user)
                    music_wrappers.append(music_wrapper)
                    log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
                    msgs.append("<li><b>{}</b> ({})</li>".format(music_wrapper.item().title,
                                                                 file[:match.span()[0]]
                                                                 + "<b style='color:pink'>"
                                                                 + file[match.span()[0]: match.span()[1]]
                                                                 + "</b>"
                                                                 + file[match.span()[1]:]
                                                                 ))

            if count != 0:
                msgs.append("</ul>")
                var.playlist.extend(music_wrappers)
                send_multi_lines_in_channel(bot, msgs, "")
            else:
                if do_not_refresh_cache:
                    bot.send_msg(tr("no_file"), text)
                else:
                    var.cache.build_dir_cache()
                    cmd_play_file_match(bot, user, text, command, parameter, do_not_refresh_cache=True)

        except re.error as e:
            msg = tr('wrong_pattern', error=str(e))
            bot.send_msg(msg, text)
    else:
        bot.send_msg(tr('bad_parameter', command=command), text)


def cmd_play_url(bot, user, text, command, parameter):
    url = util.get_url_from_input(parameter)
    if url and not util.is_public_url(url):
        bot.send_msg(tr('bad_url'), text)
        return
    if url:
        music_wrapper = get_cached_wrapper_from_scrap(type='url', url=url, user=user)
        var.playlist.append(music_wrapper)

        log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
        send_item_added_message(bot, music_wrapper, len(var.playlist) - 1, text)

        if len(var.playlist) == 2:
            # If I am the second item on the playlist. (I am the next one!)
            bot.async_download_next()
    else:
        bot.send_msg(tr('bad_parameter', command=command), text)



def cmd_play_playlist(bot, user, text, command, parameter):
    offset = 0  # if you want to start the playlist at a specific index
    try:
        offset = int(parameter.split(" ")[-1])
    except ValueError:
        pass

    url = util.get_url_from_input(parameter)
    if url and not util.is_public_url(url):
        bot.send_msg(tr('bad_url'), text)
        return
    if url:
        log.debug(f"cmd: fetching media info from playlist url {url}")
        items = get_playlist_info(url=url, start_index=offset, user=user)
        if len(items) > 0:
            items = var.playlist.extend(list(map(lambda item: get_cached_wrapper_from_scrap(**item), items)))
            for music in items:
                log.info("cmd: add to playlist: " + music.format_debug_string())
        else:
            bot.send_msg(tr("playlist_fetching_failed"), text)
    else:
        bot.send_msg(tr('bad_parameter', command=command), text)


def cmd_play_radio(bot, user, text, command, parameter):
    if not parameter:
        all_radio = var.config.items('radio')
        msg = tr('preconfigurated_radio')
        for i in all_radio:
            comment = ""
            if len(i[1].split(maxsplit=1)) == 2:
                comment = " - " + i[1].split(maxsplit=1)[1]
            msg += "<br />" + i[0] + comment
        bot.send_msg(msg, text)
    else:
        if var.config.has_option('radio', parameter):
            parameter = var.config.get('radio', parameter)
            parameter = parameter.split()[0]
        url = util.get_url_from_input(parameter)
        if url:
            music_wrapper = get_cached_wrapper_from_scrap(type='radio', url=url, user=user)

            var.playlist.append(music_wrapper)
            log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
            send_item_added_message(bot, music_wrapper, len(var.playlist) - 1, text)
        else:
            bot.send_msg(tr('bad_url'), text)


def cmd_rb_query(bot, user, text, command, parameter):
    log.info('cmd: Querying radio stations')
    if not parameter:
        log.debug('rbquery without parameter')
        msg = tr('rb_query_empty')
        bot.send_msg(msg, text)
    else:
        log.debug('cmd: Found query parameter: ' + parameter)
        rb = RadioBrowser()
        rb_stations = rb.search(name=parameter, name_exact=False)
        msg = tr('rb_query_result')
        msg += '\n<table><tr><th>!rbplay ID</th><th>Station Name</th><th>Genre</th><th>Codec/Bitrate</th><th>Country</th></tr>'
        if not rb_stations:
            log.debug(f"cmd: No matches found for rbquery {parameter}")
            bot.send_msg(f"Radio-Browser found no matches for {parameter}", text)
        else:
            for s in rb_stations:
                station_id = s['stationuuid']
                station_name = s['name']
                country = s['countrycode']
                codec = s['codec']
                bitrate = s['bitrate']
                genre = s['tags']
                msg += f"<tr><td>{station_id}</td><td>{station_name}</td><td>{genre}</td><td>{codec}/{bitrate}</td><td>{country}</td></tr>"
            msg += '</table>'
            # Full message as html table
            if len(msg) <= 5000:
                bot.send_msg(msg, text)
            # Shorten message if message too long (stage I)
            else:
                log.debug('Result too long stage I')
                msg = tr('rb_query_result') + ' (shortened L1)'
                msg += '\n<table><tr><th>!rbplay ID</th><th>Station Name</th></tr>'
                for s in rb_stations:
                    station_id = s['stationuuid']
                    station_name = s['name']
                    msg += f'<tr><td>{station_id}</td><td>{station_name}</td>'
                msg += '</table>'
                if len(msg) <= 5000:
                    bot.send_msg(msg, text)
                # Shorten message if message too long (stage II)
                else:
                    log.debug('Result too long stage II')
                    msg = tr('rb_query_result') + ' (shortened L2)'
                    msg += '!rbplay ID - Station Name'
                    for s in rb_stations:
                        station_id = s['stationuuid']
                        station_name = s['name'][:12]
                        msg += f'{station_id} - {station_name}'
                    if len(msg) <= 5000:
                        bot.send_msg(msg, text)
                    # Message still too long
                    else:
                        bot.send_msg('Query result too long to post (> 5000 characters), please try another query.', text)


def cmd_rb_play(bot, user, text, command, parameter):
    log.debug('cmd: Play a station by ID')
    if not parameter:
        log.debug('rbplay without parameter')
        msg = tr('rb_play_empty')
        bot.send_msg(msg, text)
    else:
        log.debug('cmd: Retreiving url for station ID ' + parameter)
        rb = RadioBrowser()
        rstation = rb.station_by_uuid(parameter)
        stationname = rstation[0]['name']
        country = rstation[0]['countrycode']
        codec = rstation[0]['codec']
        bitrate = rstation[0]['bitrate']
        genre = rstation[0]['tags']
        homepage = rstation[0]['homepage']
        url = rstation[0]['url']
        msg = 'Radio station added to playlist:'

        msg += '<table><tr><th>ID</th><th>Station Name</th><th>Genre</th><th>Codec/Bitrate</th><th>Country</th><th>Homepage</th></tr>' + \
               f"<tr><td>{parameter}</td><td>{stationname}</td><td>{genre}</td><td>{codec}/{bitrate}</td><td>{country}</td><td>{homepage}</td></tr></table>"
        log.debug(f'cmd: Added station to playlist {stationname}')
        bot.send_msg(msg, text)
        if url != "-1":
            log.info('cmd: Found url: ' + url)
            music_wrapper = get_cached_wrapper_from_scrap(type='radio', url=url, name=stationname, user=user)
            var.playlist.append(music_wrapper)
            log.info("cmd: add to playlist: " + music_wrapper.format_debug_string())
            bot.async_download_next()
        else:
            log.info('cmd: No playable url found.')
            msg += "No playable url found for this station, please try another station."
            bot.send_msg(msg, text)


yt_last_result = []
yt_last_page = 0  # TODO: if we keep adding global variables, we need to consider sealing all commands up into classes.


def cmd_yt_search(bot, user, text, command, parameter):
    global log, yt_last_result, yt_last_page
    item_per_page = 5

    if parameter:
        # if next page
        if parameter.startswith("-n"):
            yt_last_page += 1
            if len(yt_last_result) > yt_last_page * item_per_page:
                _shared.song_shortlist = [{'type': 'url',
                                   'url': "https://www.youtube.com/watch?v=" + result[0],
                                   'title': result[1]
                                   } for result in yt_last_result[yt_last_page * item_per_page: (yt_last_page * item_per_page) + item_per_page]]
                msg = _yt_format_result(yt_last_result, yt_last_page * item_per_page, item_per_page)
                bot.send_msg(tr('yt_result', result_table=msg), text)
            else:
                bot.send_msg(tr('yt_no_more'), text)

        # if query
        else:
            results = util.youtube_search(parameter)
            if results:
                yt_last_result = results
                yt_last_page = 0
                _shared.song_shortlist = [{'type': 'url', 'url': "https://www.youtube.com/watch?v=" + result[0]}
                                  for result in results[0: item_per_page]]
                msg = _yt_format_result(results, 0, item_per_page)
                bot.send_msg(tr('yt_result', result_table=msg), text)
            else:
                bot.send_msg(tr('yt_query_error'), text)
    else:
        bot.send_msg(tr('bad_parameter', command=command), text)


def _yt_format_result(results, start, count):
    msg = '<table><tr><th width="10%">Index</th><th>Title</th><th width="20%">Uploader</th></tr>'
    for index, item in enumerate(results[start:start + count]):
        msg += '<tr><td>{index:d}</td><td>{title}</td><td>{uploader}</td></tr>'.format(
            index=index + 1, title=item[1], uploader=item[2])
    msg += '</table>'

    return msg


def cmd_yt_play(bot, user, text, command, parameter):
    global log, yt_last_result, yt_last_page

    if parameter:
        results = util.youtube_search(parameter)
        if results:
            yt_last_result = results
            yt_last_page = 0
            url = "https://www.youtube.com/watch?v=" + yt_last_result[0][0]
            cmd_play_url(bot, user, text, command, url)
        else:
            bot.send_msg(tr('yt_query_error'), text)
    else:
        bot.send_msg(tr('bad_parameter', command=command), text)



def cmd_list_file(bot, user, text, command, parameter):

    files = var.music_db.query_music(Condition()
                                     .and_equal('type', 'file')
                                     .order_by('path'))

    _shared.song_shortlist = files

    msgs = [tr("multiple_file_found") + "<ul>"]
    try:
        count = 0
        for index, file in enumerate(files):
            if parameter:
                match = re.search(parameter, file['path'])
                if not match:
                    continue

            count += 1
            if count > ITEMS_PER_PAGE:
                break
            msgs.append("<li><b>{:d}</b> - <b>{:s}</b> ({:s})</li>".format(index + 1, file['title'], file['path']))

        if count != 0:
            msgs.append("</ul>")
            if count > ITEMS_PER_PAGE:
                msgs.append(tr("records_omitted"))
            msgs.append(tr("shortlist_instruction"))
            send_multi_lines(bot, msgs, text, "")
        else:
            bot.send_msg(tr("no_file"), text)

    except re.error as e:
        msg = tr('wrong_pattern', error=str(e))
        bot.send_msg(msg, text)


