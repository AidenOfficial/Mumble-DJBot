import copy
import threading
import logging
import os
import time
import hashlib
import traceback
from PIL import Image
import yt_dlp as youtube_dl
import glob
from io import BytesIO
from urllib.parse import urlparse
import base64

import util
from constants import tr_cli as tr
import media
import variables as var
from media.item import BaseItem, item_builders, item_loaders, item_id_generators, ValidationFailedError, \
    PreparationFailedError
from util import format_time

log = logging.getLogger("bot")


def url_item_builder(**kwargs):
    return URLItem(kwargs['url'])


def url_item_loader(_dict):
    return URLItem("", _dict)


def url_item_id_generator(**kwargs):
    return hashlib.md5(kwargs['url'].encode()).hexdigest()


item_builders['url'] = url_item_builder
item_loaders['url'] = url_item_loader
item_id_generators['url'] = url_item_id_generator


class URLItem(BaseItem):
    def __init__(self, url, from_dict=None):
        self.validating_lock = threading.Lock()
        if from_dict is None:
            super().__init__()
            self.url = url if url[-1] != "/" else url[:-1]
            self.title = ""
            self.duration = 0
            self.id = hashlib.md5(url.encode()).hexdigest()
            self.path = var.tmp_folder + self.id
            self.thumbnail = ""
            self.keywords = ""
        else:
            super().__init__(from_dict)
            self.url = from_dict['url']
            self.duration = from_dict['duration']
            self.path = from_dict['path']
            self.title = from_dict['title']
            self.thumbnail = from_dict['thumbnail']

        self.downloading = False
        # Fraction in [0.0, 1.0] of the current download, updated by the
        # yt-dlp progress hook so other threads can report progress.
        self.progress = 0.0
        # Set by the player when a stream-while-downloading attempt failed
        # (e.g. a container that cannot be decoded before it is complete);
        # from then on this item waits for the full download.
        self.no_stream = False
        self.type = "url"
        # 准备进度(Web 界面的"还要等多久"用):
        #   pending -> fetching_info -> starting -> downloading -> ready / failed
        self.stage = 'ready' if self.ready == 'yes' else 'pending'
        self.stage_since = time.time()
        self.speed = 0.0            # 字节/秒
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.download_eta = None    # 下载完整个文件还要几秒(yt-dlp 估算)
        # 校验时读到的 yt-dlp 信息,开始下载时直接复用,省掉第二次读取(实测 B 站约 4 秒)
        self._info = None
        self._info_at = 0.0
        self._info_cookies = []

    def uri(self):
        return self.path

    def is_ready(self):
        if self.downloading or self.ready != 'yes':
            return False
        if self.ready == 'yes' and not os.path.exists(self.path):
            self.log.info(
                "url: music file missed for %s" % self.format_debug_string())
            self.ready = 'validated'
            return False

        return True

    def validate(self):
        try:
            self.validating_lock.acquire()
            if self.ready in ['yes', 'validated']:
                return True
            if self.downloading:
                # 正在下载(!repeat、重复点同一链接、切换播放模式都会再校验一次)。
                # 下面那段会把带 .incomplete 标记的文件当成崩溃残留删掉,
                # 而那正是边下边播正在写、ffmpeg 正在读的文件。
                # (只看 downloading:库里残留的 'preparing' 是上次崩溃留下的,照常清理。)
                return True

            # if self.ready == 'failed':
            #     self.validating_lock.release()
            #     return False
            #
            if os.path.exists(self.path):
                if os.path.exists(self._incomplete_marker_path()):
                    # A stream-while-downloading (nopart) download died before
                    # completing, leaving a truncated file at the final path.
                    # Discard it and fall through to a fresh validation.
                    self._discard_incomplete_download()
                else:
                    self.ready = "yes"
                    return True

            # Check if this url is banned
            if var.db.has_option('url_ban', self.url):
                raise ValidationFailedError(tr('url_ban', url=self.url))

            # avoid multiple process validating in the meantime
            info = self._get_info_from_url()

            if not info:
                return False

            # Check if the song is too long and is not whitelisted
            max_duration = var.config.getint('bot', 'max_track_duration') * 60
            if max_duration and \
                    not var.db.has_option('url_whitelist', self.url) and \
                    self.duration > max_duration:
                log.info(
                    "url: " + self.url + " has a duration of " + str(self.duration / 60) + " min -- too long")
                raise ValidationFailedError(tr('too_long', song=self.format_title(),
                                               duration=format_time(self.duration),
                                               max_duration=format_time(max_duration)))
            else:
                self.ready = "validated"
                self.version += 1  # notify wrapper to save me
                return True
        finally:
            self.validating_lock.release()

    # Run in a other thread
    def prepare(self):
        # 以前这里是两个 assert:清理线程把 ready 从 yes 改回 validated、
        # 或上一次下载失败留下 failed 时,AssertionError 会直接打死下载线程。
        if self.is_ready():
            return True
        if self.downloading:
            return True  # 另一个线程正在下载,主循环会轮询 is_ready()
        if self.ready != 'validated':
            self.validate()
        return self._download()

    # 各阶段最近的典型耗时(指数平均),用来在拿不到下载速度时估算"还要多久"
    typical_secs = {'fetching_info': 4.5, 'starting': 1.0}

    def _set_stage(self, stage):
        if self.stage != stage:
            now = time.time()
            finished = self.stage
            if finished in self.typical_secs and stage not in ('failed',):
                spent = now - self.stage_since
                if 0 < spent < 120:
                    URLItem.typical_secs[finished] = self.typical_secs[finished] * 0.7 + spent * 0.3
            self.stage = stage
            self.stage_since = now

    def eta_estimate(self):
        """还在读信息 / 建连接时,按近期典型耗时粗估开播还要几秒。"""
        left = 0.0
        elapsed = time.time() - self.stage_since
        if self.stage in ('pending', 'fetching_info'):
            done = elapsed if self.stage == 'fetching_info' else 0
            left += max(0.5, self.typical_secs['fetching_info'] - done) + self.typical_secs['starting']
        elif self.stage == 'starting':
            left += max(0.3, self.typical_secs['starting'] - elapsed)
        else:
            return None
        return left + 0.5  # 缓冲 30 秒音频 + ffmpeg 启动,实测约 0.3-0.5 秒

    INFO_REUSE_SECONDS = 1800  # 信息里的音频直链会过期(B 站约 2 小时,YouTube 约 6 小时),留足余量

    def _get_info_from_url(self):
        self.log.info("url: fetching metadata of url %s " % self.url)
        self._set_stage('fetching_info')
        ydl_opts = {
            'noplaylist': True,
            # 和下载用同一个格式:读到的信息里选好的就是要下载的音频流,下载时可以直接复用。
            # (用默认格式会选成"视频+音频",复用时把视频流/直链带进下载,实测 B 站返回 403)
            'format': 'bestaudio/best',
        }

        cookie = var.config.get('youtube_dl', 'cookie_file')
        if cookie:
            ydl_opts['cookiefile'] = var.config.get('youtube_dl', 'cookie_file')

        user_agent = var.config.get('youtube_dl', 'user_agent')
        if user_agent:
            youtube_dl.utils.std_headers['User-Agent'] = var.config.get('youtube_dl', 'user_agent')\

        succeed = False
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            attempts = var.config.getint('bot', 'download_attempts')
            for i in range(attempts):
                try:
                    info = ydl.extract_info(self.url, download=False)
                except youtube_dl.utils.DownloadError:
                    continue
                except Exception:
                    self.log.warning("url: metadata extraction crashed for %s", self.url, exc_info=True)
                    continue
                if not info:
                    continue
                # 直播/首映/部分 B 站条目没有时长或标题,yt-dlp 给 None;
                # 以前直接拿 None 去和 max_duration 比较,TypeError 打死线程。
                try:
                    self.duration = int(info.get('duration') or 0)
                except (TypeError, ValueError):
                    self.duration = 0
                self.title = str(info.get('title') or self.url).strip()
                self.keywords = self.title
                if info.get('_type', 'video') == 'video':
                    # 直链可能绑定了读取时拿到的 cookie(B 站 buvid 等),一起留着给下载用
                    self._info, self._info_at = info, time.time()
                    self._info_cookies = list(ydl.cookiejar)
                self._set_stage('pending')
                succeed = True
                return True

        if not succeed:
            self.ready = 'failed'
            self._set_stage('failed')
            self.log.error("url: error while fetching info from the URL")
            raise ValidationFailedError(tr('unable_download', item=self.format_title()))

    def _incomplete_marker_path(self):
        return self.path + ".incomplete"

    def _discard_incomplete_download(self):
        for f in glob.glob(self.path + "*"):
            try:
                os.remove(f)
            except OSError:
                pass

    def playable_from(self, playhead, buffer_secs):
        """True if enough of this item is on disk to start (or continue)
        playback at `playhead` with `buffer_secs` seconds of safety margin.
        Used by the player for stream-while-downloading."""
        if self.ready == 'yes':
            return True
        if self.no_stream or not self.downloading or not self.duration:
            return False
        if not os.path.exists(self.path):
            return False
        downloaded_secs = (self.progress or 0.0) * self.duration
        # near the end of the file the margin cannot be satisfied anymore
        return downloaded_secs >= min(self.duration - 1, playhead + buffer_secs)

    @staticmethod
    def _enforce_cache_limit():
        # 按 LRU 把缓存压到 tmp_folder_max_size 以内(不碰固定/队列中/下载中的条目)
        try:
            from bot import cache_store
            cache_store.enforce_size_limit()
        except Exception:
            log.warning("url: cache size enforcement failed", exc_info=True)

    def _download(self):
        self._enforce_cache_limit()

        self.downloading = True
        self.progress = 0.0
        self.speed = 0.0
        self.downloaded_bytes = self.total_bytes = 0
        self.download_eta = None
        base_path = var.tmp_folder + self.id
        save_path = base_path

        # Download only if music is not existed
        self.ready = "preparing"
        self._set_stage('starting')

        # Stream-while-downloading needs the file to grow in place at its
        # final path (no .part + rename), so ffmpeg can read it while yt-dlp
        # is still writing. A marker file flags the download as incomplete so
        # a crash can never leave a truncated file that looks finished.
        streaming = var.config.getboolean('bot', 'stream_while_downloading',
                                          fallback=False)
        if streaming:
            try:
                open(base_path + ".incomplete", 'w').close()
            except OSError:
                streaming = False

        self.log.info("bot: downloading url (%s) %s " % (self.title, self.url))
        # 封面不再用 yt-dlp 的 writethumbnail 在下载前同步抓取+转换(会推迟音频开始下载),
        # 改为拿到信息后在后台线程单独下载
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': base_path,
            'noplaylist': True,
            'updatetime': False,
            'verbose': var.config.getboolean('debug', 'youtube_dl'),
            'progress_hooks': [self._ydl_progress_hook],
            # B 站 CDN 会在传输中途断开长连接(实测 "22089194 bytes read, 51358771 more expected"),
            # 让 yt-dlp 自己多续传几次
            'retries': 15,
            'socket_timeout': 20,
        }
        if streaming:
            ydl_opts['nopart'] = True

        cookie = var.config.get('youtube_dl', 'cookie_file')
        if cookie:
            ydl_opts['cookiefile'] = var.config.get('youtube_dl', 'cookie_file')

        user_agent = var.config.get('youtube_dl', 'user_agent')
        if user_agent:
            youtube_dl.utils.std_headers['User-Agent'] = var.config.get('youtube_dl', 'user_agent')

        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            attempts = max(1, var.config.getint('bot', 'download_attempts'))
            download_succeed = False
            for i in range(attempts):
                self.log.info("bot: download attempts %d / %d" % (i + 1, attempts))
                try:
                    client = self._youtube_fallback_client(i, base_path)
                    info = self._fresh_info() if i == 0 else None
                    if client:
                        self.log.info("url: retrying with YouTube player client %s", client)
                        alt_opts = dict(ydl_opts, extractor_args={'youtube': {'player_client': [client]}})
                        with youtube_dl.YoutubeDL(alt_opts) as alt:
                            info = alt.extract_info(self.url)
                    elif info is not None:
                        # 复用校验时读到的信息直接下载(等同 yt-dlp --load-info-json),
                        # 省掉第二次读取:实测 B 站从 4.7 秒缩到 0.7 秒开始收到数据
                        for c in self._info_cookies:
                            ydl.cookiejar.set_cookie(c)
                        info = ydl.process_ie_result(info, download=True)
                    else:
                        info = ydl.extract_info(self.url)  # 重试时重新读取,拿新的直链
                    self._fetch_thumbnail_async(info, base_path)
                    self._normalize_download_path(info, base_path)
                    download_succeed = True
                    break
                except:
                    error_traceback = traceback.format_exc().split("During")[0]
                    error = error_traceback.rstrip().split("\n")[-1]
                    self.log.error("bot: download failed with error:\n %s" % error)
                    # 不删已下载的部分:下一次尝试从断点续传(边下边播时正在播放的也是这个文件)
                    if i + 1 < attempts:
                        time.sleep(min(2 ** i, 8))

            if download_succeed:
                try:
                    os.remove(base_path + ".incomplete")
                except OSError:
                    pass
                self.path = save_path
                self.ready = "yes"
                self._set_stage('ready')
                self.log.info(
                    "bot: finished downloading url (%s) %s, saved to %s." % (self.title, self.url, self.path))
                self.downloading = False
                if not self.thumbnail:
                    self._read_thumbnail_from_file(base_path + ".jpg")
                self.version += 1  # notify wrapper to save me
                self._enforce_cache_limit()
                return True
            else:
                for f in glob.glob(base_path + "*"):
                    try:
                        os.remove(f)
                    except OSError:
                        pass  # 清理线程可能已经删掉了
                self.ready = "failed"
                self._set_stage('failed')
                self.downloading = False
                raise PreparationFailedError(tr('unable_download', item=self.format_title()))

    # 默认客户端拿到的音频直链对个别视频固定 403(实测 visionos 客户端,换 web_embedded 就好);
    # web/mweb 需要 PO token,没配时一般拿不到格式,所以排在后面
    YOUTUBE_FALLBACK_CLIENTS = ('web_embedded', 'tv', 'mweb')

    def _is_youtube(self):
        host = (urlparse(self.url).hostname or '').lower()
        return host == 'youtu.be' or host == 'youtube.com' or host.endswith(('.youtube.com', '.youtube-nocookie.com'))

    def _youtube_fallback_client(self, attempt, base_path):
        """第 2、4、6… 次尝试换备用客户端,其余仍用默认(偶发 403 重试默认客户端就能好)。
        已经下了一部分就不换:不同客户端可能选到不同的流,续传会把文件拼坏。"""
        if attempt % 2 == 0 or not self._is_youtube():
            return None
        try:
            if os.path.getsize(base_path) > 0:
                return None
        except OSError:
            pass
        clients = self.YOUTUBE_FALLBACK_CLIENTS
        return clients[(attempt // 2) % len(clients)]

    def _normalize_download_path(self, info, base_path):
        """bot 约定缓存文件就叫 <id>(不带扩展名)。万一 yt-dlp 实际写成了别的名字
        (实测出现过 <id>.m4a,结果下载完立刻被判"文件丢失"),改回约定的名字。"""
        if os.path.exists(base_path):
            return
        for download in (info or {}).get('requested_downloads') or []:
            actual = download.get('filepath') or download.get('_filename')
            if actual and actual != base_path and os.path.exists(actual):
                os.replace(actual, base_path)
                self.log.info("url: renamed downloaded file %s -> %s", actual, base_path)
                return
        for candidate in glob.glob(glob.escape(base_path) + '.*'):
            if not candidate.endswith(('.jpg', '.incomplete', '.part', '.ytdl')):
                os.replace(candidate, base_path)
                self.log.info("url: renamed downloaded file %s -> %s", candidate, base_path)
                return

    def _fresh_info(self):
        """校验时读到的信息还新鲜就拿来复用(深拷贝:yt-dlp 会就地改写)。"""
        if self._info is None or time.time() - self._info_at > self.INFO_REUSE_SECONDS:
            return None
        info, self._info = self._info, None  # 只用一次,失败重试时重新读取
        return copy.deepcopy(info)

    def _fetch_thumbnail_async(self, info, base_path):
        url = (info or {}).get('thumbnail')
        if not url or self.thumbnail:
            return

        def run():
            try:
                import requests
                headers = {'User-Agent': 'Mozilla/5.0'}
                if 'hdslb.com' in url or 'bilibili' in url:
                    headers['Referer'] = 'https://www.bilibili.com/'
                r = requests.get(url, headers=headers, timeout=10)
                r.raise_for_status()
                im = Image.open(BytesIO(r.content))
                im.convert('RGB').save(base_path + '.jpg', format='JPEG', quality=88)  # 缓存页/存入曲库用
                self.thumbnail = self._prepare_thumbnail(im)
                self.version += 1
            except Exception:
                self.log.debug("url: thumbnail fetch failed for %s", url, exc_info=True)

        threading.Thread(target=run, name="Thumb-" + self.id[:7], daemon=True).start()

    def eta_to_playable(self, playhead, buffer_secs, can_stream):
        """估算还要几秒能开始播放;不知道(还在读信息/连接中、没有速度数据)返回 None。"""
        if self.ready == 'yes':
            return 0.0
        if self.stage != 'downloading' or not self.speed or not self.total_bytes:
            return None
        if can_stream and self.duration and not self.no_stream:
            need = min(self.duration - 1, playhead + buffer_secs) / self.duration * self.total_bytes
        else:
            need = self.total_bytes
        return max(0.0, (need - self.downloaded_bytes) / self.speed)

    def _ydl_progress_hook(self, d):
        # Called frequently by yt-dlp while downloading; keep it cheap.
        status = d.get('status')
        if status == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            done = d.get('downloaded_bytes', 0)
            self._set_stage('downloading')
            self.downloaded_bytes = done or 0
            self.total_bytes = total or 0
            if d.get('speed'):
                # 平滑一下,yt-dlp 的瞬时速度抖得很厉害
                self.speed = d['speed'] if not self.speed else self.speed * 0.7 + d['speed'] * 0.3
            self.download_eta = d.get('eta')
            if total:
                try:
                    self.progress = max(0.0, min(1.0, done / total))
                except (TypeError, ZeroDivisionError):
                    pass
        elif status == 'finished':
            self.progress = 1.0

    def _read_thumbnail_from_file(self, path_thumbnail):
        # 封面坏了不影响播放,别让 PIL 的异常把"下载成功"变成崩溃
        try:
            if os.path.isfile(path_thumbnail):
                im = Image.open(path_thumbnail)
                self.thumbnail = self._prepare_thumbnail(im)
        except Exception:
            self.log.debug("url: could not read thumbnail %s", path_thumbnail, exc_info=True)

    def _prepare_thumbnail(self, im):
        im.thumbnail((100, 100), Image.LANCZOS)
        buffer = BytesIO()
        im = im.convert('RGB')
        im.save(buffer, format="JPEG")
        return base64.b64encode(buffer.getvalue()).decode('utf-8')

    def to_dict(self):
        dict = super().to_dict()
        dict['type'] = 'url'
        dict['url'] = self.url
        dict['duration'] = self.duration
        dict['path'] = self.path
        dict['title'] = self.title
        dict['thumbnail'] = self.thumbnail

        return dict

    def format_debug_string(self):
        return "[url] {title} ({url})".format(
            title=self.title,
            url=self.url
        )

    def format_song_string(self, user):
        if self.ready in ['validated', 'yes']:
            return tr("url_item",
                      title=self.title if self.title else "??",
                      url=self.url,
                      user=user)
        return self.url

    def format_current_playing(self, user):
        display = tr("now_playing", item=self.format_song_string(user))

        if self.thumbnail:
            thumbnail_html = '<img width="80" src="data:image/jpge;base64,' + \
                             self.thumbnail + '"/>'
            display += "<br />" + thumbnail_html

        return display

    def format_title(self):
        return self.title if self.title else self.url

    def display_type(self):
        return tr("url")
