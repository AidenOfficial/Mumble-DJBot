#!/usr/bin/python3
# coding=utf-8

import hashlib
import html
import magic
import os
import io
import stat
import sys
import variables as var
import zipfile
import re
from urllib.parse import urlparse, urljoin
import ipaddress
import socket
import subprocess as sp
import logging
from importlib import reload
from sys import platform
import traceback
import requests
from packaging import version

import yt_dlp as youtube_dl
YT_PKG_NAME = 'yt-dlp'

log = logging.getLogger("bot")


def solve_filepath(path):
    if not path:
        return ''

    if os.path.isabs(path):
        return path
    elif os.path.exists(path):
        return path
    else:
        mydir = os.path.dirname(os.path.realpath(__file__))
        return mydir + '/' + path


def get_recursive_file_list_sorted(path):
    filelist = []
    for root, dirs, files in os.walk(path, topdown=True, onerror=None, followlinks=True):
        relroot = root.replace(path, '', 1)
        if relroot != '' and relroot in var.config.get('bot', 'ignored_folders'):
            continue
        for file in files:
            if file in var.config.get('bot', 'ignored_files'):
                continue

            fullpath = os.path.join(path, relroot, file)
            if not os.access(fullpath, os.R_OK):
                continue

            try:
                mime = magic.from_file(fullpath, mime=True)
                if 'audio' in mime or 'audio' in magic.from_file(fullpath).lower() or 'video' in mime:
                    filelist.append(os.path.join(relroot, file))
            except:
                pass

    filelist.sort()
    return filelist


# - zips files
# - returns the absolute path of the created zip file
# - zip file will be in the applications tmp folder (according to configuration)
# - format of the filename itself = prefix_hash.zip
#       - prefix can be controlled by the caller
#       - hash is a sha1 of the string representation of the directories' contents (which are
#           zipped)
def zipdir(files, zipname_prefix=None):
    zipname = var.tmp_folder
    if zipname_prefix and '../' not in zipname_prefix:
        zipname += zipname_prefix.strip().replace('/', '_') + '_'

    _hash = hashlib.sha1(str(files).encode()).hexdigest()
    zipname += _hash + '.zip'

    if os.path.exists(zipname):
        return zipname

    zipf = zipfile.ZipFile(zipname, 'w', zipfile.ZIP_DEFLATED)

    for file_to_add in files:
        if not os.access(file_to_add, os.R_OK):
            continue
        if file_to_add in var.config.get('bot', 'ignored_files'):
            continue

        add_file_as = os.path.basename(file_to_add)
        zipf.write(file_to_add, add_file_as)

    zipf.close()
    return zipname


def get_user_ban():
    res = "List of ban hash"
    for i in var.db.items("user_ban"):
        res += "<br/>" + i[0]
    return res


def new_release_version(target):
    if target == "testing":
        r = requests.get("https://packages.azlux.fr/botamusique/testing-version")
    else:
        r = requests.get("https://packages.azlux.fr/botamusique/version")
    v = r.text
    return v.rstrip()


def fetch_changelog():
    r = requests.get("https://packages.azlux.fr/botamusique/changelog")
    c = r.text
    return c


def check_update(current_version):
    global log
    log.debug("update: checking for updates...")
    new_version = new_release_version(var.config.get('bot', 'target_version'))
    try:
        update_available = version.parse(new_version) > version.parse(current_version)
    except version.InvalidVersion:
        log.debug("update: version string not comparable, skipping update check.")
        return None, None
    if update_available:
        changelog = fetch_changelog()
        log.info(f"update: new version {new_version} found, current installed version {current_version}.")
        log.info(f"update: changelog: {changelog}")
        changelog = changelog.replace("\n", "<br>")
        return new_version, changelog
    else:
        log.debug("update: no new version found.")
        return None, None


def update(current_version):
    global log

    target = var.config.get('bot', 'target_version')
    new_version = new_release_version(target)
    msg = ""
    if target == "git":
        msg = "git install, I do nothing<br/>"

    elif (target == "stable" and version.parse(new_version) > version.parse(current_version)) or \
            (target == "testing" and version.parse(new_version) != version.parse(current_version)):
        log.info('update: new version, start updating...')
        tp = sp.check_output(['/usr/bin/env', 'bash', 'update.sh', target]).decode()
        log.debug(tp)
        log.info('update: update pip libraries dependencies')
        sp.check_output([var.config.get('bot', 'pip3_path'), 'install', '--upgrade', '-r', 'requirements.txt']).decode()
        msg = "New version installed, please restart the bot.<br/>"

    log.info(f'update: starting update {YT_PKG_NAME} via pip3')
    tp = sp.check_output([var.config.get('bot', 'pip3_path'), 'install', '--upgrade', YT_PKG_NAME]).decode()
    if f"Collecting {YT_PKG_NAME}" in tp.splitlines():
        msg += "Update done: " + tp.split('Successfully installed')[1]
    else:
        msg += YT_PKG_NAME.capitalize() + " is up-to-date"

    reload(youtube_dl)
    msg += "<br/>" + YT_PKG_NAME.capitalize() + " reloaded"
    return msg


def pipe_no_wait():
    """ Generate a non-block pipe used to fetch the STDERR of ffmpeg.
    """

    if platform == "linux" or platform == "linux2" or platform == "darwin" or platform.startswith("openbsd") or platform.startswith("freebsd"):
        import fcntl
        import os

        pipe_rd = 0
        pipe_wd = 0

        if hasattr(os, "pipe2"):
            pipe_rd, pipe_wd = os.pipe2(os.O_NONBLOCK)
        else:
            pipe_rd, pipe_wd = os.pipe()

            try:
                fl = fcntl.fcntl(pipe_rd, fcntl.F_GETFL)
                fcntl.fcntl(pipe_rd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
            except:
                print(sys.exc_info()[1])
                return None, None
        return pipe_rd, pipe_wd

    elif platform == "win32":
        # https://stackoverflow.com/questions/34504970/non-blocking-read-on-os-pipe-on-windows
        import msvcrt
        import os

        from ctypes import windll, byref, wintypes, WinError, POINTER
        from ctypes.wintypes import HANDLE, DWORD, BOOL

        pipe_rd, pipe_wd = os.pipe()

        LPDWORD = POINTER(DWORD)
        PIPE_NOWAIT = wintypes.DWORD(0x00000001)
        ERROR_NO_DATA = 232

        SetNamedPipeHandleState = windll.kernel32.SetNamedPipeHandleState
        SetNamedPipeHandleState.argtypes = [HANDLE, LPDWORD, LPDWORD, LPDWORD]
        SetNamedPipeHandleState.restype = BOOL

        h = msvcrt.get_osfhandle(pipe_rd)

        res = windll.kernel32.SetNamedPipeHandleState(h, byref(PIPE_NOWAIT), None, None)
        if res == 0:
            print(WinError())
            return None, None
        return pipe_rd, pipe_wd


class Dir(object):
    def __init__(self, path):
        self.name = os.path.basename(path.strip('/'))
        self.fullpath = path
        self.subdirs = {}
        self.files = []

    def add_file(self, file):
        if file.startswith(self.name + '/'):
            file = file.replace(self.name + '/', '', 1)

        if '/' in file:
            # This file is in a subdir
            subdir = file.split('/')[0]
            if subdir in self.subdirs:
                self.subdirs[subdir].add_file(file)
            else:
                self.subdirs[subdir] = Dir(os.path.join(self.fullpath, subdir))
                self.subdirs[subdir].add_file(file)
        else:
            self.files.append(file)
        return True

    def get_subdirs(self, path=None):
        subdirs = []
        if path and path != '' and path != './':
            subdir = path.split('/')[0]
            if subdir in self.subdirs:
                searchpath = '/'.join(path.split('/')[1::])
                subdirs = self.subdirs[subdir].get_subdirs(searchpath)
                subdirs = list(map(lambda subsubdir: os.path.join(subdir, subsubdir), subdirs))
        else:
            subdirs = self.subdirs

        return subdirs

    def get_subdirs_recursively(self, path=None):
        subdirs = []
        if path and path != '' and path != './':
            subdir = path.split('/')[0]
            if subdir in self.subdirs:
                searchpath = '/'.join(path.split('/')[1::])
                subdirs = self.subdirs[subdir].get_subdirs_recursively(searchpath)
        else:
            subdirs = list(self.subdirs.keys())

            for key, val in self.subdirs.items():
                subdirs.extend(map(lambda subdir: key + '/' + subdir, val.get_subdirs_recursively()))

        subdirs.sort()
        return subdirs

    def get_files(self, path=None):
        files = []
        if path and path != '' and path != './':
            subdir = path.split('/')[0]
            if subdir in self.subdirs:
                searchpath = '/'.join(path.split('/')[1::])
                files = self.subdirs[subdir].get_files(searchpath)
        else:
            files = self.files

        return files

    def get_files_recursively(self, path=None):
        files = []
        if path and path != '' and path != './':
            subdir = path.split('/')[0]
            if subdir in self.subdirs:
                searchpath = '/'.join(path.split('/')[1::])
                files = self.subdirs[subdir].get_files_recursively(searchpath)
        else:
            files = self.files

            for key, val in self.subdirs.items():
                files.extend(map(lambda file: key + '/' + file, val.get_files_recursively()))

        return files

    def render_text(self, ident=0):
        print('{}{}/'.format(' ' * (ident * 4), self.name))
        for key, val in self.subdirs.items():
            val.render_text(ident + 1)
        for file in self.files:
            print('{}{}'.format(' ' * (ident + 1) * 4, file))


# Parse the html from the message to get the URL

def get_url_from_input(string):
    string = string.strip()[:4096]
    if not (string.startswith("http") or string.startswith("HTTP")):
        res = re.search('href="(.+?)"', string, flags=re.IGNORECASE)
        if res:
            string = res.group(1)
        else:
            return ""

    match = re.search(r"(http|https)://([^/]*)/(\S*)", string, flags=re.IGNORECASE)
    if match:
        url = match[1].lower() + "://" + match[2].lower() + "/" + match[3]
        # https://github.com/mumble-voip/mumble/issues/4999
        return html.unescape(url)
    else:
        return ""


def is_public_url(url):
    # Reject URLs whose host resolves to a private / loopback / link-local /
    # reserved address, so untrusted chat input can't make the bot probe the
    # local network (SSRF). Only http(s) URLs are accepted.
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https"):
        return False
    host = parts.hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def bv_to_av(bvid):
    # Convert a Bilibili BV id (e.g. "BV1iCHXzJEdk") into its numeric av id.
    xor_code = 23442827791579
    mask_code = 2251799813685247
    alphabet = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
    chars = list(bvid)
    chars[3], chars[9] = chars[9], chars[3]
    chars[4], chars[7] = chars[7], chars[4]
    tmp = 0
    for c in chars[3:]:
        tmp = tmp * 58 + alphabet.index(c)
    return (tmp & mask_code) ^ xor_code


def _is_bilibili_host(host):
    host = (host or "").lower()
    return (host == "bilibili.com" or host.endswith(".bilibili.com")
            or host in ("b23.tv", "www.b23.tv"))


def _resolve_bilibili_redirect(url, max_hops=5):
    # Follow HTTP redirects one hop at a time, refusing to leave Bilibili.
    # requests' allow_redirects=True would fetch every hop server-side before we
    # could check it, letting a crafted b23.tv link reach internal hosts (SSRF).
    for _ in range(max_hops):
        try:
            resp = requests.head(url, allow_redirects=False, timeout=10)
        except requests.exceptions.RequestException:
            return None
        location = resp.headers.get("Location")
        if not location:
            return url
        location = urljoin(url, location)
        if not _is_bilibili_host(urlparse(location).hostname):
            return None
        url = location
    return url


def get_bilibili_url_from_input(string):
    # Normalize user input into a Bilibili video URL for yt-dlp. Bilibili's
    # anti-crawl currently answers BV-form video pages with HTTP 412, while the
    # equivalent av-form URL works - so BV ids are converted to av ids here.
    # Accepts full bilibili.com / b23.tv links and bare ids, plus an optional
    # trailing "pN" to select a part of a multi-part video.
    string = string.strip()[:2048]

    page = None
    # Single \s (not \s+) keeps this linear - a long run of spaces from chat
    # would otherwise cause catastrophic regex backtracking (ReDoS).
    page_match = re.search(r'\sp(\d+)\s*$', string, flags=re.IGNORECASE)
    if page_match:
        page = page_match.group(1)
        string = string[:page_match.start()].strip()

    parsed = get_url_from_input(string)

    # Resolve b23.tv short links to the real bilibili.com URL (SSRF-safe:
    # redirects are followed one hop at a time and must stay on Bilibili).
    if parsed and (urlparse(parsed).hostname or "").lower() in ("b23.tv", "www.b23.tv"):
        parsed = _resolve_bilibili_redirect(parsed) or parsed

    # Only accept Bilibili links (or a bare id when no URL was given) so
    # look-alikes like "evilbilibili.com" are rejected.
    if parsed and not _is_bilibili_host(urlparse(parsed).hostname):
        return ""

    haystack = parsed if parsed else string

    # A multi-part video may carry "?p=N" in the URL - keep that part number.
    if page is None:
        p_in_url = re.search(r'[?&]p=(\d+)', haystack)
        if p_in_url:
            page = p_in_url.group(1)

    url = ""
    bv_match = re.search(r'BV[0-9A-Za-z]{10}', haystack)
    if bv_match:
        try:
            url = "https://www.bilibili.com/video/av%d" % bv_to_av(bv_match.group(0))
        except (ValueError, IndexError):
            url = "https://www.bilibili.com/video/" + bv_match.group(0)
    else:
        av_match = re.search(r'av\d+', haystack, flags=re.IGNORECASE)
        if av_match:
            url = "https://www.bilibili.com/video/" + av_match.group(0).lower()
        elif parsed:
            # e.g. a bangumi link - pass it through unchanged.
            url = parsed

    if not url:
        return ""

    if page and "p=" not in url:
        url += ("&" if "?" in url else "?") + "p=" + page

    return url


def youtube_search(query):
    global log
    import json

    try:
        cookie_file =  var.config.get('youtube_dl', 'cookie_file')
        cookie = parse_cookie_file(cookie_file) if cookie_file else {}
        r = requests.get("https://www.youtube.com/results", cookies=cookie,
                         params={'search_query': query}, timeout=5)
        result_json_match = re.findall(r">var ytInitialData = (.*?);</script>", r.text)

        if not len(result_json_match):
            log.error("util: can not interpret youtube search web page")
            return False

        result_big_json = json.loads(result_json_match[0])
        results = []
        try:
            for item in result_big_json['contents']['twoColumnSearchResultsRenderer']\
                    ['primaryContents']['sectionListRenderer']['contents'][0]\
                    ['itemSectionRenderer']['contents']:
                if 'videoRenderer' not in item:
                    continue
                video_info = item['videoRenderer']
                title = video_info['title']['runs'][0]['text']
                video_id = video_info['videoId']
                uploader = video_info['ownerText']['runs'][0]['text']
                results.append([video_id, title, uploader])
        except (json.JSONDecodeError, KeyError):
            log.error("util: can not interpret youtube search web page")
            return False

        return results

    except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError, requests.exceptions.Timeout):
        error_traceback = traceback.format_exc().split("During")[0]
        log.error("util: youtube query failed with error:\n %s" % error_traceback)
        return False


def get_media_duration(path):
    command = ("ffprobe", "-v", "quiet", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", path)
    process = sp.Popen(command, stdout=sp.PIPE, stderr=sp.PIPE)
    stdout, stderr = process.communicate()

    try:
        if not stderr:
            return float(stdout)
        else:
            return 0
    except ValueError:
        return 0


def parse_time(human):
    match = re.search("(?:(\d\d):)?(?:(\d\d):)?(\d+(?:\.\d*)?)", human, flags=re.IGNORECASE)
    if match:
        if match[1] is None and match[2] is None:
            return float(match[3])
        elif match[2] is None:
            return float(match[3]) + 60 * int(match[1])
        else:
            return float(match[3]) + 60 * int(match[2]) + 3600 * int(match[1])
    else:
        raise ValueError("Invalid time string given.")


def format_time(seconds):
    seconds = int(seconds)
    hours = seconds // 3600
    seconds = seconds % 3600
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{hours:d}:{minutes:02d}:{seconds:02d}"


def parse_file_size(human):
    units = {"B": 1, "KB": 1024, "MB": 1024 * 1024, "GB": 1024 * 1024 * 1024, "TB": 1024 * 1024 * 1024 * 1024,
             "K": 1024, "M": 1024 * 1024, "G": 1024 * 1024 * 1024, "T": 1024 * 1024 * 1024 * 1024}
    match = re.search("(\d+(?:\.\d*)?)\s*([A-Za-z]+)", human, flags=re.IGNORECASE)
    if match:
        num = float(match[1])
        unit = match[2].upper()
        if unit in units:
            return int(num * units[unit])

    raise ValueError("Invalid file size given.")


def get_salted_password_hash(password):
    salt = os.urandom(10)
    hashed = hashlib.pbkdf2_hmac('sha1', password.encode("utf-8"), salt, 100000)

    return hashed.hex(), salt.hex()


def verify_password(password, salted_hash, salt):
    hashed = hashlib.pbkdf2_hmac('sha1', password.encode("utf-8"), bytearray.fromhex(salt), 100000)
    if hashed.hex() == salted_hash:
        return True
    return False


def get_supported_language():
    root_dir = os.path.dirname(__file__)
    lang_files = os.listdir(os.path.join(root_dir, 'lang'))
    lang_list = []
    for lang_file in lang_files:
        match = re.search("([a-z]{2}_[A-Z]{2})\.json", lang_file)
        if match:
            lang_list.append(match[1])

    return lang_list


def set_logging_formatter(handler: logging.Handler, logging_level):
    if logging_level == logging.DEBUG:
        formatter = logging.Formatter(
            "[%(asctime)s] > [%(threadName)s] > "
            "[%(filename)s:%(lineno)d] %(message)s"
        )
    else:
        formatter = logging.Formatter(
            '[%(asctime)s %(levelname)s] %(message)s', "%b %d %H:%M:%S")

    handler.setFormatter(formatter)


def get_snapshot_version():
    import subprocess
    wd = os.getcwd()
    root_dir = os.path.dirname(__file__)
    os.chdir(root_dir)

    ver = "unknown"
    if os.path.exists(os.path.join(root_dir, ".git")):
        try:
            ret = subprocess.check_output(["git", "describe", "--tags"]).strip()
            ver = ret.decode("utf-8")
        except (FileNotFoundError, subprocess.CalledProcessError):
            try:
                with open(os.path.join(root_dir, ".git/refs/heads/master")) as f:
                    ver = "g" + f.read()[:7]
            except FileNotFoundError:
                pass

    os.chdir(wd)
    return ver


class LoggerIOWrapper(io.TextIOWrapper):
    def __init__(self, logger: logging.Logger, logging_level, fallback_io_buffer):
        super().__init__(fallback_io_buffer, write_through=True)
        self.logger = logger
        self.logging_level = logging_level

    def write(self, text):
        if isinstance(text, bytes):
            msg = text.decode('utf-8').rstrip()
            self.logger.log(self.logging_level, msg)
            super().write(msg + "\n")
        else:
            self.logger.log(self.logging_level, text.rstrip())
            super().write(text + "\n")


class VolumeHelper:
    def __init__(self, plain_volume=0, ducking_plain_volume=0):
        self.plain_volume_set = 0
        self.plain_ducking_volume_set = 0
        self.volume_set = 0
        self.ducking_volume_set = 0

        self.real_volume = 0

        self.set_volume(plain_volume)
        self.set_ducking_volume(ducking_plain_volume)

    def set_volume(self, plain_volume):
        self.volume_set = self._convert_volume(plain_volume)
        self.plain_volume_set = plain_volume

    def set_ducking_volume(self, plain_volume):
        self.ducking_volume_set = self._convert_volume(plain_volume)
        self.plain_ducking_volume_set = plain_volume

    def _convert_volume(self, volume):
        if volume == 0:
            return 0

        # convert input of 0~1 into -35~5 dB
        dB = -35 + volume * 40

        # Some dirty trick to stretch the function, to make to be 0 when input is -35 dB
        return (10 ** (dB / 20) - 10 ** (-35 / 20)) / (1 - 10 ** (-35 / 20))


def get_size_folder(path):
    global log

    folder_size = 0
    for (path, dirs, files) in os.walk(path):
        for file in files:
            filename = os.path.join(path, file)
            try:
                folder_size += os.path.getsize(filename)
            except (FileNotFoundError, OSError):
                continue
    return int(folder_size / (1024 * 1024))


def clear_tmp_folder(path, size):
    global log

    if size == -1:
        return
    elif size == 0:
        for (path, dirs, files) in os.walk(path):
            for file in files:
                filename = os.path.join(path, file)
                try:
                    os.remove(filename)
                except (FileNotFoundError, OSError):
                    continue
    else:
        if get_size_folder(path=path) > size:
            # Snapshot (mtime, size, path) of the top-level download cache in
            # one pass. Concurrent threads (parallel downloads, yt-dlp temp
            # files, the cache cleaner) may delete files while we scan - a
            # vanished file must be skipped, never crash the download thread.
            # Subdirectories (e.g. spotify req_* caches) have their own
            # pruning and are left alone.
            entries = []
            try:
                names = os.listdir(path)
            except OSError:
                return
            for name in names:
                filename = os.path.join(path, name)
                try:
                    st = os.stat(filename)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    entries.append((st.st_mtime, st.st_size, filename))
            entries.sort()
            size_tp = 0
            for idx, (_mtime, fsize, _fname) in enumerate(entries):
                size_tp += fsize
                if int(size_tp / (1024 * 1024)) > size:
                    log.info("Cleaning tmp folder")
                    for (_m, _s, f) in entries[:idx]:
                        log.debug("Removing " + f)
                        try:
                            os.remove(f)
                        except OSError:
                            continue
                    return


def check_extra_config(config, template):
    extra = []

    for key in config.sections():
        if key in ['radio']:
            continue
        for opt in config.options(key):
            if not template.has_option(key, opt):
                extra.append((key, opt))

    return extra


def parse_cookie_file(cookiefile):
    # https://stackoverflow.com/a/54659484/1584825

    cookies = {}
    with open (cookiefile, 'r') as fp:
        for line in fp:
            if not re.match(r'^#', line):
                lineFields = line.strip().split('\t')
                cookies[lineFields[5]] = lineFields[6]
    return cookies
