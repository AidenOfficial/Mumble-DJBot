# PROGRESS

无人值守推进记录。状态:TODO / DOING / DONE / BLOCKED。

## 环境备注(本轮执行环境)

- 本轮运行在远程 Linux 容器(Claude Code 云端),仓库为全新 clone,**没有本地 `configuration.ini`**(gitignore 状态,含服务器密码,不在仓库内)。
- 因此**无法连真实 Mumble 服务器做端到端验证**(smoke_bot.ini / Team B 频道那套只能在用户本机跑)。本环境验证手段:pytest 全量、pyflakes、`scripts/smoke_test.py --only imports`、以及可行时的 bili 元数据探测。涉及播放行为的改动会在 PROGRESS 中标注"待本机端到端复验"。
- 容器 Python 3.11,pymumble 2.0 要求 >=3.12 装不上;单测与 smoke imports 均不依赖 pymumble,可正常验证。venv 位于 `~/venv`(容器内,不入库)。

## 目标状态

### A1. 下载音频自动清理(7 天周期) — DONE

- 实现:`bot/cleanup.py` 的 `CacheCleaner` daemon 线程,bot/startup.py 在 watchdog 之前启动(`var.cleaner`)。
- 上次清理时间持久化到 settings 数据库(`cleanup/last_run`);启动时到期立即清理,否则按剩余时间调度(每小时醒一次重估,容忍时钟回拨);clock 可注入。
- 清理范围:`tmp_folder` 顶层 32 位 hex 文件名(URL/Bilibili md5 id)+ music_db 中 url 记录指向的 tmp 内文件;`[spotify] download_folder` 内的普通文件。
- 安全:不删 playlist 引用文件、downloading 条目、`.part/.ytdl/...` 半成品、keep_days 窗口内文件、1 小时内新文件(MIN_AGE 兜底);music_folder 一律不碰(含 tmp_folder 被误配成 music_folder 的场景);tmp_folder 默认 /tmp/ 共享目录,靠文件名白名单(hex id / db path)避免误删他人文件;req_* 子目录留给 spotify 已有 prune 逻辑。
- DB 同步:url 记录 ready 重置为 validated(下次点播重新下载,内存 cache 同步);spotify file 缓存记录直接删除;本地曲库相对路径 file 记录不受影响(有单测)。
- 配置:`cleanup_interval_days`(默认 7,0=关)、`cleanup_keep_days`(默认 7),已写入 configuration.default.ini,注释在 configuration.example.ini。
- 验证:16 个新单测(tests/test_cleanup.py,注入 clock + 临时目录 + 真 sqlite),全量 36 passed;pyflakes 新文件 0 告警;smoke imports + bili 元数据探测通过。

### A2. 大文件流式播放(边下边播) — DONE(代码+单测;真机端到端待本机复验)

- Spike 结论(容器内用静态 ffmpeg 7.0.2 实测"增长中文件"):webm/opus、mp3、fragmented mp4 都能直接解码增长中文件,到 EOF 以 rc=0 干净退出;`-ss playhead` 重启可无缝续播(60s 源实测 16s 断点续播 +44s 补齐);moov 在尾部的普通 mp4 打不开(rc=183)→ 必须降级等完整下载。
- 实现方案(复用现有 loop 结构,不引入管道方案):
  - `media/url.py`:开启流式时 yt-dlp 用 `nopart`(文件在最终路径原地增长);写 `<path>.incomplete` 标记,下载成功删除、失败随 glob 清除;validate 发现"文件存在但标记还在"(崩溃残留的截断文件)就丢弃重下。新增 `playable_from(playhead, buffer_secs)`:按 progress×duration 估算已下秒数,水位=playhead+buffer(文件尾部自动放宽)。
  - `bot/player.py`:`_stream_playable`(配置开关+时长门槛+水位)在 wait_for_ready 分支触发提前 launch_music(playhead);`_stream_rewait` 处理 ffmpeg 退出:rc=0 且产出过 PCM 且未到曲尾 → 追上下载了,回到等待态攒够 buffer 再从 playhead 续播;rc≠0 或零产出 → 标记 item.no_stream,等完整下载(moov 尾部 mp4 自动降级,不会误删条目);-9/-15(skip/pause)不拦截。resume() 同步支持流式条目。
  - 清理联动:`.incomplete` 加入 A1 的 PARTIAL_SUFFIXES。
- 配置(默认关):`stream_while_downloading=False`、`stream_buffer_seconds=30`、`stream_min_duration=300`,注释在 example ini。
- 验证:19 个新单测(watermark/rewait/降级/skip 路径),全量 55 passed;pyflakes 无新增;smoke imports 通过。**待本机复验**:开启配置后用长 B 站视频实测(harness 断言音频先于下载完成到达),及 Windows 上 nopart 行为。

### A3. 队列预取(prefetch) — DONE

- `media/playlist.py` 新增 `upcoming_items(count)`(Base 按索引窗口;Oneshot 从 index 1 起;Repeat 环绕不重复;Random/Autoplay 继承),只读不动播放状态。
- `bot/player.py` `async_download_next()` 末尾调 `_prefetch_upcoming()`:窗口 = 配置 `prefetch_count`(默认 2),跳过第一个(loop 自身已下),并发上限 `PREFETCH_MAX_CONCURRENT=2`(复用 `_active_downloads` 去重);验证失败走既有错误路径(消息+移除+free)。加歌/切歌都会触发,窗口自然滑动。
- 配置:`prefetch_count = 2`(1=旧行为),注释在 example ini。
- 验证:10 个新单测(窗口语义 × 各播放模式、并发上限、ready 跳过、滑动),全量 65 passed;pyflakes 无新增;smoke imports 通过。

### B0. Web 技术方案 — TODO

### B1. 设计与审美 — TODO

### B2. Now Playing 主界面 — TODO

### B3. 统一搜索(YouTube + Bilibili) — TODO

### B4. 统计页 — TODO

### B5. Dashboard 完整功能 — TODO

### B6. 发布准备文档 — TODO

## DECISIONS 待决区

### ⚠️ push 被 403 拒绝(需要用户处理)

- 现象:`git push` 与 GitHub API 写操作均返回 403(`Resource not accessible by integration`);读操作正常。本会话的 GitHub 凭据对 AidenOfficial/Mumble-DJBot 只有读权限。
- 影响:所有提交暂存在会话容器本地(容器是临时的,**会话闲置回收后本地提交会丢失**)。
- 备选:1) 在 Claude Code 会话/GitHub App 设置里给该仓库授予写权限(推荐);2) 用户本地手动拉取(无法直接从容器拉,需我把 patch 贴出来)。
- 我会每轮重试 push,权限修复后会自动推上去并开 draft PR。

## 本机待复验清单(用户回来后)

- (待补充)
