# coding=utf-8
import collections
import logging
import os
import re
import signal
import sys
import threading
import time
import traceback

from packaging import version

from mumble import Mumble
from mumble.constants import CONN_STATE

import util
import variables as var
from constants import tr_cli as tr

from .player import PlayerMixin


class MumbleBot(PlayerMixin):
    version = 'git'

    def __init__(self, args):
        self.log = logging.getLogger("bot")
        self.log.info(f"bot: botamusique version {self.get_version()}, starting...")
        signal.signal(signal.SIGINT, self.ctrl_caught)
        self.cmd_handle = {}

        self.stereo = var.config.getboolean('bot', 'stereo')

        if args.channel:
            self.channel = args.channel
        else:
            self.channel = var.config.get("server", "channel")

        var.user = args.user
        var.is_proxified = var.config.getboolean(
            "webinterface", "is_web_proxified")

        # Flags to indicate the bot is exiting (Ctrl-C, or !kill)
        self.exit = False
        self.nb_exit = 0

        # Related to ffmpeg thread
        self.thread = None
        self.thread_stderr = None
        self.read_pcm_size = 0
        self.pcm_buffer_size = 0
        self.last_ffmpeg_err = ""
        self._ffmpeg_stderr_lines = collections.deque(maxlen=20)

        # Single-instance guard so the same item never spawns two concurrent
        # download / progress-reporter threads (which would double every
        # download message in chat).
        self._active_downloads = set()
        self._download_lock = threading.Lock()

        # Liveness tracking for the watchdog and the (Docker) healthcheck.
        self.last_loop_at = time.time()
        self._last_heartbeat_write = 0.0
        self.heartbeat_file = os.environ.get("BAM_HEARTBEAT") or \
            os.path.join(var.tmp_folder, "botamusique.heartbeat")

        # Play/pause status
        self.is_pause = False
        self.pause_at_id = ""
        self.playhead = -1  # current position in a song.
        self.song_start_at = -1
        self.wait_for_ready = False  # flag for the loop are waiting for download to complete in the other thread

        #
        self.on_interrupting = False

        if args.host:
            host = args.host
        else:
            host = var.config.get("server", "host")

        if args.port:
            port = args.port
        else:
            port = var.config.getint("server", "port")

        if args.password:
            password = args.password
        else:
            password = var.config.get("server", "password")

        if args.channel:
            self.channel = args.channel
        else:
            self.channel = var.config.get("server", "channel")

        if args.certificate:
            certificate = args.certificate
        else:
            certificate = util.solve_filepath(var.config.get("server", "certificate"))

        if args.tokens:
            tokens = args.tokens
        else:
            tokens = var.config.get("server", "tokens")
            tokens = tokens.split(',')


        if args.user:
            self.username = args.user
        else:
            self.username = var.config.get("bot", "username")

        if args.bandwidth:
            self.bandwidth = args.bandwidth
        else:
            self.bandwidth = var.config.getint("bot", "bandwidth")

        self.mumble = Mumble(host, self.username, port=port, password=password, tokens=tokens,
                             stereo=self.stereo,
                             debug=var.config.getboolean('debug', 'mumble_connection'),
                             certfile=certificate or None)
        self.mumble.callbacks.text_message_received.set_handler(self.message_received)

        # Sound reception is decided per user queue in pymumble 2.x. Keep it
        # off unless ducking needs it (decoding every incoming packet is pure
        # CPU waste otherwise), and apply the current state to users that
        # appear later.
        self._receive_sound = False
        self.mumble.callbacks.user_created.set_handler(self._on_user_created)

        self.mumble.set_codec_profile("audio")
        self.mumble.start()  # start the mumble thread
        # wait for the connection; on failure the ready lock is never released,
        # so rely on the timeout instead of blocking forever.
        if not self.mumble.wait_until_connected(timeout=60) \
                or self.mumble.connected != CONN_STATE.CONNECTED:
            self.log.error("bot: failed to connect to the Mumble server.")
            sys.exit(1)

        self.set_comment()
        self.set_avatar()
        self.mumble.users.myself.self_mute = False  # by sure the user is not muted
        self.join_channel()
        self.mumble.set_bandwidth(self.bandwidth)

        bots = var.config.get("bot", "when_nobody_in_channel_ignore",fallback="")
        self.bots = set(bots.split(','))
        self._user_in_channel = self.get_user_count_in_channel()


        # ====== Volume ======
        self.volume_helper = util.VolumeHelper()

        max_vol = var.config.getfloat('bot', 'max_volume')
        if var.db.has_option('bot', 'max_volume'):
            max_vol = var.db.getfloat('bot', 'max_volume')                
        _volume = var.config.getfloat('bot', 'volume')
        if var.db.has_option('bot', 'volume'):
            _volume = var.db.getfloat('bot', 'volume')
        _volume = min(_volume, max_vol)
        self.volume_helper.set_volume(_volume)

        self.is_ducking = False
        self.on_ducking = False
        self.ducking_release = time.time()
        self.last_volume_cycle_time = time.time()

        self._ducking_volume = 0
        _ducking_volume = var.config.getfloat("bot", "ducking_volume")
        _ducking_volume = var.db.getfloat("bot", "ducking_volume", fallback=_ducking_volume)
        self.volume_helper.set_ducking_volume(_ducking_volume)

        self.ducking_threshold = var.config.getfloat("bot", "ducking_threshold")
        self.ducking_threshold = var.db.getfloat("bot", "ducking_threshold", fallback=self.ducking_threshold)

        self.ducking_delay = var.config.getfloat("bot", "ducking_delay")
        self.ducking_delay = var.db.getfloat("bot", "ducking_delay", fallback=self.ducking_delay)
        self.ducking_loud_since = 0

        if not var.db.has_option("bot", "ducking") and var.config.getboolean("bot", "ducking") \
                or var.config.getboolean("bot", "ducking"):
            self.is_ducking = True
            self.mumble.callbacks.sound_received.set_handler(self.ducking_sound_received)
            self.set_receive_sound(True)

        assert var.config.get("bot", "when_nobody_in_channel") in ['pause', 'pause_resume', 'stop', 'nothing', ''], \
            "Unknown action for when_nobody_in_channel"

        if var.config.get("bot", "when_nobody_in_channel") in ['pause', 'pause_resume', 'stop']:
            user_change_callback = \
                lambda user, action: threading.Thread(target=self.users_changed,
                                                      args=(user, action), daemon=True).start()
            self.mumble.callbacks.user_removed.set_handler(user_change_callback)
            self.mumble.callbacks.user_updated.set_handler(user_change_callback)

        # Debug use
        self._loop_status = 'Idle'
        self._display_rms = False
        self._max_rms = 0

        self.redirect_ffmpeg_log = var.config.getboolean('debug', 'redirect_ffmpeg_log')

        if var.config.getboolean("bot", "auto_check_update"):
            def check_update():
                nonlocal self
                new_version, changelog = util.check_update(self.get_version())
                if new_version:
                    self.send_channel_msg(tr('new_version_found', new_version=new_version, changelog=changelog))

            th = threading.Thread(target=check_update, name="UpdateThread")
            th.daemon = True
            th.start()

        last_startup_version = var.db.get("bot", "version", fallback=None)
        try:
            if not last_startup_version or version.parse(last_startup_version) < version.parse(self.version):
                var.db.set("bot", "version", self.version)
                if var.config.getboolean("bot", "auto_check_update"):
                    changelog = util.fetch_changelog()
                    self.send_channel_msg(tr("update_successful", version=self.version, changelog=changelog))
        except version.InvalidVersion:
            var.db.set("bot", "version", self.version)

    # Set the CTRL+C shortcut
    def ctrl_caught(self, signal, frame):
        self.log.info(
            "\nSIGINT caught, quitting, {} more to kill".format(2 - self.nb_exit))

        if var.config.getboolean('bot', 'save_playlist') \
                and var.config.get("bot", "save_music_library"):
            self.log.info("bot: save playlist into database")
            var.playlist.save()

        if self.nb_exit > 1:
            self.log.info("Forced Quit")
            sys.exit(0)
        self.nb_exit += 1

        self.exit = True

    def get_version(self):
        if self.version != "git":
            return self.version
        else:
            return util.get_snapshot_version()

    def register_command(self, cmd, handle, no_partial_match=False, access_outside_channel=False, admin=False):
        cmds = cmd.split(",")
        for command in cmds:
            command = command.strip()
            if command:
                self.cmd_handle[command] = {'handle': handle,
                                            'partial_match': not no_partial_match,
                                            'access_outside_channel': access_outside_channel,
                                            'admin': admin}
                self.log.debug("bot: command added: " + command)

    def set_comment(self):
        self.mumble.users.myself.comment = var.config.get('bot', 'comment')

    def set_avatar(self):
        avatar_path = var.config.get('bot', 'avatar')

        if avatar_path:
            with open(avatar_path, 'rb') as avatar_file:
                self.mumble.users.myself.texture = avatar_file.read()
        else:
            self.mumble.users.myself.texture = b''

    def set_receive_sound(self, value):
        # pymumble 2.x has no global receive toggle: reception is decided per
        # user audio queue. Remember the desired state and apply it to every
        # known user; _on_user_created applies it to users that appear later.
        self._receive_sound = value
        for user in self.mumble.users.by_session().values():
            if user.sound:
                user.sound.set_receive_sound(value)

    def _on_user_created(self, user):
        if user.sound:
            user.sound.set_receive_sound(self._receive_sound)

    def join_channel(self):
        if self.channel:
            if '/' in self.channel:
                self.mumble.channels.find_by_tree(self.channel.split('/')).move_in()
            else:
                self.mumble.channels.find_by_name(self.channel).move_in()

    # =======================
    #         Message
    # =======================

    # All text send to the chat is analysed by this function
    def message_received(self, text):
        raw_message = text.message.strip()
        message = re.sub(r'<.*?>', '', raw_message)
        if text.actor == 0:
            # Some server will send a welcome message to the bot once connected.
            # It doesn't have a valid "actor". Simply ignore it here.
            return

        user = self.mumble.users[text.actor].name

        if var.config.getboolean('commands', 'split_username_at_space'):
            # in can you use https://github.com/Natenom/mumblemoderator-module-collection/tree/master/os-suffixes ,
            # you want to split the username
            user = user.split()[0]

        command_symbols = var.config.get('commands', 'command_symbol')
        match = re.match(fr'^[{re.escape(command_symbols)}](?P<command>\S+)(?:\s(?P<argument>.*))?', message)
        if match:
            command = match.group("command").lower()
            argument = match.group("argument") or ""

            if not command:
                return

            self.log.info(f'bot: received command "{command}" with arguments "{argument}" from {user}')

            # Anti stupid guy function
            if not self.is_admin(user) and not var.config.getboolean('bot', 'allow_private_message') and text.session:
                self.mumble.users[text.actor].send_text_message(
                    tr('pm_not_allowed'))
                return

            for i in var.db.items("user_ban"):
                if user.lower() == i[0]:
                    self.mumble.users[text.actor].send_text_message(
                        tr('user_ban'))
                    return

            if not self.is_admin(user) and argument:
                input_url = util.get_url_from_input(argument)
                if input_url and var.db.has_option('url_ban', input_url):
                    self.mumble.users[text.actor].send_text_message(
                        tr('url_ban'))
                    return

            command_exc = ""
            try:
                if command in self.cmd_handle:
                    command_exc = command
                else:
                    # try partial match
                    cmds = self.cmd_handle.keys()
                    matches = []
                    for cmd in cmds:
                        if cmd.startswith(command) and self.cmd_handle[cmd]['partial_match']:
                            matches.append(cmd)

                    if len(matches) == 1:
                        self.log.info("bot: {:s} matches {:s}".format(command, matches[0]))
                        command_exc = matches[0]

                    elif len(matches) > 1:
                        self.mumble.users[text.actor].send_text_message(
                            tr('which_command', commands="<br>".join(matches)))
                        return
                    else:
                        self.mumble.users[text.actor].send_text_message(
                            tr('bad_command', command=command))
                        return

                if self.cmd_handle[command_exc]['admin'] and not self.is_admin(user):
                    self.mumble.users[text.actor].send_text_message(tr('not_admin'))
                    return

                if not self.cmd_handle[command_exc]['access_outside_channel'] \
                        and not self.is_admin(user) \
                        and not var.config.getboolean('bot', 'allow_other_channel_message') \
                        and self.mumble.users[text.actor].channel_id != self.mumble.users.myself.channel_id:
                    self.mumble.users[text.actor].send_text_message(
                        tr('not_in_my_channel'))
                    return

                self.cmd_handle[command_exc]['handle'](self, user, text, command_exc, argument)
            except:
                error_traceback = traceback.format_exc()
                error = error_traceback.rstrip().split("\n")[-1]
                self.log.error(f"bot: command {command_exc} failed with error: {error_traceback}\n")
                self.send_msg(tr('error_executing_command', command=command_exc, error=error), text)

    def send_msg(self, msg, text):
        msg = msg.encode('utf-8', 'ignore').decode('utf-8')
        # text if the object message, contain information if direct message or channel message
        self.mumble.users[text.actor].send_text_message(msg)

    def send_channel_msg(self, msg):
        msg = msg.encode('utf-8', 'ignore').decode('utf-8')
        own_channel = self.mumble.channels[self.mumble.users.myself.channel_id]
        own_channel.send_text_message(msg)

    @staticmethod
    def is_admin(user):
        list_admin = var.config.get('bot', 'admin').rstrip().split(';')
        if user in list_admin:
            return True
        else:
            return False

    # =======================
    #   Other Mumble Events
    # =======================

    def get_user_count_in_channel(self):
        # Get the channel, based on the channel id
        own_channel = self.mumble.channels[self.mumble.users.myself.channel_id]

        # Build set of unique usernames
        users = set([user.name for user in own_channel.get_users()])

        # Exclude all bots from the set of usernames
        users = users.difference(self.bots)

        # Return the number of elements in the set, as the final user count
        return len(users)


    def users_changed(self, user, message):
        # only check if there is one more user currently in the channel
        # else when the music is paused and somebody joins, music would start playing again
        user_count = self.get_user_count_in_channel()

        if user_count > self._user_in_channel and user_count == 2:
            if var.config.get("bot", "when_nobody_in_channel") == "pause_resume":
                self.resume()
            elif var.config.get("bot", "when_nobody_in_channel") == "pause" and self.is_pause:
                self.send_channel_msg(tr("auto_paused"))
        elif user_count == 1 and len(var.playlist) != 0:
            # if the bot is the only user left in the channel and the playlist isn't empty
            if var.config.get("bot", "when_nobody_in_channel") == "stop":
                self.log.info('bot: No user in my channel. Stop music now.')
                self.clear()
            else:
                self.log.info('bot: No user in my channel. Pause music now.')
                self.pause()

        self._user_in_channel = user_count
