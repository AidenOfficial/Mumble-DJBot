#!/usr/bin/python3
# coding=utf-8

import hashlib
import hmac
import html
import magic
import os
import io
import sys
import variables as var
import re
from urllib.parse import urlparse, urljoin
import ipaddress
import socket
import subprocess as sp
import logging
import requests

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


# yt-dlp / spotdl 跟着 B 站、YouTube 改版频繁更新;与 entrypoint.sh 的 BAM_UPDATE_ON_START 升级同一组包
EXTRACTOR_PACKAGES = ('yt-dlp', 'spotdl')


def upgrade_extractors():
    """用当前解释器的 pip 升级 yt-dlp / spotdl,返回给管理员看的结果文本(HTML)。

    已加载的模块不会被替换,新版本要重启 bot 才生效。
    """
    log.info(f"update: upgrading {', '.join(EXTRACTOR_PACKAGES)} via pip")
    try:
        out = sp.run([sys.executable, '-m', 'pip', 'install', '--upgrade', *EXTRACTOR_PACKAGES],
                     capture_output=True, text=True, timeout=600)
    except (OSError, sp.TimeoutExpired) as e:
        log.error(f"update: pip failed to run: {e}")
        return f"Update failed: {html.escape(str(e))}"
    if out.returncode != 0:
        log.error(f"update: pip exited with {out.returncode}:\n{out.stderr[-2000:]}")
        return f"Update failed (pip exit code {out.returncode}), see the log for details."
    log.debug(out.stdout)
    installed = [line.split('Successfully installed', 1)[1].strip()
                 for line in out.stdout.splitlines() if 'Successfully installed' in line]
    if installed:
        return f"Updated: {html.escape(installed[-1])}<br/>Restart the bot to use the new version."
    return f"{' / '.join(EXTRACTOR_PACKAGES)} already up to date."


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
    match = re.search(r"(?:(\d\d):)?(?:(\d\d):)?(\d+(?:\.\d*)?)", human, flags=re.IGNORECASE)
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
    match = re.search(r"(\d+(?:\.\d*)?)\s*([A-Za-z]+)", human, flags=re.IGNORECASE)
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
    return hmac.compare_digest(hashed.hex(), salted_hash)


def get_supported_language():
    root_dir = os.path.dirname(__file__)
    lang_files = os.listdir(os.path.join(root_dir, 'lang'))
    lang_list = []
    for lang_file in lang_files:
        match = re.search(r"([a-z]{2}_[A-Z]{2})\.json", lang_file)
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
    """git 检出时返回 tag 或短 commit 号;不是 git 检出(Docker 镜像里没有 .git)就是 unknown。"""
    root_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.exists(os.path.join(root_dir, ".git")):
        return "unknown"
    try:
        ret = sp.check_output(["git", "describe", "--tags", "--always"], cwd=root_dir, stderr=sp.DEVNULL)
        return ret.decode("utf-8").strip() or "unknown"
    except (OSError, sp.CalledProcessError):
        return "unknown"


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


# 已删除、但旧配置文件里可能还留着的配置项:忽略并提示,不当成未知配置拒绝启动
OBSOLETE_CONFIG = {
    ('bot', 'auto_check_update'),  # 上游 azlux 的版本检查
    ('bot', 'target_version'),     # 上游 azlux 的 stable/testing 更新通道
    ('bot', 'pip3_path'),          # !update 改用当前解释器的 pip
}


def check_extra_config(config, template):
    """返回 (未知配置项, 已废弃配置项) 两个 (section, option) 列表。"""
    extra = []
    obsolete = []

    for key in config.sections():
        if key in ['radio']:
            continue
        for opt in config.options(key):
            if (key, opt) in OBSOLETE_CONFIG:
                obsolete.append((key, opt))
            elif not template.has_option(key, opt):
                extra.append((key, opt))

    return extra, obsolete


def parse_cookie_file(cookiefile):
    # https://stackoverflow.com/a/54659484/1584825

    cookies = {}
    with open (cookiefile, 'r') as fp:
        for line in fp:
            if not re.match(r'^#', line):
                lineFields = line.strip().split('\t')
                cookies[lineFields[5]] = lineFields[6]
    return cookies
