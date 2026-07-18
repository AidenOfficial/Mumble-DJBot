# Mumble 音乐机器人 — ARM Ubuntu 部署文档

本文档是把这个机器人部署到一台 ARM 架构 Ubuntu Linux 机器上的完整步骤。
按顺序做即可。命令块都可以直接复制粘贴(尖括号 `<...>` 的部分替换成你自己的值)。

> **要部署到绿联 NAS,或其他不方便直接用 `apt` 装系统依赖的设备?**
> 请改用 Docker 方案,见 [`DOCKER.md`](DOCKER.md)。Docker 把全部依赖装在容器内,
> 与宿主机系统完全隔离,不会影响 NAS 本身。

---

## 一、开始前的准备清单

动手前先把下面这些准备好:

- [ ] 一台 **ARM 架构的 Ubuntu Linux** 机器,你有 `sudo` 权限,能 SSH 登录。
- [ ] **Mumble 服务器信息**:服务器地址(host)、端口(默认 `64738`)、连接密码(如果有)、希望机器人加入的**频道名**。
- [ ] **Spotify API 凭据**(`!spotify` 命令需要)—— 见下方「获取 Spotify API 凭据」。
- [ ] 你自己的 **Mumble 用户名**(会设为机器人的管理员,用于 `!update`、`!kill` 等管理命令)。
- [ ] (可选)**B 站 cookies 文件** —— 只有要播放大会员专享/番剧时才需要,见第九节。

### 获取 Spotify API 凭据

1. 打开 <https://developer.spotify.com/dashboard>,用**任意 Spotify 账号**登录(免费账号即可,**不需要 Premium**)。
2. 点 **Create app**。App name / description 随便填;**Redirect URI** 填 `http://localhost:8888/callback`(spotdl 用不到回调,但表单必填);API 勾选 **Web API**。
3. 创建后进入该 App 的 **Settings**,记下 **Client ID**;点 **View client secret** 记下 **Client Secret**。
4. 这两个值稍后填进配置文件。

---

## 二、第 1 步:把代码传到 ARM 机器

机器人代码现在在你这台 Windows 机器上(本仓库目录)。需要整个目录传到 ARM 机器。

**在 Windows 上**(PowerShell),把整个项目目录拷过去:

```powershell
scp -r "E:\Claude\Mumble-Music" <你的用户名>@<ARM机器IP>:~/
```

**在 ARM 机器上**(SSH 登录后),把它移动到标准安装路径 `/opt/botamusique`:

```bash
sudo mv ~/Mumble-Music /opt/botamusique
cd /opt/botamusique
```

> 安装路径用 `/opt/botamusique` 是因为 `deploy/` 里的 systemd 文件都按这个路径写死。
> 如果你坚持用别的路径,需要同步改 `deploy/` 下三个 `.service` / `.timer` 文件里的路径。

---

## 三、第 2 步:运行安装脚本

```bash
cd /opt/botamusique
bash deploy/install.sh
```

这个脚本会:用 `apt` 安装系统依赖(Python、ffmpeg、libopus 等)、创建 Python 虚拟环境 `venv/`、安装全部 Python 依赖(含 `yt-dlp`、`spotdl`)、做安装后验证、并生成一份初始 `configuration.ini`。

ARM 上首次安装可能较慢(部分依赖需要本地编译),耐心等待。结束时若看到 spotdl / yt-dlp / ffmpeg 三个版本号正常打印,说明依赖就绪。

---

## 四、第 3 步:编辑配置文件

用编辑器打开配置文件:

```bash
nano /opt/botamusique/configuration.ini
```

最省事的做法:**把整个文件内容替换成下面这份最小配置**(其余所有选项都会自动从 `configuration.default.ini` 取默认值):

```ini
[server]
host = <你的 Mumble 服务器地址>
port = 64738
; 如果服务器需要密码,取消下面这行的分号并填写;不需要就删掉这行
;password = <连接密码>
; 机器人要加入的频道名;多级频道用斜杠,如 Games/Squad
channel = <频道名>

[bot]
username = MusicBot
; 管理员 Mumble 用户名,多个用分号隔开,如 Alice;Bob
admin = <你的 Mumble 用户名>

[spotify]
client_id = <你的 Spotify Client ID>
client_secret = <你的 Spotify Client Secret>
```

保存退出(nano 里按 `Ctrl+O`、回车、`Ctrl+X`)。

**说明:**
- 不填 `[spotify]` 也能启动,只是 `!spotify` 命令会提示未配置;`!bili` 不受影响。
- 想播本地音乐文件的话,把文件放进 `/opt/botamusique/music_folder/` 即可。
- 想播超过 60 分钟的长视频:在 `[bot]` 段加一行 `max_track_duration = 180`(单位分钟)。
- 响度均衡(让大小声不一的歌音量趋于一致)默认已开启;如想关闭,在 `[bot]` 段加 `normalize_volume = False`。

---

## 五、第 4 步:手动测试

先在前台手动跑一次,确认能连上服务器:

```bash
cd /opt/botamusique
venv/bin/python3 mumbleBot.py --config configuration.ini
```

观察输出日志,应该能看到连接成功、加入频道。然后:

1. 用你自己的 Mumble 客户端登录同一台服务器、进入同一个频道。
2. 在频道里发文字 `!help` —— 机器人应回复命令列表。
3. 测试 `!bili <B站视频链接>` —— 频道里应能听到视频声音。
4. 测试 `!spotify <歌名>` 或 `!spotify <Spotify链接>` —— 稍等片刻后应开始播放。
5. 测试 `!queue`、`!skip`、`!volume 80` 等队列与播放控制。

确认正常后,在终端按 `Ctrl+C` 停止。如果有问题,见第九节「故障排查」。

---

## 六、第 5 步:设置开机自启(systemd)

让机器人作为系统服务后台常驻、崩溃自动重启、开机自启。

```bash
# 1. 创建一个专用的非特权用户来运行机器人
sudo useradd --system --home /opt/botamusique --shell /usr/sbin/nologin botamusique

# 2. 把整个目录的属主改成该用户
sudo chown -R botamusique:botamusique /opt/botamusique

# 3. 安装 service 文件并启动
sudo cp /opt/botamusique/deploy/botamusique.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now botamusique

# 4. 查看运行状态与实时日志
sudo systemctl status botamusique
sudo journalctl -u botamusique -f
```

> 之后如果还要再改 `configuration.ini`,因为文件属主已是 `botamusique`,需要用 `sudo nano /opt/botamusique/configuration.ini`,改完执行 `sudo systemctl restart botamusique` 重启生效。

---

## 七、第 6 步:启用 yt-dlp 每日自动更新

Bilibili / YouTube 经常改版,旧版 yt-dlp 会失效。这一步让系统每天自动把 `yt-dlp` 和 `spotdl` 升级到最新版并重启机器人。

```bash
sudo cp /opt/botamusique/deploy/botamusique-ytdlp-update.service /etc/systemd/system/
sudo cp /opt/botamusique/deploy/botamusique-ytdlp-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now botamusique-ytdlp-update.timer

# 确认定时器已生效、查看下次触发时间
sudo systemctl list-timers botamusique-ytdlp-update.timer
```

部署到此结束。机器人已在后台运行,并会每天自动更新。

---

## 八、命令速查(发给小队成员)

机器人的所有操作都是在 Mumble 频道里发文字命令(以 `!` 开头)。

**点歌**
| 命令 | 说明 |
|---|---|
| `!bili <链接 / BV号>` | 播放 Bilibili 视频音频。支持完整链接、`b23.tv` 短链、裸 BV/av 号 |
| `!bili BV1xx... p3` | 播放多 P 视频的第 3 个分 P |
| `!spotify <链接>` | 播放 Spotify 单曲 / 专辑 / 歌单链接 |
| `!spotify <关键词>` | 按关键词搜 Spotify 并播放最佳匹配,如 `!spotify 周杰伦 晴天` |
| `!yplay <关键词>` | 直接搜 YouTube 并播放第一条结果 |
| `!ysearch <关键词>` | 搜 YouTube 列出结果,再用 `!sl <序号>` 选 |
| `!url <链接>` | 播放 YouTube / SoundCloud 等链接 |

**队列与播放控制**
| 命令 | 说明 |
|---|---|
| `!queue` | 查看播放队列 |
| `!np` | 显示当前播放 |
| `!skip` | 下一首 |
| `!play <N>` | 跳到队列第 N 首 |
| `!rm <N>` | 删除队列第 N 首 |
| `!clear` | 清空队列 |
| `!volume <0-100>` | 查看 / 设置音量 |
| `!mode <模式>` | 切换播放模式:`one-shot` / `repeat` / `random` / `autoplay` |

**闪避 ducking**(有人说话时自动调低音乐音量,通常由管理员设一次)
| 命令 | 说明 |
|---|---|
| `!duck on/off` | 开 / 关闭闪避 |
| `!duckv <0-100>` | 闪避时音乐降到的音量 |
| `!duckthres <数字>` | 触发阈值;不带参数则显示当前值和最近噪音峰值 |
| `!duckdelay <秒>` | 噪音需持续多少秒才触发闪避,用来过滤短噪音 |

---

## 九、故障排查

**机器人连不上服务器**
- 核对 `[server]` 的 host / port / password;`journalctl -u botamusique -e` 看具体错误。
- 若服务器对用户名有注册要求,换一个未被占用的 `[bot] username`。

**报错 `module 'ssl' has no attribute 'wrap_socket'`**
- 项目现已使用社区维护的 pymumble 2.x(Codeberg 版,`requirements.txt` 里锁定了 commit),
  它要求 **Python ≥ 3.12**,并已解除旧版对 protobuf 3.20 的锁定。
  如果部署机的 python3 低于 3.12,请先安装 python3.12 再建 venv。

**机器人在频道里但没声音**
- 编辑配置,在 `[debug]` 段加 `ffmpeg = True` 和 `youtube_dl = True`,重启后看 `journalctl` 里的 ffmpeg / 下载日志。
- 报 `libopus` 相关错误:`sudo apt install --reinstall libopus0`。

**`!bili` 报下载失败**
- 多半是 Bilibili 改版导致 yt-dlp 暂时失效。手动更新:`sudo /opt/botamusique/venv/bin/pip install --upgrade yt-dlp`,再 `sudo systemctl restart botamusique`(第六步的定时器平时会自动做这件事)。
- 大会员专享 / 番剧内容:需要 B 站登录 cookies。用浏览器扩展(如 "Get cookies.txt")导出 `bilibili.com` 的 Netscape 格式 cookies 文件,放到服务器上,在配置文件 `[youtube_dl]` 段加一行 `cookie_file = /opt/botamusique/bili_cookies.txt`。

**`!spotify` 失败**
- 提示「未配置」:检查 `[spotify]` 的 `client_id` / `client_secret` 是否填对。
- 提示「没找到匹配曲目」、或 spotdl 输出含 `require Deno`:spotdl 需要 Deno 来下载 YouTube 音频。`install.sh` 会自动装;手动补装:`apt install -y unzip && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh`。
- 仍失败且报 YouTube 要求登录验证:云服务器 IP 被 YouTube 风控,需给 yt-dlp 配置 cookies。

**`install.sh` 报 `bad interpreter` 或带 `\r` 的奇怪错误**
- 说明文件换行符被改成了 Windows 的 CRLF。修复:`sudo apt install -y dos2unix && dos2unix /opt/botamusique/deploy/*`。

---

## 十、后续维护

- **看日志**:`sudo journalctl -u botamusique -f`
- **重启 / 停止**:`sudo systemctl restart botamusique` / `sudo systemctl stop botamusique`
- **yt-dlp / spotdl 更新**:第六步的定时器每天自动做;也可手动触发 `sudo systemctl start botamusique-ytdlp-update.service`。
- **磁盘**:Spotify 下载缓存在 `/opt/botamusique/spotdl_cache/`,会按 `[spotify] max_cache_size`(默认 2048 MB)自动清理旧文件,一般无需手动管理。
- **改机器人代码**:本项目的 Bilibili / Spotify 功能是在 botamusique 基础上的本地改动(未提交到上游)。如果以后想用 git 管理、方便升级,建议把它推到你自己的 GitHub fork 再维护。
