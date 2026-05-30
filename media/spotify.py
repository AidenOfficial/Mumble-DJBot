"""Spotify playback support via the spotdl command-line tool.

This module never plays audio itself: it drives spotdl to resolve Spotify
links and download matching audio, then hands local file paths back to the
bot, which enqueues them as ordinary file items.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import util
import variables as var

log = logging.getLogger("bot")

# Matches https://open.spotify.com/<type>/... with an optional localized
# path prefix such as /intl-de/.
_SPOTIFY_URL_RE = re.compile(
    r'^https?://open\.spotify\.com/(?:[a-zA-Z-]+/)?'
    r'(track|album|playlist|artist)/',
    re.IGNORECASE
)

_AUDIO_EXTENSIONS = ('.opus', '.mp3', '.m4a', '.flac', '.ogg', '.wav')


class SpotifyError(Exception):
    """Raised on an expected failure while fetching tracks from Spotify."""
    pass


def is_spotify_url(url):
    """True for any open.spotify.com track/album/playlist/artist link."""
    return bool(_SPOTIFY_URL_RE.match(url.strip()))


def is_spotify_collection(url):
    """True for a multi-track link (album / playlist / artist), as opposed
    to a single track link."""
    match = _SPOTIFY_URL_RE.match(url.strip())
    return bool(match) and match.group(1).lower() in ('album', 'playlist', 'artist')


def _spotdl_command():
    """Base command used to invoke spotdl."""
    spotdl_path = var.config.get('spotify', 'spotdl_path', fallback='').strip()
    if spotdl_path:
        return [spotdl_path]
    return [sys.executable, '-m', 'spotdl']


def _spotdl_config_candidates():
    home = os.path.expanduser('~')
    return [
        os.path.join(home, '.spotdl', 'config.json'),
        os.path.join(home, '.config', 'spotdl', 'config.json'),
    ]


def _prepare_spotdl_credentials(client_id, client_secret):
    """Store the Spotify credentials in spotdl's own config file so they do
    not have to be passed on the command line (where `ps` / /proc would
    expose the secret).

    Returns True when the config file is ready and the caller may omit the
    --client-id / --client-secret flags, or False to fall back to passing
    the credentials on the command line.
    """
    path = next((p for p in _spotdl_config_candidates() if os.path.isfile(p)), None)

    if path is None:
        # No config yet: let spotdl write its own complete default config.
        try:
            subprocess.run(_spotdl_command() + ['--generate-config'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("spotify: 'spotdl --generate-config' failed (%s)" % e)
        path = next((p for p in _spotdl_config_candidates() if os.path.isfile(p)), None)

    if path is None:
        return False

    try:
        with open(path, 'r', encoding='utf-8') as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            return False

        if (data.get('client_id') == client_id
                and data.get('client_secret') == client_secret
                and data.get('load_config') is True):
            return True

        data['client_id'] = client_id
        data['client_secret'] = client_secret
        data['load_config'] = True
        with open(path, 'w', encoding='utf-8') as fp:
            json.dump(data, fp, indent=4)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

        # spotipy caches an OAuth token next to the config; drop it so the
        # (possibly changed) credentials take effect on the next call.
        token_cache = os.path.join(os.path.dirname(path), '.spotipy')
        if os.path.isfile(token_cache):
            try:
                os.remove(token_cache)
            except OSError:
                pass
        return True
    except (OSError, ValueError) as e:
        log.warning("spotify: could not update spotdl config (%s); "
                    "falling back to command-line credentials" % e)
        return False


def _prune_cache(folder, max_size_mb):
    """Trim the (flat) Spotify cache folder to max_size_mb by deleting the
    oldest files first. Files still referenced by the playlist are kept.
    Also sweeps stale request subfolders left behind by crashed downloads.
    """
    # Remove stale per-request subfolders (normally cleaned up in a finally
    # block, but a crash could leave one behind).
    try:
        now = time.time()
        for name in os.listdir(folder):
            if name.startswith('req_'):
                sub = os.path.join(folder, name)
                if os.path.isdir(sub) and now - os.path.getmtime(sub) > 3600:
                    shutil.rmtree(sub, ignore_errors=True)
    except OSError:
        pass

    if max_size_mb <= 0:
        return

    # Never delete a file that the playlist still points at.
    protected = set()
    try:
        for wrapper in list(var.playlist):
            try:
                uri = wrapper.uri()
                if uri:
                    protected.add(os.path.abspath(uri))
            except Exception:
                continue
    except Exception:
        pass

    files = []
    total = 0
    try:
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            try:
                size = os.path.getsize(path)
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            files.append((path, size, mtime))
            total += size
    except OSError:
        return

    limit = max_size_mb * 1024 * 1024
    if total <= limit:
        return

    files.sort(key=lambda entry: entry[2])  # oldest first
    for path, size, _ in files:
        if total <= limit:
            break
        if os.path.abspath(path) in protected:
            continue
        try:
            os.remove(path)
            total -= size
            log.debug("spotify: pruned cache file %s" % path)
        except OSError:
            continue


def _run_spotdl(command, timeout, action):
    """Run a spotdl subprocess and return the CompletedProcess. Raises
    SpotifyError on a timeout or a missing executable."""
    try:
        return subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SpotifyError("spotdl %s timed out after %d seconds" % (action, timeout))
    except FileNotFoundError:
        raise SpotifyError("spotdl executable not found")


def list_spotify_tracks(url):
    """Resolve a Spotify playlist/album/artist URL to its track list WITHOUT
    downloading any audio, using `spotdl save`.

    Returns a list of dicts, each with 'url' (the track's own Spotify URL),
    'name' and 'artist'. Raises SpotifyError on failure; returns an empty
    list when spotdl ran cleanly but found nothing.
    """
    client_id = var.config.get('spotify', 'client_id', fallback='').strip()
    client_secret = var.config.get('spotify', 'client_secret', fallback='').strip()
    timeout = var.config.getint('spotify', 'timeout', fallback=600)

    if not client_id or not client_secret:
        raise SpotifyError("spotify client_id/client_secret not configured")

    tmp_dir = tempfile.mkdtemp(prefix='spotdl_save_')
    save_file = os.path.join(tmp_dir, 'tracklist.spotdl')
    try:
        command = _spotdl_command() + ['save', '--save-file', save_file]
        if not _prepare_spotdl_credentials(client_id, client_secret):
            command += ['--client-id', client_id, '--client-secret', client_secret]
        # "--" keeps argparse from treating the URL as an option.
        command += ['--', url]

        log.info("spotify: resolving track list for %s" % url)
        result = _run_spotdl(command, timeout, 'save')
        output = result.stdout.decode('utf-8', errors='replace') if result.stdout else ''

        if not os.path.isfile(save_file) or os.path.getsize(save_file) == 0:
            if result.returncode != 0:
                raise SpotifyError("spotdl save failed with exit code %d"
                                   % result.returncode)
            log.warning("spotify: spotdl save produced no track list for %s. "
                        "Output below:\n%s" % (url, output))
            return []

        try:
            with open(save_file, 'r', encoding='utf-8') as fp:
                data = json.load(fp)
        except ValueError as e:
            raise SpotifyError("could not parse spotdl save file: %s" % e)

        if not isinstance(data, list):
            raise SpotifyError("unexpected spotdl save file format")

        tracks = []
        for song in data:
            if not isinstance(song, dict):
                continue
            track_url = song.get('url')
            if not track_url:
                continue
            tracks.append({
                'url': track_url,
                'name': song.get('name', '') or '',
                'artist': song.get('artist', '') or '',
            })
        log.info("spotify: %s resolved to %d track(s)" % (url, len(tracks)))
        return tracks
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def download_tracks(queries):
    """Download one or more Spotify queries with spotdl. Each query may be a
    Spotify track URL or free-text search keywords.

    Returns a list of absolute paths to the downloaded audio files. Raises
    SpotifyError on a hard failure (spotdl missing, timeout, or a non-zero
    exit that produced nothing); returns an empty list when spotdl ran
    cleanly but matched no track.
    """
    queries = [q for q in queries if q]
    if not queries:
        return []

    client_id = var.config.get('spotify', 'client_id', fallback='').strip()
    client_secret = var.config.get('spotify', 'client_secret', fallback='').strip()
    download_folder = var.config.get('spotify', 'download_folder', fallback='spotdl_cache/')
    audio_format = var.config.get('spotify', 'format', fallback='opus')
    bitrate = var.config.get('spotify', 'bitrate', fallback='128k')
    timeout = var.config.getint('spotify', 'timeout', fallback=600)
    max_cache_size = var.config.getint('spotify', 'max_cache_size', fallback=2048)

    if not client_id or not client_secret:
        raise SpotifyError("spotify client_id/client_secret not configured")

    download_folder = util.solve_filepath(download_folder)
    os.makedirs(download_folder, exist_ok=True)
    _prune_cache(download_folder, max_cache_size)

    # Download into a private subfolder so concurrent requests cannot clash;
    # finished files are moved out and the subfolder is removed afterwards.
    request_folder = tempfile.mkdtemp(prefix='req_', dir=download_folder)
    try:
        command = _spotdl_command() + ['download']
        output_template = os.path.join(request_folder,
                                        '{artists} - {title}.{output-ext}')
        command += ['--output', output_template, '--format', audio_format]
        if bitrate:
            command += ['--bitrate', bitrate]
        if not _prepare_spotdl_credentials(client_id, client_secret):
            command += ['--client-id', client_id, '--client-secret', client_secret]
        # "--" keeps argparse from treating a query starting with "-" as an
        # option.
        command += ['--'] + list(queries)

        log.info("spotify: downloading %d query/queries with spotdl" % len(queries))
        result = _run_spotdl(command, timeout, 'download')
        output = result.stdout.decode('utf-8', errors='replace') if result.stdout else ''
        log.debug("spotify: spotdl output:\n%s" % output)

        produced = sorted(
            name for name in os.listdir(request_folder)
            if os.path.isfile(os.path.join(request_folder, name))
            and name.lower().endswith(_AUDIO_EXTENSIONS)
        )

        # Move finished files out of the per-request subfolder into the flat
        # cache folder, so the cache stays flat (which keeps cleanup correct).
        final_paths = []
        for name in produced:
            src = os.path.join(request_folder, name)
            dst = os.path.join(download_folder, name)
            try:
                os.replace(src, dst)
                final_paths.append(os.path.abspath(dst))
            except OSError as e:
                log.warning("spotify: could not move downloaded file %s (%s)"
                            % (name, e))

        if not final_paths:
            if result.returncode != 0:
                raise SpotifyError("spotdl failed with exit code %d"
                                   % result.returncode)
            log.warning("spotify: spotdl matched no track. Output below:\n%s"
                        % output)
            return []

        if result.returncode != 0:
            log.warning("spotify: spotdl exited %d but produced %d file(s); "
                        "continuing" % (result.returncode, len(final_paths)))
        log.info("spotify: downloaded %d file(s)" % len(final_paths))
        return final_paths
    finally:
        shutil.rmtree(request_folder, ignore_errors=True)
