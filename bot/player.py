# coding=utf-8
import audioop
import math
import struct
import subprocess as sp
import threading
import time

import variables as var
from constants import tr_cli as tr
from media.item import ValidationFailedError, PreparationFailedError


class PlayerMixin:
    """Playback half of MumbleBot: ffmpeg decoding, the send loop,
    download orchestration, volume/ducking and transport control."""

    # Hard cap on concurrent downloads started by the prefetcher, so a long
    # queue cannot saturate bandwidth or disk.
    PREFETCH_MAX_CONCURRENT = 2

    # =======================
    #   Launch and Download
    # =======================

    def _stream_enabled(self):
        return var.config.getboolean('bot', 'stream_while_downloading', fallback=False)

    def _stream_playable(self, wrapper):
        """True if stream-while-downloading is on and enough of the (still
        downloading) current item is on disk to play from the playhead."""
        if not self._stream_enabled():
            return False
        try:
            item = wrapper.item()
        except Exception:
            return False
        min_duration = var.config.getint('bot', 'stream_min_duration', fallback=300)
        if (getattr(item, 'duration', 0) or 0) < min_duration:
            return False
        buffer_secs = var.config.getint('bot', 'stream_buffer_seconds', fallback=30)
        return wrapper.playable_from(self.playhead, buffer_secs)

    def _stream_rewait(self, ffmpeg_rc):
        """Called when ffmpeg exited while the current item is still
        downloading (only possible after a streaming launch). Decide whether
        to keep waiting on this item instead of advancing the playlist.

        A clean exit (rc 0) after producing audio means ffmpeg simply caught
        up with the download: wait for more data, then relaunch from the
        playhead. Any other outcome means this container cannot be decoded
        while growing (e.g. mp4 with a trailing moov atom): flag the item so
        it waits for the full download instead of retrying forever."""
        if not self._stream_enabled() or var.playlist.current_index == -1:
            return False
        current = var.playlist.current_item()
        if not current:
            return False
        try:
            item = current.item()
        except Exception:
            return False
        if not getattr(item, 'downloading', False) or ffmpeg_rc in (-9, -15):
            return False  # not a streaming launch, or killed on purpose
        if ffmpeg_rc == 0 and self.read_pcm_size > 0:
            duration = getattr(item, 'duration', 0) or 0
            if self.playhead >= duration - 2:
                return False  # played to the (known) end already
            self.log.info("bot: streaming caught up with the download at "
                          "%.0fs, waiting for more data" % self.playhead)
        else:
            item.no_stream = True
            self.log.info("bot: streaming attempt failed (ffmpeg exit %s), "
                          "waiting for the full download" % ffmpeg_rc)
        # keep the playhead; the wait_for_ready branch relaunches from it
        self.wait_for_ready = True
        self.song_start_at = -1
        return True

    def launch_music(self, music_wrapper, start_from=0):
        assert music_wrapper.is_ready() or music_wrapper.playable_from(start_from, 0)

        uri = music_wrapper.uri()

        self.log.info("bot: play music " + music_wrapper.format_debug_string())

        if var.config.getboolean('bot', 'announce_current_music'):
            self.send_channel_msg(music_wrapper.format_current_playing())

        if var.config.getboolean('debug', 'ffmpeg'):
            ffmpeg_debug = "debug"
        else:
            ffmpeg_debug = "warning"

        channels = 2 if self.stereo else 1
        self.pcm_buffer_size = 960 * channels

        command = ["ffmpeg", '-v', ffmpeg_debug, '-nostdin', '-i', uri, '-ss', f"{start_from:f}",
                   # Decode audio only. Without this, ffmpeg may try to handle a
                   # video / cover-art stream from a container (mp4/mkv, or a
                   # YouTube/Bilibili "best" format) and fail to produce PCM -
                   # which used to take playback (and sometimes the bot) down.
                   '-vn', '-map', '0:a:0?']

        # Loudness normalization (EBU R128): keeps loud and quiet tracks at a
        # consistent volume so the bot can serve as background music without
        # anyone adjusting the volume by hand.
        if var.config.getboolean('bot', 'normalize_volume'):
            target = var.config.get('bot', 'normalize_volume_target')
            command += ['-af', f"loudnorm=I={target}:TP=-1.5:LRA=11"]

        command += ['-ac', str(channels), '-f', 's16le', '-ar', '48000', '-']
        self.log.debug("bot: execute ffmpeg command: " + " ".join(command))

        # Always capture stderr through a pipe and drain it in a dedicated
        # thread. ffmpeg (especially with loudnorm) can be very chatty; if its
        # stderr pipe is never read, the OS buffer fills up and ffmpeg blocks
        # mid-stream - the classic "long audio freezes the bot" deadlock.
        self.last_ffmpeg_err = ""
        self._ffmpeg_stderr_lines.clear()
        self.thread = sp.Popen(command, stdout=sp.PIPE, stderr=sp.PIPE, bufsize=self.pcm_buffer_size)
        stderr_thread = threading.Thread(
            target=self._drain_ffmpeg_stderr, name="ffmpeg-stderr", args=(self.thread,))
        stderr_thread.daemon = True
        stderr_thread.start()

    def _drain_ffmpeg_stderr(self, proc):
        # Continuously read ffmpeg's stderr so its pipe can never fill up and
        # block the process. Keep the last few lines around for error reporting.
        try:
            for raw_line in iter(proc.stderr.readline, b''):
                line = raw_line.decode('utf-8', 'replace').rstrip()
                if not line:
                    continue
                self._ffmpeg_stderr_lines.append(line)
                self.last_ffmpeg_err = "\n".join(self._ffmpeg_stderr_lines)
                if self.redirect_ffmpeg_log:
                    self.log.debug("ffmpeg: " + line)
        except Exception:
            pass
        finally:
            try:
                proc.stderr.close()
            except Exception:
                pass

    def async_download_next(self):
        # Function start if the next music isn't ready
        # Do nothing in case the next music is already downloaded
        self.log.debug("bot: Async download next asked ")
        while var.playlist.next_item():
            # usually, all validation will be done when adding to the list.
            # however, for performance consideration, youtube playlist won't be validate when added.
            # the validation has to be done here.
            next = var.playlist.next_item()
            try:
                if not next.is_ready():
                    self.async_download(next)

                break
            except ValidationFailedError as e:
                self.send_channel_msg(e.msg)
                var.playlist.remove_by_id(next.id)
                var.cache.free_and_delete(next.id)

        self._prefetch_upcoming()

    def _prefetch_upcoming(self):
        """Pre-download items beyond the immediate next one so queue
        advances don't stall on downloads. The window size comes from
        prefetch_count; at most PREFETCH_MAX_CONCURRENT downloads run at
        once (the _active_downloads guard also dedupes against the
        download the loop itself started)."""
        count = var.config.getint('bot', 'prefetch_count', fallback=2)
        if count <= 1:
            return
        for wrapper in var.playlist.upcoming_items(count)[1:]:
            with self._download_lock:
                if len(self._active_downloads) >= self.PREFETCH_MAX_CONCURRENT:
                    break
            try:
                if not wrapper.is_ready():
                    self.async_download(wrapper)
            except ValidationFailedError as e:
                # same cleanup path async_download_next uses
                self.send_channel_msg(e.msg)
                var.playlist.remove_by_id(wrapper.id)
                var.cache.free_and_delete(wrapper.id)

    def async_download(self, item):
        # Guard against the same item being downloaded twice concurrently:
        # otherwise a second call would spawn another progress-reporter thread
        # and every download message would appear twice in chat.
        with self._download_lock:
            if item.id in self._active_downloads:
                self.log.debug("bot: download already running for %s, not starting another", item.id[:7])
                return None
            self._active_downloads.add(item.id)

        th = threading.Thread(
            target=self._download, name="Prepare-" + item.id[:7], args=(item,))
        self.log.info(f"bot: start preparing item in thread: {item.format_debug_string()}")
        th.daemon = True
        th.start()
        # Announce progress in chat for downloads slow enough to matter.
        reporter = threading.Thread(
            target=self._download_progress_reporter,
            name="Progress-" + item.id[:7], args=(item,))
        reporter.daemon = True
        reporter.start()
        return th

    def start_download(self, item):
        if not item.is_ready():
            self.log.info("bot: current music isn't ready, start downloading.")
            self.async_download(item)
            self.send_channel_msg(
                tr('download_in_progress', item=item.format_title()))

    def _download(self, item):
        try:
            ver = item.version
            try:
                item.validate()
                if item.is_ready():
                    return True
            except ValidationFailedError as e:
                self.send_channel_msg(e.msg)
                var.playlist.remove_by_id(item.id)
                var.cache.free_and_delete(item.id)
                return False

            try:
                item.prepare()
                if item.version > ver:
                    var.playlist.version += 1
                return True
            except PreparationFailedError as e:
                self.send_channel_msg(e.msg)
                return False
        finally:
            with self._download_lock:
                self._active_downloads.discard(item.id)

    def _download_progress_reporter(self, wrapper):
        # Announce download progress in chat, but only for downloads slow
        # enough to matter (e.g. a multi-hour video). Short downloads finish
        # within the grace period and produce no messages at all.
        grace = 25          # seconds of silence before the first message
        poll = 4
        min_gap = 20        # minimum seconds between progress messages
        max_wait = 7200     # safety cap

        start = time.time()
        try:
            item = wrapper.item()
        except Exception:
            return
        if not hasattr(item, 'progress'):
            return          # not a downloadable URL item

        reported_any = False
        announced_unknown = False
        last_msg_time = 0.0
        next_milestone = 0.25

        while not self.exit and time.time() - start < max_wait:
            try:
                if item.is_ready() or item.is_failed():
                    break
            except Exception:
                break

            now = time.time()
            if now - start >= grace and getattr(item, 'downloading', False):
                progress = getattr(item, 'progress', 0.0) or 0.0
                title = getattr(item, 'title', '') or getattr(item, 'url', '') or '...'
                if progress <= 0.0:
                    if not announced_unknown and now - last_msg_time >= min_gap:
                        self.send_channel_msg(tr('download_progress_start', item=title))
                        announced_unknown = True
                        reported_any = True
                        last_msg_time = now
                elif progress + 1e-6 >= next_milestone and now - last_msg_time >= min_gap:
                    self.send_channel_msg(tr('download_progress', item=title,
                                             percent=int(progress * 100)))
                    reported_any = True
                    last_msg_time = now
                    while next_milestone <= progress:
                        next_milestone += 0.25
            time.sleep(poll)

        if reported_any:
            try:
                finished = item.is_ready()
            except Exception:
                finished = False
            if finished:
                title = getattr(item, 'title', '') or getattr(item, 'url', '') or '...'
                self.send_channel_msg(tr('download_finished', item=title))

    # =======================
    #          Loop
    # =======================

    # Main loop of the Bot
    def loop(self):
        while not self.exit and self.mumble.is_alive():
            self.last_loop_at = time.time()
            self._write_heartbeat()
            try:
                self._loop_iteration()
            except Exception:
                # A failure while playing one item must never take the whole bot
                # down. Log it, drop the current ffmpeg process and skip ahead.
                self.log.exception("bot: unexpected error in playback loop; skipping current item")
                if self.thread:
                    try:
                        self.thread.kill()
                    except Exception:
                        pass
                    self.thread = None
                self.read_pcm_size = 0
                self.wait_for_ready = False
                # Don't advance the playlist here: with thread=None and
                # wait_for_ready=False, the next normal iteration calls
                # playlist.next() exactly once, skipping the offending item.
                time.sleep(0.1)

        while self.mumble.send_audio.get_buffer_size() > 0 and self.mumble.is_alive():
            # Empty the buffer before exit
            time.sleep(0.01)
        time.sleep(0.5)

        if self.exit:
            self._loop_status = "exited"
            if var.config.getboolean('bot', 'save_playlist') \
                    and var.config.get("bot", "save_music_library"):
                self.log.info("bot: save playlist into database")
                var.playlist.save()

    def _write_heartbeat(self):
        # Touch a heartbeat file every few seconds so an external healthcheck
        # (e.g. Docker) can confirm the main loop is still alive.
        now = time.time()
        if now - self._last_heartbeat_write < 5:
            return
        self._last_heartbeat_write = now
        try:
            with open(self.heartbeat_file, "w") as f:
                f.write(str(now))
        except Exception:
            pass

    def _loop_iteration(self):
        while self.thread and self.mumble.send_audio.get_buffer_size() > 0.5 and not self.exit:
            # If the buffer isn't empty, I cannot send new music part, so I wait
            self._loop_status = f'Wait for buffer {self.mumble.send_audio.get_buffer_size():.3f}'
            time.sleep(0.01)

        raw_music = None
        if self.thread:
            # I get raw from ffmpeg thread
            # move playhead forward
            self._loop_status = 'Reading raw'
            if self.song_start_at == -1:
                self.song_start_at = time.time() - self.playhead
            self.playhead = time.time() - self.song_start_at

            raw_music = self.thread.stdout.read(self.pcm_buffer_size)
            # Capture whether this is the very first chunk of the song *before*
            # bumping the counter. Otherwise read_pcm_size is already > 0 by the
            # time the fade-in test runs, so the fade-in branch is dead code.
            is_first_chunk = self.read_pcm_size == 0
            self.read_pcm_size += len(raw_music)

            if raw_music:
                # Adjust the volume and send it to mumble
                self.volume_cycle()

                if is_first_chunk and not self.on_interrupting \
                        and len(raw_music) == self.pcm_buffer_size:
                    # First full chunk of a freshly started song: fade in so the
                    # attack doesn't begin on a hard discontinuity (click/pop).
                    # Must be tested before the normal full-chunk branch below,
                    # which would otherwise swallow it.
                    self.mumble.send_audio.add_sound(
                        audioop.mul(self._fadeout(raw_music, self.stereo, fadein=True), 2, self.volume_helper.real_volume))
                elif not self.on_interrupting and len(raw_music) == self.pcm_buffer_size:
                    self.mumble.send_audio.add_sound(
                        audioop.mul(raw_music, 2, self.volume_helper.real_volume))
                elif self.on_interrupting or len(raw_music) < self.pcm_buffer_size:
                    self.mumble.send_audio.add_sound(
                        audioop.mul(self._fadeout(raw_music, self.stereo, fadein=False), 2, self.volume_helper.real_volume))
                    self.thread.kill()
                    self.thread = None
                    time.sleep(0.1)
                    self.on_interrupting = False
            else:
                time.sleep(0.1)
        else:
            time.sleep(0.1)

        if not self.is_pause and not raw_music:
            # bot is not paused, but the ffmpeg thread produced no audio: the
            # song finished, the bot just resumed from pause, or ffmpeg died.
            ffmpeg_rc = None
            if self.thread:
                try:
                    ffmpeg_rc = self.thread.wait(timeout=1)
                except Exception:
                    # Still running (e.g. an interrupt that didn't go through the
                    # fadeout branch). We force-kill it ourselves, so this is not
                    # a decode failure - leave ffmpeg_rc as None so it isn't
                    # mistaken for a bad codec (Windows kill() returns 1, not -9).
                    try:
                        self.thread.kill()
                    except Exception:
                        pass
            self.thread = None
            self.on_interrupting = False

            # Stream-while-downloading: if ffmpeg exited while the current
            # item is still downloading, wait for more data instead of
            # advancing (or misreading the early EOF as a decode failure).
            if ffmpeg_rc is not None and self._stream_rewait(ffmpeg_rc):
                self.last_ffmpeg_err = ""
                return

            # A non-zero ffmpeg exit code that we didn't cause ourselves
            # (SIGKILL=-9 / SIGTERM=-15) means the track failed to decode - an
            # unsupported codec, or a corrupt / truncated file.
            if ffmpeg_rc is not None and ffmpeg_rc not in (0, -9, -15) \
                    and var.playlist.current_index != -1:
                current = var.playlist.current_item()
                if current:
                    self.log.error("bot: cannot play music %s (ffmpeg exit %s)",
                                   current.format_debug_string(), ffmpeg_rc)
                    if self.last_ffmpeg_err:
                        self.log.error("bot: ffmpeg said: %s", self.last_ffmpeg_err)
                    self.send_channel_msg(tr('unable_play', item=current.format_title()))
                    var.playlist.remove_by_id(current.id)
                    var.cache.free_and_delete(current.id)
            self.last_ffmpeg_err = ""

            # move to the next song.
            if not self.wait_for_ready:  # if wait_for_ready flag is not true, move to the next song.
                if var.playlist.next():
                    current = var.playlist.current_item()
                    self.log.debug(f"bot: next into the song: {current.format_debug_string()}")
                    try:
                        self.start_download(current)
                        self.wait_for_ready = True

                        self.song_start_at = -1
                        self.playhead = 0

                    except ValidationFailedError as e:
                        self.send_channel_msg(e.msg)
                        var.playlist.remove_by_id(current.id)
                        var.cache.free_and_delete(current.id)
                else:
                    self._loop_status = 'Empty queue'
            else:
                # if wait_for_ready flag is true, means the pointer is already
                # pointing to target song. start playing
                current = var.playlist.current_item()
                if current:
                    if current.is_ready():
                        self.wait_for_ready = False
                        self.read_pcm_size = 0

                        self.launch_music(current, self.playhead)
                        self.last_volume_cycle_time = time.time()
                        self.async_download_next()
                    elif current.is_failed():
                        var.playlist.remove_by_id(current.id)
                        self.wait_for_ready = False
                    elif self._stream_playable(current):
                        # enough of the download is on disk: start playing
                        # from the playhead while yt-dlp keeps writing
                        self.wait_for_ready = False
                        self.read_pcm_size = 0

                        self.launch_music(current, self.playhead)
                        self.last_volume_cycle_time = time.time()
                        # note: no async_download_next() here - the current
                        # item is still downloading, don't compete with it
                    else:
                        self._loop_status = 'Wait for the next item to be ready'
                else:
                    self.wait_for_ready = False

    def volume_cycle(self):
        delta = time.time() - self.last_volume_cycle_time

        if self.on_ducking and self.ducking_release < time.time():
            self.on_ducking = False
            self._max_rms = 0

        if delta > 0.001:
            if self.is_ducking and self.on_ducking:
                self.volume_helper.real_volume = \
                    (self.volume_helper.real_volume - self.volume_helper.ducking_volume_set) * math.exp(- delta / 0.2) \
                    + self.volume_helper.ducking_volume_set
            else:
                self.volume_helper.real_volume = self.volume_helper.volume_set - \
                                                 (self.volume_helper.volume_set - self.volume_helper.real_volume) * math.exp(- delta / 0.5)

            self.last_volume_cycle_time = time.time()

    def ducking_sound_received(self, user, sound):
        rms = audioop.rms(sound.pcm, 2)
        self._max_rms = max(rms, self._max_rms)
        if self._display_rms:
            if rms < self.ducking_threshold:
                print('%6d/%6d  ' % (rms, self._max_rms) + '-' * int(rms / 200), end='\r')
            else:
                print('%6d/%6d  ' % (rms, self._max_rms) + '-' * int(self.ducking_threshold / 200)
                      + '+' * int((rms - self.ducking_threshold) / 200), end='\r')

        if rms > self.ducking_threshold:
            now = time.time()
            # A gap longer than the release window means this is the start
            # of a fresh burst of noise.
            if not self.on_ducking and now > self.ducking_release:
                self.ducking_loud_since = now
            self.ducking_release = now + 1  # ducking release after 1s

            # Only duck once the noise has lasted at least ducking_delay
            # seconds, so brief background noises don't dip the music.
            if now - self.ducking_loud_since >= self.ducking_delay:
                if self.on_ducking is False:
                    self.log.debug("bot: ducking triggered")
                    self.on_ducking = True

    def _fadeout(self, _pcm_data, stereo=False, fadein=False):
        pcm_data = bytearray(_pcm_data)
        # Drop any trailing bytes that don't form a complete sample frame
        # (4 bytes in stereo, 2 in mono) so the struct.unpack calls below can
        # never read past the end of a partial final buffer and raise.
        frame = 4 if stereo else 2
        pcm_data = pcm_data[:len(pcm_data) - (len(pcm_data) % frame)]
        if stereo:
            if not fadein:
                mask = [math.exp(-x / 60) for x in range(0, int(len(pcm_data) / 4))]
            else:
                mask = [math.exp(-x / 60) for x in reversed(range(0, int(len(pcm_data) / 4)))]

            for i in range(int(len(pcm_data) / 4)):
                pcm_data[4 * i:4 * i + 2] = struct.pack("<h",
                                                        round(struct.unpack("<h", pcm_data[4 * i:4 * i + 2])[0] * mask[i]))
                pcm_data[4 * i + 2:4 * i + 4] = struct.pack("<h", round(
                    struct.unpack("<h", pcm_data[4 * i + 2:4 * i + 4])[0] * mask[i]))
        else:
            if not fadein:
                mask = [math.exp(-x / 60) for x in range(0, int(len(pcm_data) / 2))]
            else:
                mask = [math.exp(-x / 60) for x in reversed(range(0, int(len(pcm_data) / 2)))]

            for i in range(int(len(pcm_data) / 2)):
                pcm_data[2 * i:2 * i + 2] = struct.pack("<h",
                                                        round(struct.unpack("<h", pcm_data[2 * i:2 * i + 2])[0] * mask[i]))

        # Return exactly the faded data. The old upstream code appended
        # bytes(len(pcm_data)) - an equal-length run of zero bytes (silence) -
        # which doubled every faded chunk and injected a ~10 ms silent gap at
        # each song start / fade edge.
        return bytes(pcm_data)

    # =======================
    #      Play Control
    # =======================

    def play(self, index=-1, start_at=0):
        if not self.is_pause:
            self.interrupt()

        if index != -1:
            var.playlist.point_to(index)

        current = var.playlist.current_item()

        self.start_download(current)
        self.is_pause = False
        self.wait_for_ready = True
        self.song_start_at = -1
        self.playhead = start_at

    def clear(self):
        # Kill the ffmpeg thread and empty the playlist
        self.interrupt()
        var.playlist.clear()
        self.wait_for_ready = False
        self.log.info("bot: music stopped. playlist trashed.")

    def stop(self):
        self.interrupt()
        self.is_pause = True
        if len(var.playlist) > 0:
            self.wait_for_ready = True
        else:
            self.wait_for_ready = False
        self.log.info("bot: music stopped.")

    def interrupt(self):
        # Kill the ffmpeg thread
        if self.thread:
            self.on_interrupting = True

            time.sleep(0.1)
            self.song_start_at = -1
            self.read_pcm_size = 0

    def pause(self):
        # Kill the ffmpeg thread
        self.interrupt()
        self.is_pause = True
        self.song_start_at = -1
        if len(var.playlist) > 0:
            self.pause_at_id = var.playlist.current_item().id
            self.log.info(f"bot: music paused at {self.playhead:.2f} seconds.")

    def resume(self):
        self.is_pause = False
        if var.playlist.current_index == -1:
            var.playlist.next()
            self.playhead = 0
            return

        music_wrapper = var.playlist.current_item()

        if not music_wrapper or not music_wrapper.id == self.pause_at_id or \
                not (music_wrapper.is_ready() or self._stream_playable(music_wrapper)):
            self.playhead = 0
            return

        self.wait_for_ready = True
        self.pause_at_id = ""
