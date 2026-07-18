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

### B0. Web 技术方案 — DONE(方案定稿如下)

**既有 API 清单(interface.py,769 行,全部走 `requires_auth`,auth_method 默认 none)**

| 路由 | 方法 | 功能 |
|---|---|---|
| `/` | GET | 旧版 index HTML(按语言选模板) |
| `/playlist` | GET | 队列切片 JSON(range_from/range_to,含 HTML 片段字段) |
| `/post` | POST | 万能命令端点:add_item_at_once/bottom/next、add_url、add_radio、delete_music(按 index)、play_music(跳转)、move_playhead、delete_item_from_library、add_tag、action=(random/one-shot/repeat/autoplay/rescan/stop/next/pause/resume/clear/volume_up/volume_down/volume_set_value);返回 status() |
| (无路由) | — | `status()`:ver/current_index/empty/play/mode/volume/playhead,仅作为 /post 响应 |
| `/library/info` | GET | dirs/tags/upload_enabled/delete_allowed/max_upload_file_size |
| `/library` | POST | action=query(分页)/add/delete/edit_tags |
| `/upload` | POST | 上传音频到 music_folder(mimetype 校验、路径穿越防护) |
| `/download` | GET | 单曲或按条件 zip 下载 |

**痛点**:`/post` 单端点复用 + 表单语义、playlist 响应里混 HTML 片段、无独立 status 轮询端点、无搜索/统计 API、jQuery+webpack4 前端难以演进。

**决策:保留 Flask 做 JSON API + 全新 SPA 前端(不渐进改造)**

- 前端:**Vite 8 + Vue 3.5(`<script setup>` + TS)+ Tailwind CSS 4**,新目录 `webui/`,产物输出 `webui/dist/`,由 Flask 静态托管。容器内已验证 node 22 + npm 可用,依赖锁 package-lock.json。
- 理由:Vue3 组合式 API 适合这种中等规模仪表盘;Tailwind 4 便于做 design token 体系(B1);Vite 构建快且产物自包含;不渐进改造是因为旧前端(jQuery+webpack4+babel7)升级成本高于重写,且旧 UI 在新 UI 完成前原样保留。
- 后端:新增 **`web_api.py` Flask Blueprint,挂 `/api/*`**,interface.py 注册之,复用 requires_auth;旧路由一概不动。新端点(纯 JSON,无 HTML 片段):
  - `GET /api/status`(轻量轮询:play/playhead/duration/volume/mode/当前曲目摘要+缩略图)
  - `GET /api/queue` + `POST /api/queue/...`(move/remove/top/clear,复用 /post 既有逻辑抽函数)
  - `POST /api/controls`(pause/resume/skip/mode/volume)
  - `GET /api/search?q=`(并行 ytsearchN + B 站公开搜索,B3)
  - `GET /api/stats/*`(B4,含 play_history 埋点)
  - 曲库/上传沿用旧端点(前端直接调 /library、/upload)
- 新 UI 挂载:开发期 Flask 挂 **`/app`**(旧 `/` 不动);B5 完成后 `/` 切新 UI、旧界面下线(一次 commit 可回滚)。
- Docker 构建链:Dockerfile 增加 node 构建 stage 产出 `webui/dist`(不执行部署,仅改 Dockerfile 内容)。
- 部署边界:仍只监听配置地址;鉴权继续依赖 Cloudflare Access(应用层 auth_method 保持现状)。

**实施顺序**:B2 后端 status API → webui 脚手架+B1 token 体系 → B2 Now Playing → B5 队列/控制 → B3 搜索 → B4 统计 → 切换 `/` + B6 文档。

### B1. 设计与审美 — DOING

- [x] 设计 token 体系(webui/src/style.css):surface/text/accent/feedback/radius/shadow 全部 CSS 自定义属性,浅色默认 + prefers-color-scheme 暗色 + `[data-theme]` 强制覆盖(App 头部有 auto/light/dark 三态切换,localStorage 记忆);`@theme inline` 桥接进 Tailwind 4 实用类。主色 #6d5ef2(暗色 #8577ff),中性色分 bg/surface/surface-2 三层。
- [x] 截图自查:桌面+移动 × 深浅主题四张,层次/间距/对比度 OK,无 console 错误。
- [ ] 后续页面沿用此体系,统计图表配色对齐(B4)。

### B2. Now Playing 主界面 — DOING

- [x] 后端:`web_api.py` Blueprint(`create_blueprint(requires_auth)` 注入鉴权,interface.py 注册,挂 `/api/*`):`GET /api/status`(轻量轮询:play/mode/volume/ducking/playhead/queue_length/current 摘要+server_time,不含缩略图)、`GET /api/thumbnail/<id>`(JPEG 字节,Cache-Control 1 天,前端按 id 换歌时取一次)。6 个新单测(Flask test client + fake bot/playlist/cache),全量 71 passed。
- [x] webui/ 脚手架:Vite 8 + Vue 3.5(`<script setup>` TS)+ Tailwind 4,base=/app/,dev proxy /api→8181,package-lock 锁定;node_modules 入 gitignore,**dist/ 随仓库提交**(无 node 的部署环境直接可用)。
- [x] Now Playing 雏形(NowPlaying.vue + useStatus.ts):封面(有 thumbnail 按 id 取一次不闪烁,无则渐变占位)、曲名/来源徽标、进度条(rAF 前端平滑推算 + 3s 轮询校准,超时钳到 duration)、队列摘要/暂停标记、错误提示。
- [x] Flask 挂载:`/app` 302 → `/app/`,`/app/<path>` 走 send_from_directory(requires_auth 保持),旧 `/` 不动。
- [ ] 播放控制按钮(播放/暂停/跳过/音量)待 B5 controls API 一起接。

### B3. 统一搜索(YouTube + Bilibili) — DONE

- 后端 `web_search.py`:`GET /api/search?q=`(≥2 字符,limit≤12)线程池并行查 yt-dlp `ytsearchN:`(extract_flat)与 B 站公开搜索 API(UA+Referer+cookie 种子请求防 412,复用 youtube_dl cookie_file 配置);单源失败降级为 `failed` 标记;解析器为纯函数可单测(em 标签剥离、MM:SS/HH:MM:SS 时长、protocol-relative 图片补 https)。**两源已在本容器真实调通**。
- `POST /api/search/add`:B 站结果走 util.get_bilibili_url_from_input 的 av/BV 规范化;拒绝非 http(s) URL;沿用 add_url 语义(len==2 时触发预下载)。
- 前端 SearchPage.vue:400ms 防抖 + stale 响应丢弃、加载态、来源徽标(YouTube 红/Bilibili 蓝)、降级提示、逐条 + Queue 按钮(pending/Queued ✓/Failed);头部导航 Now Playing | Search。
- 验证:12 个新单测(解析器 fixture、交错合并、降级、endpoint mock、add 规范化与垃圾输入 400),全量 100 passed;真实搜索冒烟;stub+Playwright 点击断言 POST 正确;截图两断点两主题过关。

### B4. 统计页 — TODO

### B5. Dashboard 完整功能 — DOING

- [x] 后端 `POST /api/controls`(pause/resume/skip/stop/clear/mode/volume,语义逐条镜像旧 /post,响应返回最新 status 供前端乐观更新);`GET /api/queue`(全队列摘要+is_current)、`POST /api/queue`(move/top/remove/play/clear;move 带 current_index 修正,remove 镜像旧 delete_music 语义)。17 个新单测(含 move 三种 index 位移、边界 400),全量 88 passed。
- [x] 前端:Controls.vue(播放/暂停、跳过、音量滑条 150ms 防抖、ducking 徽标、四模式切换 chips)+ QueueList.vue(缩略图/时长、当前曲高亮、▶ 插播 / ⤴ 置顶 / ✕ 删除、Clear all;按 status.version 变化自动刷新 + 10s 兜底轮询);useStatus 重构为模块级单例 store,控制操作直接 applyStatus 响应。
- [x] 截图自查(桌面暗色 + 移动浅色),harness 点击 Pause 断言 POST 真实到达。
- [ ] 拖拽排序(后端 move API 已备好,前端拖拽后置)。
- [ ] 曲库浏览/上传页(复用旧 /library、/upload)。

### B6. 发布准备文档 — TODO

## DECISIONS 待决区

### ⚠️ push 被 403 拒绝(需要用户处理)

- 现象:`git push` 与 GitHub API 写操作均返回 403(`Resource not accessible by integration`);读操作正常。本会话的 GitHub 凭据对 AidenOfficial/Mumble-DJBot 只有读权限。
- 影响:所有提交暂存在会话容器本地(容器是临时的,**会话闲置回收后本地提交会丢失**)。
- 备选:1) 在 Claude Code 会话/GitHub App 设置里给该仓库授予写权限(推荐);2) 用户本地手动拉取(无法直接从容器拉,需我把 patch 贴出来)。
- 我会每轮重试 push,权限修复后会自动推上去并开 draft PR。

## 本机待复验清单(用户回来后)

- (待补充)
