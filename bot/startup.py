# coding=utf-8
import argparse
import configparser
import logging
import logging.handlers
import os
import sys
import threading
import time

import commands
import constants
import media.playlist
import util
import variables as var
from database import SettingsDatabase, MusicDatabase, DatabaseMigration, PlayHistoryDatabase
from media.cache import MusicCache

from .cleanup import CacheCleaner
from .core import MumbleBot


def start_web_interface(addr, port):
    global formatter
    import interface

    # setup logger
    werkzeug_logger = logging.getLogger('werkzeug')
    logfile = util.solve_filepath(var.config.get('webinterface', 'web_logfile'))
    if logfile:
        handler = logging.handlers.RotatingFileHandler(logfile, mode='a', maxBytes=10240, backupCount=3)  # Rotate after 10KB, leave 3 old logs
    else:
        handler = logging.StreamHandler()

    werkzeug_logger.addHandler(handler)

    interface.init_proxy()
    interface.web.env = 'development'
    interface.web.secret_key = var.config.get('webinterface', 'flask_secret')
    interface.web.run(port=port, host=addr)



def main():
    supported_languages = util.get_supported_language()

    parser = argparse.ArgumentParser(
        description='Bot for playing music on Mumble')

    # General arguments
    parser.add_argument("--config", dest='config', type=str, default='configuration.ini',
                        help='Load configuration from this file. Default: configuration.ini')
    parser.add_argument("--db", dest='db', type=str,
                        default=None, help='Settings database file')
    parser.add_argument("--music-db", dest='music_db', type=str,
                        default=None, help='Music library database file')
    parser.add_argument("--lang", dest='lang', type=str, default=None,
                        help='Preferred language. Support ' + ", ".join(supported_languages))

    parser.add_argument("-q", "--quiet", dest="quiet",
                        action="store_true", help="Only Error logs")
    parser.add_argument("-v", "--verbose", dest="verbose",
                        action="store_true", help="Show debug log")

    # Mumble arguments
    parser.add_argument("-s", "--server", dest="host",
                        type=str, help="Hostname of the Mumble server")
    parser.add_argument("-u", "--user", dest="user",
                        type=str, help="Username for the bot")
    parser.add_argument("-P", "--password", dest="password",
                        type=str, help="Server password, if required")
    parser.add_argument("-T", "--tokens", dest="tokens",
                        type=str, help="Server tokens to enter a channel, if required (multiple entries separated with comma ','")
    parser.add_argument("-p", "--port", dest="port",
                        type=int, help="Port for the Mumble server")
    parser.add_argument("-c", "--channel", dest="channel",
                        type=str, help="Default channel for the bot")
    parser.add_argument("-C", "--cert", dest="certificate",
                        type=str, default=None, help="Certificate file")
    parser.add_argument("-b", "--bandwidth", dest="bandwidth",
                        type=int, help="Bandwidth used by the bot")

    args = parser.parse_args()

    # ======================
    #     Load Config
    # ======================

    config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    default_config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    var.config = config

    if len(default_config.read(
            util.solve_filepath('configuration.default.ini'),
            encoding='utf-8')) == 0:
        logging.error("Could not read default configuration file 'configuration.default.ini', please check"
                      "your installation.")
        sys.exit()

    if len(config.read(
            [util.solve_filepath('configuration.default.ini'), util.solve_filepath(args.config)],
            encoding='utf-8')) == 0:
        logging.error(f'Could not read configuration from file "{args.config}"')
        sys.exit()

    extra_configs = util.check_extra_config(config, default_config)
    if extra_configs:
        extra_str = ", ".join([f"'[{k}] {v}'" for (k, v) in extra_configs])
        logging.error(f'Unexpected config items {extra_str} defined in your config file. '
                      f'This is likely caused by a recent change in the names of config items, '
                      f'or the removal of obsolete config items. Please refer to the changelog.')
        sys.exit()

    # ======================
    #     Setup Logger
    # ======================

    bot_logger = logging.getLogger("bot")
    bot_logger.setLevel(logging.INFO)

    if args.verbose:
        bot_logger.setLevel(logging.DEBUG)
        bot_logger.debug("Starting in DEBUG loglevel")
    elif args.quiet:
        bot_logger.setLevel(logging.ERROR)
        bot_logger.error("Starting in ERROR loglevel")

    logfile = util.solve_filepath(var.config.get('bot', 'logfile').strip())
    handler = None
    if logfile:
        print(f"Redirecting stdout and stderr to log file: {logfile}")
        handler = logging.handlers.RotatingFileHandler(logfile, mode='a', maxBytes=10240, backupCount=3)  # Rotate after 10KB, leave 3 old logs
        if var.config.getboolean("bot", "redirect_stderr"):
            sys.stderr = util.LoggerIOWrapper(bot_logger, logging.INFO,
                                              fallback_io_buffer=sys.stderr.buffer)
    else:
        handler = logging.StreamHandler()

    util.set_logging_formatter(handler, bot_logger.level)
    bot_logger.addHandler(handler)
    logging.getLogger("root").addHandler(handler)
    var.bot_logger = bot_logger

    # ======================
    #     Load Database
    # ======================
    if args.user:
        username = args.user
    else:
        username = var.config.get("bot", "username")

    sanitized_username = "".join([x if x.isalnum() else "_" for x in username])
    var.settings_db_path = args.db if args.db is not None else util.solve_filepath(
        config.get("bot", "database_path") or f"settings-{sanitized_username}.db")
    var.music_db_path = args.music_db if args.music_db is not None else util.solve_filepath(
        config.get("bot", "music_database_path"))

    var.db = SettingsDatabase(var.settings_db_path)

    if var.config.get("bot", "save_music_library"):
        var.music_db = MusicDatabase(var.music_db_path)
    else:
        var.music_db = MusicDatabase(":memory:")

    DatabaseMigration(var.db, var.music_db).migrate()

    # Play log for the web interface statistics; lives next to the music
    # database (own table, created on first use).
    var.play_history = PlayHistoryDatabase(var.music_db.db_path)

    var.music_folder = util.solve_filepath(var.config.get('bot', 'music_folder'))
    if not var.music_folder.endswith(os.sep):
        # The file searching logic assumes that the music folder ends in a /
        var.music_folder = var.music_folder + os.sep
    var.tmp_folder = util.solve_filepath(var.config.get('bot', 'tmp_folder'))

    # ======================
    #      Translation
    # ======================

    lang = ""
    if args.lang:
        lang = args.lang
    else:
        lang = var.config.get('bot', 'language')

    if lang not in supported_languages:
        raise KeyError(f"Unsupported language {lang}")
    var.language = lang
    constants.load_lang(lang)

    # ======================
    #     Prepare Cache
    # ======================
    var.cache = MusicCache(var.music_db)

    if var.config.getboolean("bot", "refresh_cache_on_startup"):
        var.cache.build_dir_cache()

    # ======================
    #   Load playback mode
    # ======================
    playback_mode = None
    if var.db.has_option("playlist", "playback_mode"):
        playback_mode = var.db.get('playlist', 'playback_mode')
    else:
        playback_mode = var.config.get('bot', 'playback_mode')

    if playback_mode in ["one-shot", "repeat", "random", "autoplay"]:
        var.playlist = media.playlist.get_playlist(playback_mode)
    else:
        raise KeyError(f"Unknown playback mode '{playback_mode}'")

    # ======================
    #  Create bot instance
    # ======================
    var.bot = MumbleBot(args)
    commands.register_all_commands(var.bot)

    # load playlist
    if var.config.getboolean('bot', 'save_playlist'):
        var.bot_logger.info("bot: load playlist from previous session")
        var.playlist.load()

    # ============================
    #   Start the web interface
    # ============================
    if var.config.getboolean("webinterface", "enabled"):
        wi_addr = var.config.get("webinterface", "listening_addr")
        wi_port = var.config.getint("webinterface", "listening_port")
        tt = threading.Thread(
            target=start_web_interface, name="WebThread", args=(wi_addr, wi_port))
        tt.daemon = True
        bot_logger.info('Starting web interface on {}:{}'.format(wi_addr, wi_port))
        tt.start()

    # ============================
    #   Periodic cache cleanup
    # ============================
    var.cleaner = CacheCleaner()
    var.cleaner.start()

    # ============================
    #   Crash safety / watchdog
    # ============================
    # In Python an unhandled exception in a worker thread silently kills only
    # that thread, leaving a half-dead "zombie" bot that no restart policy can
    # detect. Turn such failures into a loud process exit so the supervisor
    # (systemd / Docker restart policy) can bring a fresh bot back.
    if hasattr(threading, "excepthook"):
        def _thread_excepthook(args):
            if args.exc_type is SystemExit:
                return
            bot_logger.critical(
                "bot: unhandled exception in thread %s, exiting for restart",
                args.thread.name if args.thread else "?",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
            os._exit(1)
        threading.excepthook = _thread_excepthook

    # Watchdog: if the main playback loop stops making progress (e.g. a stuck
    # ffmpeg read), force an exit so the supervisor restarts a fresh process.
    # Set [bot] watchdog_timeout = 0 to disable.
    watchdog_timeout = var.config.getint("bot", "watchdog_timeout", fallback=0)
    if watchdog_timeout > 0:
        def _watchdog():
            while not var.bot.exit:
                time.sleep(min(15, watchdog_timeout))
                if var.bot.exit:
                    return
                stalled = time.time() - var.bot.last_loop_at
                if stalled > watchdog_timeout:
                    bot_logger.critical(
                        "bot: playback loop stalled for %.0fs (> %ds), exiting for restart",
                        stalled, watchdog_timeout)
                    os._exit(1)
        threading.Thread(target=_watchdog, name="Watchdog", daemon=True).start()

    # Start the main loop.
    var.bot.loop()

