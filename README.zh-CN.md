<div align="center">
<img src="static/image/logo.png" alt="Mumble-DJBot logo" width="160px" />
<h1>Mumble-DJBot</h1>
<p>自托管的 <a href="https://www.mumble.info/">Mumble</a> 音乐机器人，支持聊天命令与 Web 界面控制。</p>

<p>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT" /></a>
<img src="https://img.shields.io/badge/python-3.12-blue.svg" alt="Python 3.12" />
<img src="https://img.shields.io/badge/deploy-Docker-2496ED.svg" alt="Docker" />
</p>

<p><a href="README.md">English</a> · <strong>简体中文</strong></p>
</div>

---

Mumble-DJBot 可播放来自 YouTube、Bilibili、Spotify、直播、网络电台及本地曲库的音频。用户可通过聊天命令或浏览器中控制；控制台同时提供播放队列管理、个人歌单、缓存管理与播放统计。

本项目 fork 自 [azlux/botamusique](https://github.com/azlux/botamusique)（上游已归档）。在此基础上重写了 Web 界面，新增个人歌单、歌单导入、边下边播与频道跟随等功能，并以 Docker 配合 Cloudflare Tunnel / Cloudflare Access 作为推荐的部署方式。

## 目录

- [功能](#功能)
- [环境要求](#环境要求)
- [安装](#安装)
- [Web 界面与远程访问](#web-界面与远程访问)
- [安全注意事项](#安全注意事项)
- [使用](#使用)
- [配置](#配置)
- [开发](#开发)
- [致谢](#致谢)
- [许可证](#许可证)

## 功能

### 音源

- YouTube、Bilibili（BV 号或链接）、SoundCloud 等 [yt-dlp] 支持的站点及其播放列表。
- Spotify 单曲、歌单及关键词搜索，经 [spotDL] 获取音频。
- 直播、网络电台（支持 [radio-browser.info](https://www.radio-browser.info/) 检索）以及本地音频文件。

### 播放

- **边下边播：** 长视频缓冲约 30 秒音频后即开始播放，其余部分在后台继续下载。实测 50 分钟的 Bilibili 视频从点歌到开始播放约需 6 秒。
- **跳过非音乐片段：** 自动跳过 [SponsorBlock] 与 [BilibiliSponsorBlock] 社区标注的片段，如片头口播、赞助推广等。
- 支持队列预下载、响度标准化（`loudnorm`）、有人发言时自动降低音量（ducking）以及立体声输出。
- 提供五种播放模式：顺序、循环、单曲循环、随机、自动（从曲库中随机选曲）。

### 频道管理

- Web 界面实时显示服务器的频道树，可在其中设置默认频道或移动机器人。
- 跟随模式：频道无人时跟随最后离开的用户，或始终跟随指定用户。
- 频道持续无人达到设定时长（默认 5 分钟）后自动暂停，有人返回时自动恢复。

### Web 界面

控制台位于 `/`，原有界面保留在 `/legacy`。

- **Now Playing：** 显示封面与播放进度；准备面板显示当前所处阶段及预计开始播放的时间。
- **队列：** 支持拖拽排序，并可统一搜索 YouTube 与 Bilibili。
- **曲库：** 支持浏览与分片上传，单文件默认上限 4 GB，可断点续传；视频文件仅保留音轨。
- **缓存：** 显示存储占用与播放次数；常听的曲目可固定或转存至曲库；达到容量上限时优先淘汰最久未使用的条目。
- **统计：** 显示最常播放曲目、点歌最多的用户、最活跃的时段以及最常被跳过的曲目。

### 个人歌单

- 以 Cloudflare Access 传递的登录邮箱识别用户，用户可设置显示别名。
- 每位用户拥有独立歌单，可立即播放、随机播放或追加到队列。
- 可从 YouTube、网易云音乐、QQ 音乐与 Spotify 导入歌单。后三者的曲目会自动匹配到 YouTube 音源，匹配时同时处理简体与繁体标题。
- 可通过一次性绑定码（`!bind`）将 Mumble 账号与 Web 身份关联。关联后可在聊天中使用 `!mylist`、`!fav` 管理个人歌单。

### 可靠性

- 主循环停滞超过 120 秒时，看门狗会让进程退出并重启；心跳文件可用于容器健康检查。
- 下载失败时自动断点续传并重试；单首曲目出错不影响整个进程。

## 环境要求

- 机器人可连接的 Mumble 服务器（Murmur）。
- **Docker 部署（推荐）：** Docker Engine 与 Docker Compose。所有运行时依赖均已包含在镜像中。
- **直接部署：** Python 3.12、FFmpeg 与 Opus 编解码器，详见 [`deploy/DEPLOY.md`](deploy/DEPLOY.md)。
- **可选：** Spotify API 凭据，仅 `!spotify` 命令需要。
- **可选：** Cloudflare 账号，用于远程访问 Web 界面。

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/AidenOfficial/Mumble-DJBot.git
cd Mumble-DJBot
```

### 2. 创建配置文件

```bash
cp configuration.example.ini configuration.ini
```

> [!IMPORTANT]
> 启动容器前必须先创建 `configuration.ini`，否则 Docker 会在该路径创建一个目录，导致机器人无法读取配置。

最小配置示例如下。未填写的选项从 `configuration.default.ini` 读取默认值，请勿修改该文件。

```ini
[server]
host = mumble.example.com
port = 64738
; password = <服务器密码>
channel = Music              ; 多级频道以斜杠分隔，如 Games/Squad

[bot]
username = MusicBot
admin = YourMumbleName       ; 多个管理员以分号分隔
language = zh_CN             ; 可选语言见 lang/ 目录

[webinterface]
enabled = True
listening_addr = 0.0.0.0     ; 容器内必须监听所有地址，由 Compose 网络隔离

[spotify]
; 仅 !spotify 命令需要。导入 100 首以内的 Spotify 歌单无需凭据。
client_id =
client_secret =
```

### 3. 构建并启动

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

机器人以普通客户端身份连接服务器，无需映射任何端口。启动后进入机器人所在频道发送 `!help`，即可确认其已正常运行。

### 4. 保持解析组件为最新版本

容器每次启动时都会升级 yt-dlp 与 spotDL（`BAM_UPDATE_ON_START=1`）。YouTube 与 Bilibili 改版频繁，建议每天重启一次容器，例如添加以下 cron 任务：

```cron
0 5 * * * docker restart mumble-music
```

NAS 部署、cookies 配置与日常运维说明见 [`deploy/DOCKER.md`](deploy/DOCKER.md)。

## Web 界面与远程访问

推荐通过 Cloudflare Tunnel 配合 Cloudflare Access 对外提供 Web 界面。身份验证由 Cloudflare 负责，8181 端口始终不对外开放。

1. 在 Cloudflare Zero Trust 控制台创建 Tunnel，并将 Public hostname 的回源地址设为 `http://botamusique:8181`。
2. 为同一域名创建 Access 应用，并配置允许访问的邮箱策略。
3. 写入 Tunnel token 并启动 `tunnel` profile：

   ```bash
   echo 'CLOUDFLARE_TUNNEL_TOKEN=<token>' > .env
   docker compose --profile tunnel up -d
   ```

详细步骤见 [`deploy/WEBUI.md`](deploy/WEBUI.md)。

## 安全注意事项

> [!WARNING]
> 默认配置 `auth_method = none` 下，应用本身不进行任何身份验证，完全依赖 Cloudflare Access。

- **请勿将 8181 端口暴露至公网或不受信任的网络。**
- **建议启用 Access JWT 校验：** 在 `[webinterface]` 中设置 `access_team_domain` 与 `access_aud`。启用后，机器人会拒绝所有不带有效 Access 令牌的请求，绕过 Tunnel 的访问无法冒充其他用户。
- **使用 `auth_method = token` 时，务必将 `flask_secret` 改为足够长的随机值**，该值用于签名会话 Cookie。
- **请在 Mumble 服务器上注册管理员账号。** 目前管理员按显示名识别；未注册的用户名可能在管理员离线时被他人占用。

## 使用

### 聊天命令

命令以 `!` 开头，也可使用全角 `！`。只要前缀不产生歧义即可识别，例如 `!sk` 等同于 `!skip`。命令名可在 `[commands]` 中修改。发送 `!help` 可查看完整命令列表。

| 命令 | 说明 |
|---|---|
| `!url <链接>` / `!bili <BV 号或链接>` | 添加 YouTube / Bilibili 视频的音频 |
| `!yplay <关键词>` / `!ysearch <关键词>` | 添加 YouTube 首个搜索结果 / 列出搜索结果 |
| `!spotify <链接或关键词>` | 添加 Spotify 单曲或歌单，或搜索 Spotify |
| `!playlist <链接>` | 添加整个播放列表 |
| `!live <链接>` / `!radio <名称或链接>` | 播放直播或网络电台 |
| `!file <路径>` / `!filematch <关键词>` | 从本地曲库添加曲目 |
| `!play [序号]` / `!pause` / `!skip` / `!stop` | 播放控制 |
| `!queue` / `!np` / `!rm <序号>` | 查看队列 / 查看当前曲目 / 删除曲目 |
| `!mode <1–5>` | 顺序、循环、随机、自动、单曲循环 |
| `!repeat [次数]` | 将当前曲目再加入队列若干次（最多 20 次） |
| `!volume <0–100>` / `!duck on\|off` | 设置音量 / 开关 ducking |
| `!joinme` / `!oust` | 让机器人移动到你所在的频道 / 停止播放并返回默认频道 |
| `!bind <绑定码>` / `!mylist [歌单] [shuffle]` / `!fav [歌单]` | 个人歌单（绑定码在 Web 界面生成） |
| `!web` | 显示 Web 界面地址 |

`[bot] admin` 中列出的管理员还可使用 `!kill`、`!update`、`!urlban`、`!userban`、`!maxvolume`、`!webuseradd` 等命令。

## 配置

全部选项及说明见 [`configuration.example.ini`](configuration.example.ini)。常用选项如下：

| 选项 | 默认值 | 说明 |
|---|---|---|
| `[bot] stream_while_downloading` | `True` | 长曲目下载完成前即开始播放（仅对时长不少于 `stream_min_duration` 秒的曲目生效） |
| `[bot] tmp_folder_max_size` | `4096` | 下载缓存上限（MB），超出时优先淘汰最久未使用的条目 |
| `[bot] max_track_duration` | `0` | 单曲时长上限（分钟），`0` 表示不限制 |
| `[bot] sponsorblock` | `True` | 跳过非音乐片段，类别由 `sponsorblock_categories` 指定 |
| `[bot] when_nobody_in_channel` | `nothing` | 频道无人时立即执行的操作（`pause`、`stop`）；一般建议改用 Web 设置中的延时自动暂停 |
| `[bot] playback_mode` | `one-shot` | 启动时的播放模式 |
| `[webinterface] max_upload_file_size` | `4G` | 单个上传文件的大小上限 |
| `[youtube_dl] cookie_file` | *（空）* | Netscape 格式的 cookies，用于 Bilibili 大会员内容或已登录的 YouTube 访问 |

默认频道、跟随模式与无人自动暂停在 Web 界面的 Settings 页中设置，无需写入 INI 文件。

## 开发

### 运行测试

测试不需要 Mumble 服务器，也不需要安装 `pymumble`。

```bash
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt pytest
venv/bin/python -m pytest tests
```

需使用 Python 3.12：pymumble 2.x 要求 Python 3.12 及以上，而 Python 3.13 移除了 `audioop` 模块。如需使用 3.13，请额外安装 `audioop-lts`。

### 构建 Web 界面

控制台源码位于 `webui/`，基于 Vue 3、TypeScript、Vite 与 Tailwind CSS 4。

```bash
cd webui
npm ci
npm run dev     # 开发服务器，/api 代理至 127.0.0.1:8181
npm run build   # 生产构建输出至 webui/dist
```

为便于没有 Node.js 的环境部署，`webui/dist` 会随仓库提交；构建 Docker 镜像时也会重新生成。修改前端后，请一并提交更新后的构建产物。

### 冒烟测试

`scripts/smoke_test.py` 可在不连接 Mumble 的情况下，检查运行环境以及 Bilibili、Spotify 的下载链路，详见 [`deploy/VERIFY.md`](deploy/VERIFY.md)。

### 项目结构

| 路径 | 内容 |
|---|---|
| `mumbleBot.py`, `bot/` | 程序入口；连接管理、播放主循环（`player.py`）、频道跟随（`channels.py`）、缓存与清理 |
| `media/` | 各类音源（URL、Bilibili、Spotify、直播、电台、本地文件）、播放队列、SponsorBlock |
| `commands/` | 聊天命令处理 |
| `interface.py`, `web_*.py` | Flask 应用：旧版接口与 `/api/*` 接口 |
| `playlist_import.py` | 歌单导入与 YouTube 匹配 |
| `webui/` | Web 界面源码与构建产物 |
| `lang/` | 聊天消息与帮助文本的翻译 |
| `deploy/` | 部署文档与 systemd 单元文件 |
| `tests/` | 单元测试 |

## 致谢

本项目基于 Azlux 及 @TerryGeng、@mertkutay 等贡献者开发的 [botamusique](https://github.com/azlux/botamusique)，并使用了 [pymumble](https://codeberg.org/pymumble/pymumble)、[yt-dlp]、[spotDL]、[SponsorBlock]、[BilibiliSponsorBlock] 与 [OpenCC](https://github.com/BYVoid/OpenCC)。

## 许可证

本项目以 [MIT 许可证](LICENSE) 发布。

[yt-dlp]: https://github.com/yt-dlp/yt-dlp
[spotDL]: https://github.com/spotDL/spotify-downloader
[SponsorBlock]: https://sponsor.ajay.app/
[BilibiliSponsorBlock]: https://bsbsb.top/
