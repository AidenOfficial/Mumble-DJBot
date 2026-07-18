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

### A2. 大文件流式播放(边下边播) — TODO

### A3. 队列预取(prefetch) — TODO

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
