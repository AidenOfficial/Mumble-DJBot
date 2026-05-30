# Mumble 音乐机器人 — 绿联 NAS / Docker 部署文档

本文档把机器人以 **Docker 容器**方式部署到**绿联 NAS(UGOS Pro)**。
同样适用于任何装了 Docker 的机器。命令块可直接复制粘贴(`<...>` 替换成你自己的值)。

---

## 一、为什么用 Docker(而不是直接 apt 装)

绿联 NAS 的系统(UGOS Pro)是一个封闭的定制 Debian,直接在主机上 `apt install`
很容易把系统软件包依赖搞乱(你已经遇到过)。Docker 把机器人需要的一切
(Python、ffmpeg、libopus、yt-dlp、spotdl、Deno)全部装进**容器内部一个干净的
Debian 镜像**里:

- 容器里的 `apt` 和 NAS 主机的 `apt` 是**两套完全独立**的东西,互不影响,
  构建镜像不会动 NAS 主机一根毫毛。
- 删掉容器,机器人就干干净净地消失,不在主机上留任何依赖。
- 镜像是按本机当前代码构建的,**不存在某个文件忘记上传**的问题。
- 机器人从机房服务器搬到家里的 NAS,用的是**住宅宽带 IP**,Spotify(spotdl)
  抓 YouTube 音频不再被当作机器人拦截 —— 这正是这次搬家的主要目的。

所以在你的场景里,Docker 不是"更复杂",而是**最安全、最省心**的方案。

---

## 二、准备清单

- [ ] 一台绿联 NAS,UGOS Pro 系统,在「应用中心」装好 **Docker** 应用
      (这个应用是绿联打包的,安装它不会动主机系统)。
- [ ] 能用 **SSH 以 root 登录** NAS(UGOS「控制面板」里开启 SSH/终端机)。
- [ ] **Mumble 服务器信息**:host、端口(默认 `64738`)、密码(若有)、频道名。
- [ ] **Spotify API 凭据**(client_id / client_secret),获取方法见 `DEPLOY.md`
      的「获取 Spotify API 凭据」小节。
- [ ] 你自己的 **Mumble 用户名**(会设为机器人管理员)。

> 绿联 DXP 系列基本是 x86_64 架构。Docker 会自动选用匹配架构的基础镜像,
> 本方案的 `Dockerfile` 不分架构,ARM / x86 都能直接构建。

---

## 三、第 1 步:把代码放到 NAS

在你的 Windows 机器上(PowerShell),把整个项目目录拷到 NAS:

```powershell
scp -r "E:\Claude\Mumble-Music" root@<NAS的IP>:/volume1/docker/
```

(也可以用绿联的文件管理 / SMB 共享,把整个文件夹直接拖进 NAS 的某个共享目录。)

之后所有命令都在 **SSH 登录 NAS 后、在项目目录里**执行:

```bash
cd /volume1/docker/Mumble-Music
```

> 路径按你的实际存放位置调整。下文统一用 `<项目目录>` 指代它。

---

## 四、第 2 步:准备配置文件

**重要:必须在启动容器之前先建好 `configuration.ini` 这个文件。**
否则 Docker 挂载时会把它当成一个**目录**创建,机器人就读不到配置。

```bash
cd <项目目录>
cp configuration.example.ini configuration.ini
nano configuration.ini
```

最小配置(其余选项会自动从 `configuration.default.ini` 取默认值):

```ini
[server]
host = <你的 Mumble 服务器地址>
port = 64738
; 服务器需要密码就取消下一行分号并填写,不需要就保持注释
;password = <连接密码>
; 机器人要加入的频道名;多级频道用斜杠,如 Games/Squad
channel = <频道名>

[bot]
username = MusicBot
; 管理员 Mumble 用户名,多个用分号隔开
admin = <你的 Mumble 用户名>

[spotify]
client_id = <你的 Spotify Client ID>
client_secret = <你的 Spotify Client Secret>
```

保存退出(nano 里按 `Ctrl+O`、回车、`Ctrl+X`)。

---

## 五、第 3 步:构建并启动

```bash
cd <项目目录>
docker compose build      # 首次构建,需联网,约几分钟
docker compose up -d      # 后台启动
docker compose logs -f    # 查看实时日志
```

日志里应能看到连接成功、加入频道。然后用你自己的 Mumble 客户端进同一个频道,
发文字命令测试:`!help`、`!bili <B站链接>`、`!spotify <歌名>`、`!queue`、`!skip`。

(`Ctrl+C` 只是退出"看日志",不会停掉容器。)

> **想用图形界面也行**:UGOS Pro 的 Docker 应用 →「项目 / Compose」→ 新建项目 →
> 指向 `<项目目录>` 里的 `docker-compose.yml` 导入并启动即可。
> 但首次建议先用上面的命令行,报错信息更直观。

---

## 六、第 4 步:每天自动更新 yt-dlp / spotdl

B 站 / YouTube 经常改版,yt-dlp 必须保持最新。容器已设 `BAM_UPDATE_ON_START=1`
—— **每次启动都会自动升级 yt-dlp 和 spotdl**。只要让容器每天重启一次即可。

SSH 里执行 `crontab -e`,加一行(每天凌晨 5 点重启):

```cron
0 5 * * * docker restart mumble-music
```

或用 UGOS「控制面板 → 计划任务」新建一个每天执行 `docker restart mumble-music`
的定时任务。

部署到此结束。机器人已在后台常驻,崩溃自动重启,并每天自动更新。

---

## 七、日常运维

| 操作 | 命令 |
|---|---|
| 看实时日志 | `docker compose logs -f` |
| 重启 | `docker compose restart` |
| 停止 | `docker compose stop` |
| 再启动 | `docker compose up -d` |
| 改完 `configuration.ini` 生效 | `docker compose restart` |
| 改了机器人代码后 | `docker compose up -d --build` |
| 进容器内部排查 | `docker exec -it mumble-music bash` |

**数据持久化**:`configuration.ini`、`data/`(状态数据库)、`music_folder/`
(本地音乐)、`spotdl_cache/`(Spotify 下载缓存)都挂载在 `<项目目录>` 下,
容器删除重建都不会丢。

---

## 八、故障排查

**`configuration.ini` 变成了一个目录**
- 启动容器前没先创建该文件。`docker compose down`,删掉那个错误的目录,
  按第四节正确创建文件后重新 `up`。

**容器起不来,日志报 `exec format error` 或 bad interpreter**
- `entrypoint.sh` 换行符问题。`Dockerfile` 已用 `sed` 自动修复,正常不会遇到;
  若仍出现,`docker compose build --no-cache` 重新构建。

**机器人连不上 Mumble 服务器**
- 核对 `[server]` 的 host / port / password。容器默认 bridge 网络,
  主动连出没有限制,一般无需额外网络配置。

**`!bili` 报下载失败**
- 多半是 B 站改版导致 yt-dlp 暂时失效。`docker restart mumble-music`
  会触发自动升级。大会员 / 番剧内容需要 cookies,见下。

**`!spotify` 失败**
- 检查 `[spotify]` 的 client_id / client_secret 是否填对。
- 住宅 IP 下 spotdl 抓 YouTube 一般**不需要 cookies** —— 这正是搬到家里 NAS
  的意义。若极少数情况下仍被 YouTube 要求登录验证,再来加 cookies 支持。

**B 站大会员 / 番剧需要 cookies**
- 用浏览器扩展导出 `bilibili.com` 的 Netscape 格式 cookies 文件,放进
  `<项目目录>/cookies/`;在 `docker-compose.yml` 里取消 `./cookies` 那行挂载的注释;
  在 `configuration.ini` 的 `[youtube_dl]` 段加
  `cookie_file = /botamusique/cookies/bili.txt`;最后 `docker compose up -d`。

**机器人在频道里但没声音 / 想看详细日志**
- 在 `configuration.ini` 的 `[debug]` 段加 `ffmpeg = True` 和 `youtube_dl = True`,
  `docker compose restart` 后看 `docker compose logs -f`。

---

## 九、命令速查

机器人的聊天命令(`!bili`、`!spotify`、`!queue`、`!skip`、`!volume`、`!duck` 等)
与 `DEPLOY.md` 的「命令速查」一节完全相同,直接参见 `DEPLOY.md` 第八节。
