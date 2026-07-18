"""Periodic cleanup of downloaded audio caches.

Downloaded audio piles up in two places: `tmp_folder` holds URL/Bilibili
downloads (files named by their 32-hex md5 item id) and the Spotify
`download_folder` holds spotdl output. `tmp_folder_max_size` already trims
by size at download time; this module adds a time-based sweep that runs
every `cleanup_interval_days` days, so a rarely-used bot does not keep
months-old cache around forever.

The last run timestamp is persisted in the settings database (section
`cleanup`, option `last_run`), so restarts neither reset nor pile up the
schedule: if the interval already elapsed while the bot was down, the sweep
runs right after startup.

Safety rules, in order:
  - `music_folder` (the user's own library) is never touched.
  - Only files that are provably download cache are candidates: 32-hex
    basenames in tmp_folder, url-type db records pointing into tmp_folder,
    and plain files in the dedicated Spotify cache folder.
  - Files referenced by the current playlist/queue, items flagged as
    downloading, partial-download artifacts (.part/.ytdl/...) and files
    younger than `cleanup_keep_days` days are kept.
  - Database records are kept in sync: url records get their `ready` flag
    reset (so the next request re-downloads instead of erroring), spotify
    file cache records are dropped entirely.
"""

import logging
import os
import re
import threading
import time

from database import Condition
import util
import variables as var

log = logging.getLogger("bot")

DAY_SECONDS = 24 * 3600
# Never delete anything modified this recently, whatever keep_days says:
# a file being written right now must survive the sweep.
MIN_AGE_SECONDS = 3600
# In-progress download artifacts (yt-dlp and friends).
PARTIAL_SUFFIXES = ('.part', '.ytdl', '.tmp', '.temp', '.frag', '.download',
                    '.incomplete')

_HEX_ID_RE = re.compile(r'^[0-9a-f]{32}$')


class CacheCleaner:
    """Time-based cache sweeper running in a daemon thread.

    `clock` is injectable for tests and must behave like time.time.
    """

    def __init__(self, clock=time.time):
        self.clock = clock
        self._wake = threading.Event()
        self._thread = None

    # ---- configuration / persistence -------------------------------------

    @property
    def interval_days(self):
        return var.config.getint('bot', 'cleanup_interval_days', fallback=7)

    @property
    def keep_days(self):
        return var.config.getint('bot', 'cleanup_keep_days', fallback=7)

    def last_run(self):
        try:
            return var.db.getfloat('cleanup', 'last_run', fallback=0.0)
        except (ValueError, TypeError):
            return 0.0

    def _record_run(self, timestamp):
        var.db.set('cleanup', 'last_run', str(timestamp))

    def seconds_until_due(self, now=None):
        """<= 0 means a sweep is due right now."""
        if now is None:
            now = self.clock()
        last = self.last_run()
        if last <= 0 or last > now:  # never ran, or clock went backwards
            return 0
        return (last + self.interval_days * DAY_SECONDS) - now

    # ---- thread lifecycle -------------------------------------------------

    def start(self):
        if self.interval_days <= 0:
            log.info("cleanup: periodic cache cleanup disabled "
                     "(cleanup_interval_days = 0)")
            return
        self._thread = threading.Thread(
            target=self._loop, name="CacheCleanupThread", daemon=True)
        self._thread.start()

    def stop(self):
        self._wake.set()

    def _loop(self):
        while not self._wake.is_set():
            due_in = self.seconds_until_due()
            if due_in <= 0:
                try:
                    self.run_once()
                except Exception:
                    log.exception("cleanup: cache sweep failed")
                    # count the failed attempt as a run so a persistent
                    # error does not turn into a busy loop
                    self._record_run(self.clock())
                due_in = self.interval_days * DAY_SECONDS
            # wake up at least hourly so config/clock changes are noticed
            self._wake.wait(min(due_in, 3600))

    # ---- the sweep itself -------------------------------------------------

    def protected_paths(self):
        """Absolute paths that must never be deleted: everything the
        playlist references plus items currently downloading."""
        protected = set()
        playlist = getattr(var, 'playlist', None)
        if playlist is not None:
            for wrapper in list(playlist):
                try:
                    uri = wrapper.uri()
                    if uri:
                        protected.add(os.path.abspath(uri))
                except Exception:
                    continue
        cache = getattr(var, 'cache', None)
        if cache is not None:
            for item in list(cache.values()):
                if getattr(item, 'downloading', False):
                    path = getattr(item, 'path', None)
                    if path:
                        protected.add(os.path.abspath(path))
        return protected

    def _is_expired(self, path, now):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return False
        if now - mtime < MIN_AGE_SECONDS:
            return False
        return now - mtime >= self.keep_days * DAY_SECONDS

    def _spotify_folder(self):
        folder = var.config.get('spotify', 'download_folder',
                                fallback='spotdl_cache/')
        if not folder:
            return None
        return os.path.abspath(util.solve_filepath(folder))

    def run_once(self):
        """One sweep over both cache folders. Returns list of removed
        absolute paths (mostly useful for tests/logging)."""
        now = self.clock()
        removed = []
        protected = self.protected_paths()
        music_folder = os.path.abspath(getattr(var, 'music_folder', '') or '/nonexistent')

        removed += self._sweep_tmp_folder(now, protected, music_folder)
        removed += self._sweep_spotify_folder(now, protected, music_folder)

        self._invalidate_db_records(removed)
        self._record_run(now)
        if removed:
            log.info("cleanup: removed %d cached download(s)" % len(removed))
        else:
            log.debug("cleanup: nothing to remove")
        return removed

    def _sweep_tmp_folder(self, now, protected, music_folder):
        """tmp_folder may be a shared directory (/tmp/ by default), so only
        files that are provably ours are candidates: 32-hex md5 basenames,
        or paths recorded in the music database for url items."""
        folder = os.path.abspath(getattr(var, 'tmp_folder', '') or '')
        if not folder or not os.path.isdir(folder):
            return []
        if folder == music_folder or folder.startswith(music_folder + os.sep):
            return []

        known_paths = set()
        try:
            for record in var.music_db.query_music(
                    Condition().and_equal('type', 'url')):
                path = record.get('path')
                if path:
                    known_paths.add(os.path.abspath(path))
        except Exception:
            pass

        removed = []
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        for name in names:
            path = os.path.join(folder, name)
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            if name.lower().endswith(PARTIAL_SUFFIXES):
                continue
            base = name.split('.', 1)[0]
            if not (_HEX_ID_RE.match(base) or os.path.abspath(path) in known_paths):
                continue
            if os.path.abspath(path) in protected:
                continue
            if not self._is_expired(path, now):
                continue
            if self._remove(path):
                removed.append(os.path.abspath(path))
        return removed

    def _sweep_spotify_folder(self, now, protected, music_folder):
        folder = self._spotify_folder()
        if not folder or not os.path.isdir(folder):
            return []
        if folder == music_folder or folder.startswith(music_folder + os.sep) \
                or music_folder.startswith(folder + os.sep):
            # refuse to sweep anything that overlaps the user's library
            return []

        removed = []
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        for name in names:
            path = os.path.join(folder, name)
            # req_* download-in-progress subfolders are handled by
            # media.spotify._prune_cache; leave directories alone here.
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            if name.lower().endswith(PARTIAL_SUFFIXES):
                continue
            if os.path.abspath(path) in protected:
                continue
            if not self._is_expired(path, now):
                continue
            if self._remove(path):
                removed.append(os.path.abspath(path))
        return removed

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
            log.debug("cleanup: removed cached download %s" % path)
            return True
        except OSError as e:
            log.warning("cleanup: could not remove %s (%s)" % (path, e))
            return False

    def _invalidate_db_records(self, removed_paths):
        """Keep the music database consistent with the files we deleted:
        url items are reset to 'validated' so the next request re-downloads;
        spotify file-cache records are deleted outright."""
        if not removed_paths:
            return
        removed = set(removed_paths)
        cache = getattr(var, 'cache', None)
        try:
            records = var.music_db.query_music(
                Condition().or_equal('type', 'url').or_equal('type', 'file'))
        except Exception:
            log.exception("cleanup: could not query music db for invalidation")
            return
        for record in records:
            path = record.get('path')
            if not path:
                continue
            # file records with a relative path live in music_folder (see
            # FileItem.uri) and can never be in the removed set
            if record.get('type') == 'file' and not path.startswith('/'):
                continue
            if os.path.abspath(path) not in removed:
                continue
            record_id = record.get('id')
            if record.get('type') == 'url':
                record['ready'] = 'validated'
                try:
                    var.music_db.insert_music(dict(record))
                except Exception:
                    log.exception("cleanup: could not update db record %s" % record_id)
                # keep the in-memory cache consistent as well
                if cache is not None and record_id in cache:
                    item = cache[record_id]
                    if getattr(item, 'ready', None) == 'yes':
                        item.ready = 'validated'
            else:  # spotify file cache entry
                try:
                    var.music_db.delete_music(Condition().and_equal('id', record_id))
                except Exception:
                    log.exception("cleanup: could not delete db record %s" % record_id)
                if cache is not None and record_id in cache:
                    try:
                        del cache[record_id]
                    except KeyError:
                        pass
