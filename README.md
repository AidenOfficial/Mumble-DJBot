<div align="center">
<img src="static/image/logo.png" alt="Mumble-DJBot" width="160px" />
<h1>Mumble-DJBot</h1>
<p>给 <a href="https://www.mumble.info/">Mumble</a> 频道放歌的机器人：聊天命令点歌，网页上管队列、歌单和缓存。</p>
</div>

本项目 fork 自 [azlux/botamusique](https://github.com/azlux/botamusique)（上游已归档）。在它的基础上重写了 Web 界面，加了个人歌单、歌单导入、边下边播、频道跟随等功能，并按 Docker + Cloudflare Tunnel 的部署方式整理过。

## 功能

**音源**
- YouTube、Bilibili（BV 号 / 链接）、SoundCloud 等 yt-dlp 支持的站点，以及它们的播放列表。
- Spotify 歌曲 / 歌单 / 关键词（经 spotdl 到 YouTube 取音频）。
- 直播（`!live`）、网络电台（含 radio-browser.info 搜索）、本地曲库文件。

**播放**
- 长视频边下边播：攒够约 30 秒音频就开播，剩下的在后台继续下载。B 站 50 分钟视频从点歌到出声约 6 秒。
- 自动跳过非音乐片段（SponsorBlock / BilibiliSponsorBlock 社区标注的片头说话、赞助口播等）。
- 队列预下载、音量标准化（loudnorm）、有人说话时自动压低音量（ducking）、立体声。
- 播放模式：顺序、循环、单曲循环、随机、自动（从曲库随机挑）。

**频道**
- 设置页能看到服务器的频道树，可以设默认频道，或让 bot 立刻移过去。
- 跟随模式：频道没人时跟着最后离开的人走，或始终跟着指定的人。
- 频道里连续 5 分钟没人自动暂停，有人回来自动继续。

**Web 界面**（`/`，旧界面保留在 `/legacy`）
- Now Playing（封面、进度、等待开播的阶段和预计时间）、队列拖拽排序、YouTube + B 站统一搜索。
- 曲库浏览和分片上传：单文件默认上限 4 GB，断网自动续传，视频只保留音轨。
- 缓存管理：查看每首的占用和播放次数，可以固定常听的歌或存进曲库。超过上限时按"最久没用"淘汰，常听的歌最后才淘汰。
- 播放统计：最常放、谁点得最多、什么时段最热闹、最常被跳过。

**个人歌单**
- 用 Cloudflare Access 登录的邮箱识别身份，可以给自己设别名。
- 每人有自己的歌单，可以"立即播放 / 随机 / 追加到队列"。
- 导入 YouTube 播放列表、网易云音乐 / QQ 音乐歌单、Spotify 歌单 / 专辑。网易云、QQ 音乐和 Spotify 的歌会自动到 YouTube 匹配音源，简繁体标题都能匹配。
- 网页上生成绑定码后，在 Mumble 里发 `!bind` 绑定账号，之后可以在聊天里用 `!mylist`、`!fav` 操作自己的歌单。

**稳定性**
- 看门狗：主循环卡住超过 120 秒自动退出，由 Docker 拉起。另有心跳文件供健康检查使用。
- 下载失败自动续传重试，单首歌出错不影响整个进程。

## 快速开始（Docker）

推荐用 Docker 部署，所有依赖（Python 3.12、ffmpeg、opus、yt-dlp、spotdl、Deno）都在镜像里。bot 是主动连出到 Mumble 服务器的客户端，不需要映射端口。

```bash
git clone https://github.com/AidenOfficial/Mumble-DJBot.git
cd Mumble-DJBot
cp configuration.example.ini configuration.ini   # 必须在 up 之前建好，否则 Docker 会把它挂成目录
nano configuration.ini
```

最小配置。没写的项从 `configuration.default.ini` 取默认值，**不要改那个文件**：

```ini
[server]
host = mumble.example.com
port = 64738
;password = 服务器密码
channel = 音乐频道        ; 多级频道写成 Games/Squad

[bot]
username = MusicBot
admin = 你的Mumble用户名  ; 多个用分号隔开
language = zh_CN

[webinterface]
enabled = True
listening_addr = 0.0.0.0  ; 容器内要监听所有地址，由 compose 网络隔离

; 只有 !spotify 命令需要。导入 100 首以内的 Spotify 歌单不需要
[spotify]
client_id =
client_secret =
```

启动：

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

进同一个频道发 `!help` 就能看到全部命令。

容器每次启动都会把 yt-dlp / spotdl 升级到最新（`BAM_UPDATE_ON_START=1`）。B 站和 YouTube 经常改版，建议每天定时 `docker restart mumble-music` 一次。

完整步骤（绿联 NAS、cookies、日常运维）见 [`deploy/DOCKER.md`](deploy/DOCKER.md)。不用 Docker、直接装在 Ubuntu 上的步骤见 [`deploy/DEPLOY.md`](deploy/DEPLOY.md)。

## Web 界面与公网访问

推荐的公网发布方式是 Cloudflare Tunnel + Access：登录鉴权交给 Cloudflare，8181 端口不暴露。

```bash
echo 'CLOUDFLARE_TUNNEL_TOKEN=eyJh...' > .env
docker compose --profile tunnel up -d
```

Cloudflare 控制台里 Tunnel 的回源地址填 `http://botamusique:8181`，再建一个 Access 应用按邮箱放行。详细步骤见 [`deploy/WEBUI.md`](deploy/WEBUI.md)。

> [!IMPORTANT]
> 默认 `auth_method = none`，应用本身不做登录，全靠 Cloudflare Access 挡在前面：
> - **不要把 8181 端口直接暴露到公网或不可信的局域网。**
> - 建议在 `[webinterface]` 里填上 `access_team_domain` 和 `access_aud`，开启 Access JWT 校验。开启后，绕过 Tunnel 直连的请求无法伪造登录身份。
> - 改用 `auth_method = token` 时，一定要把 `flask_secret` 换成随机字符串。

## 聊天命令

命令以 `!` 开头（全角 `！` 也可以），支持前缀匹配，比如 `!sk` 就是 `!skip`。在 `[commands]` 里可以改名。下面是常用的，完整列表发 `!help`。

| 命令 | 作用 |
|---|---|
| `!url <链接>` / `!bili <BV号或链接>` | 点一首 YouTube / B 站视频的音频 |
| `!yplay <关键词>` / `!ysearch <关键词>` | 搜 YouTube 并直接加第一条 / 列出结果 |
| `!spotify <链接或关键词>` | Spotify 歌曲、歌单或搜索 |
| `!playlist <链接>` | 加入整个播放列表 |
| `!live <链接>` / `!radio <名字或链接>` | 直播 / 网络电台 |
| `!file <路径>` / `!filematch <关键词>` | 从本地曲库添加 |
| `!play [序号]` / `!pause` / `!skip` / `!stop` | 播放控制 |
| `!queue` / `!np` / `!rm <序号>` | 看队列 / 当前曲 / 删除 |
| `!mode <1-5>` | 顺序 / 循环 / 随机 / 自动 / 单曲循环 |
| `!repeat [次数]` | 把当前曲再排几遍（最多 20） |
| `!volume <0-100>` / `!duck on\|off` | 音量 / 说话时压低音量 |
| `!joinme` / `!oust` | 叫 bot 来自己的频道 / 停止并回默认频道 |
| `!bind <绑定码>` / `!mylist [歌单] [shuffle]` / `!fav [歌单]` | 个人歌单（先在网页上生成绑定码） |
| `!web` | 获取网页地址 |

管理员（`[bot] admin` 里的用户）还可以用 `!kill`、`!update`、`!urlban`、`!userban`、`!maxvolume`、`!webuseradd` 等命令。

> [!NOTE]
> 管理员目前按 Mumble 显示名判定。请把管理员账号在 Mumble 服务器上注册，否则别人可以在你离线时用同名登录冒充你。

## 常用配置

所有选项及说明见 [`configuration.example.ini`](configuration.example.ini)。几个常改的：

| 选项 | 默认 | 说明 |
|---|---|---|
| `[bot] stream_while_downloading` | `True` | 长视频边下边播（时长 ≥ `stream_min_duration` 秒的才启用） |
| `[bot] tmp_folder_max_size` | `4096` | 下载缓存上限（MB），超出按最久没用淘汰 |
| `[bot] max_track_duration` | `0` | 单曲时长上限（分钟），0 = 不限 |
| `[bot] sponsorblock` | `True` | 跳过非音乐片段；分类见 `sponsorblock_categories` |
| `[bot] when_nobody_in_channel` | `nothing` | 频道没人时立即 `pause` / `stop`；一般保持 `nothing`，用设置页里的"5 分钟后自动暂停" |
| `[bot] playback_mode` | `one-shot` | 启动时的播放模式 |
| `[webinterface] max_upload_file_size` | `4G` | 网页上传单文件上限 |
| `[youtube_dl] cookie_file` | 空 | B 站大会员 / YouTube 登录 cookies（Netscape 格式） |

默认频道、跟随模式、自动暂停在网页设置页里改，不用写进 ini。

## 开发

运行测试（不需要 Mumble 服务器，也不需要装 pymumble）：

```bash
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt pytest
venv/bin/python -m pytest tests
```

需要 Python 3.12。pymumble 2.x 要求 ≥ 3.12，而 3.13 移除了 `audioop`，要用 3.13 就得另装 `audioop-lts`。

Web 前端在 `webui/`，技术栈是 Vue 3 + TypeScript + Vite + Tailwind 4：

```bash
cd webui
npm ci
npm run dev     # 开发服务器，/api 代理到 127.0.0.1:8181
npm run build   # 产物在 webui/dist，随仓库提交
```

Docker 构建时也会重新 build 一次前端。改完前端请把 `webui/dist` 一起提交，这样不用 Docker 的部署也能直接用。

不连 Mumble、只验证运行环境和 B 站 / Spotify 下载链路，可以跑 `scripts/smoke_test.py`，用法见 [`deploy/VERIFY.md`](deploy/VERIFY.md)。

<details>
<summary>代码结构</summary>

| 路径 | 内容 |
|---|---|
| `mumbleBot.py`, `bot/` | 入口；连接、播放主循环（`player.py`）、频道跟随（`channels.py`）、缓存与清理 |
| `media/` | 各类音源（URL、B 站、Spotify、直播、电台、本地文件）、播放队列、SponsorBlock |
| `commands/` | 聊天命令 |
| `interface.py`, `web_*.py` | Flask：旧接口与 `/api/*`（状态、队列、搜索、上传、缓存、频道、用户与歌单） |
| `playlist_import.py` | 歌单导入与 YouTube 匹配 |
| `webui/` | 新版 Web 前端 |
| `lang/` | 聊天消息与帮助文本翻译（`zh_CN`、`en_US` 等） |
| `deploy/` | 部署文档、systemd 单元 |
| `tests/` | 单元测试 |

</details>

## 致谢与许可

基于 [azlux/botamusique](https://github.com/azlux/botamusique)（作者 Azlux，协作者 @TerryGeng、@mertkutay），使用 MIT 许可证，见 [`LICENSE`](LICENSE)。用到的主要项目：[pymumble](https://codeberg.org/pymumble/pymumble)、[yt-dlp](https://github.com/yt-dlp/yt-dlp)、[spotDL](https://github.com/spotDL/spotify-downloader)、[SponsorBlock](https://sponsor.ajay.app/)、[BilibiliSponsorBlock](https://bsbsb.top/)、[OpenCC](https://github.com/BYVoid/OpenCC)。
