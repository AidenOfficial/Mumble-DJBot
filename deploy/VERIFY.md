# 功能验证(冒烟测试)

`scripts/smoke_test.py` 用来验证三条最容易出问题的链路是否在你的真实环境里正常:

1. **运行环境** —— spotdl / Deno / yt-dlp / ffmpeg 是否齐全;
2. **Bilibili** —— `BV 号 → av 链接` 规范化、yt-dlp 取元数据不被 412、真实下载并能解码;
3. **Spotify** —— 关键词 → Spotify 解析 → 从 YouTube 下载 → 文件能解码。

脚本**不连接 Mumble 服务器**,只验证到"拿到可播放的音频文件"为止,所以可以在机器人
正常运行的同时单独跑,互不影响。它直接调用机器人自己的代码(`util.get_bilibili_url_from_input`、
`media.spotify.download_tracks`),因此通过即代表对应功能可用。

## 在绿联 NAS(Docker)上验证

先确保已按 `deploy/DOCKER.md` 建好 `configuration.ini`(尤其 `[spotify]` 的
`client_id` / `client_secret`),然后:

```bash
# 1. 构建镜像(与正式部署同一个镜像)
docker compose build

# 2. 先做不下载的快速检查(工具齐全性 + B 站/Spotify 解析)
docker compose run --rm --entrypoint venv/bin/python botamusique scripts/smoke_test.py

# 3. 做真实下载验证(会真的下一首 B 站视频和一首 Spotify 歌,确认整条链路)
docker compose run --rm --entrypoint venv/bin/python botamusique scripts/smoke_test.py --full
```

`--full` 首次运行较慢:spotdl 要拉起 Deno 解析 YouTube。全部通过时进程以 0 退出并显示
`全部通过`;有失败项会列出具体原因并以 1 退出。

## 常用参数

```bash
# 只测某一项
... scripts/smoke_test.py --only bili
... scripts/smoke_test.py --only spotify --full

# 换测试样本(默认样本可能随时间失效,可换当前有效的)
... scripts/smoke_test.py --full --spotify-query "邓紫棋 光年之外" --bilibili BV1xx411c7mu
```

## 本机已验证(开发机,2026-05-30)

- Bilibili 完整链路 **PASS**:`BV1xx411c7mu → av44724809`、取元数据不被 412、真实下载
  `av44724809.m4a` 并经 ffprobe 确认 `aac` 可解码。
- Spotify / Docker 需要装有 spotdl + Deno 的环境(即 NAS 容器)才能验证,用上面的命令在 NAS 上跑。

## 失败排查速查

| 现象 | 多半原因 |
|------|----------|
| `spotdl` FAIL:No module named spotdl | 镜像没装 spotdl(检查 `requirements.txt` 与构建日志) |
| `deno` FAIL:未安装 | 镜像没装 Deno;`!spotify` 会下载失败(Dockerfile 应已安装) |
| Spotify 真实下载返回空 | 凭据无效 / 被风控 / Deno 缺失 / 该曲在 YouTube 无匹配 |
| B 站元数据 FAIL(412 或 deleted) | yt-dlp 过旧(`BAM_UPDATE_ON_START=1` 启动时会升级),或该视频已删除/需登录 cookie——换个视频再试 |
| Spotify/B 站文件 ffprobe FAIL | 下到的文件损坏 / 编码异常,通常重试即可 |

> 提示:容器里启用 `BAM_UPDATE_ON_START=1`(compose 默认开启)会在每次启动时把
> yt-dlp / spotdl 升级到最新版,能解决大部分"突然下不动"的问题。
