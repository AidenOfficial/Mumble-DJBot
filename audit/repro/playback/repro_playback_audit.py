# coding=utf-8
"""Playback-core audit repros. Run from the repo root:
   cd <repo-root> && python -m pytest -q <this file>
Each test asserts the CURRENT (buggy) behaviour, so a passing test == bug reproduced."""
import collections
import configparser
import logging
import os
import sys
import threading
import time

REPO = os.getcwd()
sys.path.insert(0, REPO)

import pytest  # noqa: E402
import constants  # noqa: E402
import variables as var  # noqa: E402
from bot.player import PlayerMixin  # noqa: E402
from media import playlist as mp  # noqa: E402
from media.cache import CachedItemWrapper, ItemNotCachedError  # noqa: E402


# ---------------------------------------------------------------- fakes
def make_config():
    c = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    for s in ('bot', 'debug', 'youtube_dl'):
        c.add_section(s)
    for k, v in {'announce_current_music': 'False', 'normalize_volume': 'False',
                 'stream_while_downloading': 'False', 'prefetch_count': '2',
                 'save_playlist': 'False'}.items():
        c.set('bot', k, v)
    c.set('debug', 'ffmpeg', 'False')
    return c


class FakeItem:
    def __init__(self, id_, ready='yes'):
        self.id = id_
        self.ready = ready
        self.type = 'url'
        self.duration = 100
        self.downloading = False
        self.version = 0

    def is_ready(self):
        return self.ready == 'yes' and not self.downloading

    def is_failed(self):
        return self.ready == 'failed'

    def validate(self):
        return True

    def prepare(self):
        return True

    def uri(self):
        return '/music/' + self.id

    def format_title(self):
        return self.id

    def format_debug_string(self):
        return self.id

    def to_dict(self):
        return {'id': self.id}


class FakeCache(dict):
    def save(self, id):
        pass

    def free(self, id):
        self.pop(id, None)

    def free_all(self):
        self.clear()

    def free_and_delete(self, id):
        self.pop(id, None)

    def get_item_by_id(self, id):
        return self.get(id)


def W(id_, ready='yes', user='u'):
    """Real CachedItemWrapper backed by the fake cache."""
    if id_ not in var.cache:
        var.cache[id_] = FakeItem(id_, ready)
    return CachedItemWrapper(var.cache, id_, 'url', user)


def fill(pl, wrappers, index):
    list.extend(pl, wrappers)   # bypass append(): no validation thread
    pl.current_index = index
    return pl


class FakeSendAudio:
    def get_buffer_size(self):
        return 0.0

    def add_sound(self, pcm):
        pass


class FakeMumble:
    def __init__(self):
        self.send_audio = FakeSendAudio()

    def is_alive(self):
        return True


class FakeStdout:
    def __init__(self, data=b''):
        self.data = data

    def read(self, n):
        d, self.data = self.data[:n], self.data[n:]
        return d


class FakeProc:
    """An ffmpeg that already hit EOF and exited with `rc`."""
    def __init__(self, rc):
        self.rc = rc
        self.stdout = FakeStdout()

    def wait(self, timeout=None):
        return self.rc

    def kill(self):
        pass


class Player(PlayerMixin):
    def __init__(self):
        self.log = logging.getLogger('audit')
        self.stereo = True
        self.mumble = FakeMumble()
        self.exit = False
        self.thread = None
        self.read_pcm_size = 0
        self.pcm_buffer_size = 1920
        self.last_ffmpeg_err = ''
        self._ffmpeg_stderr_lines = collections.deque(maxlen=20)
        self._active_downloads = set()
        self._download_lock = threading.Lock()
        self.is_pause = False
        self.pause_at_id = ''
        self.playhead = 0
        self.song_start_at = -1
        self.wait_for_ready = False
        self.on_interrupting = False
        self.messages = []
        self.launched = []
        self.downloads = []

    def send_channel_msg(self, msg):
        self.messages.append(msg)

    # no real ffmpeg / download threads
    def launch_music(self, wrapper, start_from=0):
        self.launched.append(wrapper.id)
        self.thread = None  # pretend it played and finished instantly

    def async_download(self, item):
        self.downloads.append(item.id)

    def async_download_next(self):
        pass

    def run_iterations(self, n):
        errors = []
        for _ in range(n):
            try:
                self._loop_iteration()
            except Exception as e:   # mirrors loop()'s except-handler
                errors.append(e)
                self.thread = None
                self.read_pcm_size = 0
                self.wait_for_ready = False
        return errors


@pytest.fixture(autouse=True)
def env(monkeypatch):
    saved = {k: getattr(var, k, None) for k in ('config', 'playlist', 'cache', 'play_history', 'bot')}
    var.config = make_config()
    var.cache = FakeCache()
    var.play_history = None
    var.bot = None
    constants.load_lang('en_US')
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    yield
    for k, v in saved.items():
        setattr(var, k, v)


def ids(pl):
    return [w.id for w in pl]


# ------------------------------------------------- F1 remove_by_id shifting
def test_F1_remove_by_id_with_duplicates_removes_wrong_items():
    # "!repeat 3" on A inserts A three more times -> [A, A, A, A, B, C]
    pl = fill(mp.RepeatPlaylist(), [W('A'), W('A'), W('A'), W('A'), W('B'), W('C')], 0)
    var.playlist = pl
    pl.remove_by_id('A')            # what the ffmpeg-failure path does (player.py:749)
    var.cache.free_and_delete('A')  # player.py:750
    assert ids(pl) == ['A', 'A', 'C']   # B silently deleted, two A's survive
    with pytest.raises(ItemNotCachedError):
        pl[0].item()                    # surviving A wrappers now point to a freed cache entry


# ------------------------------------- F2 removing current then next() skips
def _decode_failure(player):
    player.thread = FakeProc(rc=1)   # ffmpeg exited non-zero (bad codec / corrupt)
    player.wait_for_ready = False
    player.run_iterations(1)         # failure path: remove_by_id + next()


def test_F2_oneshot_decode_failure_drops_following_song():
    var.playlist = fill(mp.OneshotPlaylist(), [W('A'), W('B'), W('C')], 0)
    p = Player()
    _decode_failure(p)
    assert ids(var.playlist) == ['C']          # B was never played, just deleted
    assert var.playlist.current_item().id == 'C'


def test_F2_repeat_decode_failure_skips_following_song():
    var.playlist = fill(mp.RepeatPlaylist(), [W('A'), W('B'), W('C')], 0)
    p = Player()
    _decode_failure(p)
    assert var.playlist.current_item().id == 'C'   # expected B


def test_F2_is_failed_branch_also_skips():
    var.playlist = fill(mp.RepeatPlaylist(), [W('A', ready='failed'), W('B'), W('C')], 0)
    p = Player()
    p.wait_for_ready = True
    p.run_iterations(2)                 # iter1: remove A; iter2: next()
    assert p.launched == [] and var.playlist.current_item().id == 'C'   # B skipped


# ----------------------------- F3 single mode: failing last item -> IndexError loop
def test_F3_single_mode_failure_of_last_item_wedges_loop():
    var.playlist = fill(mp.SingleLoopPlaylist(), [W('A'), W('B')], 1)
    p = Player()
    p.thread = FakeProc(rc=1)
    errors = p.run_iterations(50)
    assert len(errors) >= 49 and all(isinstance(e, IndexError) for e in errors)
    assert p.launched == []             # never plays A again on its own
    assert var.playlist.current_index == 1 and len(var.playlist) == 1


# ----------------- F4 wait_for_ready branch never (re)starts a download
def test_F4_skip_while_paused_then_resume_waits_forever():
    items = [W('A')] + [W(x, ready='validated') for x in 'BCDE']
    var.playlist = fill(mp.RepeatPlaylist(), items, 0)
    p = Player()
    p.pause_at_id = 'A'
    p.is_pause = True
    # three "!skip" while paused (commands/playback.py:112-114)
    for _ in range(3):
        var.playlist.skip_current()
        var.playlist.next()
        p.wait_for_ready = True
    p.resume()                           # id mismatch -> playhead=0, return
    p.run_iterations(1000)
    assert var.playlist.current_item().id == 'D'
    assert p.launched == [] and p.downloads == []      # nobody ever downloads D
    assert p._loop_status == 'Wait for the next item to be ready'


def test_F4_remove_current_while_waiting_replays_previous():
    # web _queue_remove / !remove of the item the bot is waiting for
    var.playlist = fill(mp.RepeatPlaylist(), [W('A'), W('B', ready='validated'), W('C')], 1)
    p = Player()
    p.wait_for_ready = True            # waiting for B's download, thread None
    import web_api
    var.bot = p
    web_api._queue_remove(1)           # the real handler behind POST /api/queue remove
    p.run_iterations(1)
    assert p.launched == ['A']         # replays A instead of moving on to C


def test_F4_remove_first_item_while_waiting_plays_last_item_repeat_mode():
    var.playlist = fill(mp.RepeatPlaylist(), [W('A', ready='validated'), W('B'), W('C')], 0)
    p = Player()
    p.wait_for_ready = True
    import web_api
    var.bot = p
    web_api._queue_remove(0)           # index 0 -> current_index becomes -1
    p.run_iterations(1)
    assert p.launched == ['C']         # current_item() with index -1 == self[-1]


# --------------------------- F5 resume() with current_index == -1 skips item 0
def test_F5_resume_from_minus_one_skips_first_song_repeat():
    var.playlist = fill(mp.RepeatPlaylist(), [W('A'), W('B')], -1)
    p = Player()
    p.is_pause = True
    p.resume()
    p.run_iterations(1)
    assert p.launched == [] and var.playlist.current_index == 1   # A skipped


def test_F5_resume_from_minus_one_drops_first_song_oneshot():
    var.playlist = fill(mp.OneshotPlaylist(), [W('A'), W('B')], -1)
    p = Player()
    p.is_pause = True
    p.resume()
    p.run_iterations(1)
    assert ids(var.playlist) == ['B']   # A deleted without being played


# ---------------- F6 re-adding a URL whose old download is still running
def test_F6_readd_during_download_never_becomes_ready():
    class RealDownloadPlayer(Player):
        async_download = PlayerMixin.async_download  # real dedupe guard

        def _download(self, item):
            started.set()
            release.wait(5)
            old.ready = 'yes'                       # the ORPHANED object gets the file
            with self._download_lock:
                self._active_downloads.discard(item.id)

        def _download_progress_reporter(self, wrapper):
            pass

    started, release = threading.Event(), threading.Event()
    var.playlist = fill(mp.RepeatPlaylist(), [], -1)
    p = RealDownloadPlayer()
    w1 = W('X', ready='validated')
    old = var.cache['X']
    list.append(var.playlist, w1)
    var.playlist.next()
    p.start_download(w1)                            # Prepare-X thread running
    assert started.wait(2)
    var.playlist.clear()                            # !clear -> cache.free_all()
    # re-add: get_item() reloads from the DB -> a NEW object, ready='validated'
    var.cache['X'] = FakeItem('X', ready='validated')
    w2 = CachedItemWrapper(var.cache, 'X', 'url', 'u')
    list.append(var.playlist, w2)
    var.playlist.next()
    p.start_download(w2)                            # deduped: "already running"
    p.wait_for_ready = True
    release.set()
    time.sleep(0)  # (patched) - give the thread a chance
    for t in threading.enumerate():
        if t.name.startswith('Prepare-'):
            t.join(2)
    assert old.ready == 'yes' and not w2.is_ready()
    p.run_iterations(500)
    assert p.launched == []                         # waits forever on the new object
    assert 'X' not in p._active_downloads


# ---------------- F7 play(index) racing a natural end-of-song plays wrong item
def test_F7_play_index_races_natural_end():
    var.playlist = fill(mp.RepeatPlaylist(), [W(x, ready='validated') for x in 'ABCDEFG'], 0)
    p = Player()
    p.thread = FakeProc(rc=0)                     # song A just reached EOF
    in_send, go = threading.Event(), threading.Event()
    calls = []

    def slow_send(msg):                           # first chat message is slow
        calls.append(msg)
        if len(calls) == 1:
            in_send.set()
            go.wait(5)
    p.send_channel_msg = slow_send

    user = threading.Thread(target=p.play, args=(5,))   # "!play 6"
    user.start()
    assert in_send.wait(2)        # user thread: interrupt + point_to(5) done, inside start_download
    p.run_iterations(1)           # main loop: EOF -> wait_for_ready still False -> next()
    go.set()
    user.join(2)
    assert var.playlist.current_index == 6        # user asked for index 5
    assert p.wait_for_ready is True


# ------------- F8 URLItem.validate() during an in-flight streaming download
def test_F8_validate_mid_download_deletes_growing_file(tmp_path, monkeypatch):
    import media.url as mu

    class DB:
        def has_option(self, *a):
            return False
    monkeypatch.setattr(var, 'db', DB(), raising=False)
    monkeypatch.setattr(var, 'tmp_folder', str(tmp_path) + os.sep, raising=False)
    var.config.set('bot', 'max_track_duration', '0')
    item = mu.URLItem('https://example.com/v')
    # state while _download() is running with stream_while_downloading=True
    item.ready, item.downloading, item.duration = 'preparing', True, 3600
    open(item.path, 'wb').write(b'x' * 1000)          # growing file (nopart)
    open(item.path + '.incomplete', 'w').close()       # marker
    monkeypatch.setattr(item, '_get_info_from_url', lambda: True)  # no network
    # A duplicate add of the same URL / "!repeat" puts the SAME item object in
    # pending_items; the validation thread then calls:
    assert item.validate() is True
    assert not os.path.exists(item.path)               # file being downloaded/streamed: gone
    assert item.ready == 'validated' and item.downloading is True
    # when yt-dlp finishes (writing into the unlinked inode) _download sets ready='yes';
    item.ready, item.downloading = 'yes', False
    assert item.is_ready() is False and item.ready == 'validated'   # "file missed" -> waits forever (F4)


# -------- F5b/F5c other resume() early returns leave wait_for_ready False
def test_F5b_remove_current_while_paused_then_resume_skips_next():
    import web_api
    var.playlist = fill(mp.RepeatPlaylist(), [W('A'), W('B'), W('C')], 0)
    p = Player()
    var.bot = p
    p.pause()                       # paused on A (thread None -> interrupt no-op)
    web_api._queue_remove(0)        # paused: no interrupt, no index fix-up -> current = B
    p.resume()                      # pause_at_id 'A' != 'B' -> playhead=0, return
    p.run_iterations(1)
    assert var.playlist.current_item().id == 'C'   # B skipped
    assert p.launched == []


def test_F5c_resume_of_partially_downloaded_stream_skips_song():
    var.playlist = fill(mp.RepeatPlaylist(), [W('A', ready='preparing'), W('B')], 0)
    var.cache['A'].downloading = True
    p = Player()
    p.playhead = 400.0              # paused mid-song in a streaming session
    p.pause()
    # resume while < playhead+30s is on disk (_stream_playable False)
    p.resume()
    p.run_iterations(1)
    assert var.playlist.current_item().id == 'B' and p.playhead == 0


# ------------------ F9 resume()'s flag ordering races the main loop
def test_F9_resume_publishes_is_pause_before_wait_for_ready():
    var.playlist = fill(mp.RepeatPlaylist(), [W('A'), W('B')], 0)
    p = Player()
    p.pause()                        # paused on A, wait_for_ready False, thread None
    orig = var.playlist.current_item
    fired = []

    def current_item_with_preemption():
        if not fired:                # GIL switch right after resume() line 935
            fired.append(1)
            p.run_iterations(1)      # main loop sees is_pause False, wait_for_ready False
        return orig()
    var.playlist.current_item = current_item_with_preemption
    p.resume()
    assert var.playlist.current_index == 1   # A skipped; resume then hit id-mismatch


# ------------- F10 one-shot: add within 0.1s of queue end is deleted
def test_F10_oneshot_item_added_right_after_queue_end_is_dropped():
    var.playlist = fill(mp.OneshotPlaylist(), [W('A')], 0)
    p = Player()
    p.thread = FakeProc(rc=0)        # A ended
    p.run_iterations(1)              # next(): deletes A, list empty, current_index stays 0
    assert var.playlist.current_index == 0 and len(var.playlist) == 0
    list.append(var.playlist, W('B'))   # user adds B before the next 0.1s tick
    p.run_iterations(1)
    assert ids(var.playlist) == [] and p.launched == []   # B deleted unplayed


# ------------- F11 disconnect while buffer > 0.5s: loop never re-checks is_alive
def test_F11_disconnect_wedges_buffer_wait_loop():
    var.playlist = fill(mp.RepeatPlaylist(), [W('A')], 0)
    p = Player()

    class StuckBuffer(FakeSendAudio):
        def get_buffer_size(self):
            return 0.51              # pymumble thread is dead: never drains
    p.mumble.send_audio = StuckBuffer()
    p.mumble.is_alive = lambda: False
    p.thread = FakeProc(rc=0)
    t = threading.Thread(target=p._loop_iteration, daemon=True)
    t.start()
    t.join(0.5)
    assert t.is_alive()              # only the watchdog (os._exit) gets us out
    p.exit = True
    t.join(1)


# ------------- F1b orphan wrapper as next item kills the song that just started
def test_F1b_orphan_next_item_aborts_fresh_launch():
    var.playlist = fill(mp.RepeatPlaylist(), [W('A'), W('B'), W('X')], 0)
    var.cache.pop('B')               # B's cache entry freed (e.g. after F1)
    p = Player()
    p.async_download_next = PlayerMixin.async_download_next.__get__(p)
    p.wait_for_ready = True
    errors = p.run_iterations(1)     # launch A, then async_download_next() -> B.is_ready()
    assert p.launched == ['A'] and isinstance(errors[0], ItemNotCachedError)
    assert p.wait_for_ready is False  # loop()'s handler -> next iteration skips A
