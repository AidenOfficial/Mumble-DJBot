# 播放核心与并发组 审计报告（组长 + Opus 工人合并版）

仓库 /home/user/Mumble-DJBot，分支 claude/quirky-fermi-6nou7s，只读审计（git status 全程干净）。行号均为当前代码实测。

复现脚本（断言"当前错误行为"的用例，通过 = 复现成功）：
- 组长：`audit/repro/playback/repro_playback_core.py`（6 用例；其中 3 条写成"期望正确行为"，失败 = 确认）
- 工人：`audit/repro/playback/repro_playback_audit.py`（19 用例，组长复跑 19 passed；直接驱动真实 `_loop_iteration/resume/pause/play`、真实 Playlist/CachedItemWrapper/web_api._queue_remove/URLItem.validate`）
- 运行：`cd <repo-root> && python -m pytest -q <文件>`

> 验收说明：工人报告 playback-worker.md 共 18 条（3H/7M/6L/2I），组长逐条核对行号、交错序列并复跑其测试，**全部成立，无一剔除**；重复项合并（PB-02≈PC-02、PB-03≈PC-01、PB-04≈PC-03、PB-16≈PC-05、PB-14 并入 PC-04、PB-17/18 并入 Info），其余以 PC-13 起编号并标注"来源 worker"。严重度裁决见各条。

## 一、总评

播放核心"单曲内"的健壮性比上游好很多（stderr 独立排水、ffmpeg 网络超时、输入端 seek、主循环/下载线程兜底、watchdog、自动保存），子进程生命周期无实质问题。真正的薄弱点集中在**"队列指针 + 等待态"这套隐式状态机**和**跨线程共享的条目状态（ready/downloading）无互斥**：

1. **等待态（`wait_for_ready=True`）只被动轮询 `is_ready()`，从不补发下载**（PC-13，High）。任何把 `current_index` 挪到一首"没人在下载"的歌上的路径——暂停时连按 skip 超出预取窗口、清空后重加同一 URL、下载线程校验失败删当前曲、校验线程把下载中文件删掉（PC-01）、random 模式切换置 -1——都让主循环**永久**停在 `Wait for the next item to be ready`，watchdog 不介入。
2. **`URLItem.validate()` 不知道自己正在下载**（PC-01，High）：`!repeat`/同 URL 再点/切模式都会让校验线程删掉边下边播中的文件并把状态改回 `validated`，随后落入第 1 条。
3. **"删除当前曲"后主循环还会再 `next()` 一次**（PC-02，High）：每次解码失败/下载失败都额外跳过（one-shot 下直接删除）紧随其后的那首；single 模式末曲失败变成每 0.1s 抛 IndexError 的死循环（PC-14）。
4. **`interrupt()` 只是一个标志位且与主循环无互斥**（PC-04/15/16/17）：等待态下 skip 无效、launch 窗口内 pause/clear 丢失、等待中删当前曲播错歌、resume/play 的状态发布顺序与主循环推进段竞争。

最该先修的 3 件事（修复面最小、消解面最大）：
① 等待分支 `else` 里"没人在下载就 `async_download(current)`"（player.py:797，一处修改消解 PC-13 整类，并让 PC-01 可自愈）；
② `BasePlaylist.remove_by_id` 倒序删除 + 新增 `remove_current()` 统一回退指针（消解 PC-02/03/14/15）；
③ `URLItem.validate()` 在 `downloading` 或 `ready=='preparing'` 时直接返回（PC-01）。

计数：**High 3 / Medium 8 / Low 8 / Info 3，共 22 条**（组长原 9+2，工人新增 11，合并 7）。

状态机草图：

```
thread=None, wait_for_ready=False  --playlist.next()--> start_download(cur); wait_for_ready=True; playhead=0
wait_for_ready=True  --cur.is_ready() / _stream_playable()--> launch_music(cur, playhead); wait_for_ready=False; thread=Popen
thread!=None  --read()==b'' or on_interrupting--> thread=None; rc=wait()
    rc: _stream_rewait(rc) -> wait_for_ready=True (回到等待, 不前进)
        _livestream_retry(rc) -> wait_for_ready=True, playhead=0 (重连)
        rc∉{0,-9,-15} -> remove_by_id(cur) + free_and_delete   (PC-02)
        然后 if not wait_for_ready: playlist.next()
"等待就绪"分支只轮询 cur.is_ready()/is_failed()/_stream_playable()，从不重新发起下载 (PC-13)
interrupt(): if thread: on_interrupting=True; sleep(0.1)  —— thread 为 None 时什么都不做 (PC-04)
```

## 二、发现列表（按严重度）

### PC-13  High  Confirmed  【来源 worker PB-01】— 等待态从不补发下载：当前曲没人在下载时主循环永久卡住

**位置**：bot/player.py:772-798（等待分支只有 is_ready / is_failed / _stream_playable / else 等待）。触发方：commands/playback.py:109-114 与 web_api.py:123-131（暂停时 skip 只做 `next(); wait_for_ready=True`）、player.py:943-946（resume 早退）、player.py:402/410（Prepare 线程校验失败 `remove_by_id` 当前曲）、player.py:365-369（`_active_downloads` 按 id 去重）、media/cache.py:56-64（重加同 URL 时从库重建新对象）、playlist.py:198-208（randomize 置 -1）。

**问题陈述**：`wait_for_ready=True` 时主循环假定"当前曲已经有人在下载"，只轮询；凡是把 `current_index` 换到一首未就绪且不在下载中的条目、而又没调用 `start_download()` 的路径，都会让播放永久停住。

**证据**（player.py:775-798）：
```python
current = var.playlist.current_item()
if current:
    if current.is_ready(): ... launch
    elif current.is_failed(): ...
    elif self._stream_playable(current): ...
    else:
        self._loop_status = 'Wait for the next item to be ready'   # 永不补发下载
```
已复现的触发路径：
- (a) **暂停时连按 skip 超出预取窗口**（`test_F4_skip_while_paused_then_resume_waits_forever`）：预取只覆盖后 `prefetch_count=2` 首，第 3 次 skip 落到的歌没人下载；`resume()` 因 `pause_at_id` 不匹配直接 return；1000 轮后 `launched==[] and downloads==[]`，状态停在等待。
- (b) **清空/删除后重加同一 URL，旧下载仍在跑**（`test_F6_readd_during_download_never_becomes_ready`）：`!clear` → playlist.py:216 `free_all()` 逐出旧对象 I1（下载线程仍持有）→ 重加时 cache.py:56 `fetch()` 重建新对象 I2（`ready='validated'`）→ 主线程 `start_download(W2)` 被 player.py:366 按 id 去重 → Prepare 线程完成把 **I1** 置 yes → I2 永远 `is_ready()` False。
- (c) **下载线程校验失败删掉当前曲**：player.py:402 `remove_by_id(current)` 后指针指向下一首 B（PC-02 同一机制）；B 若不在预取窗口内（例如连续两首校验失败，或 YouTube 播放列表条目——入队时不校验，见 player.py:323-325）则永久等待。组长核对：命令/Web 入口在队列长度为 2 时和导入时会调 `async_download_next`，所以通常前 1-2 首已在下载，(c) 需要"超出预取窗口"条件，置信度 Likely。
- (d) PC-01 的结局也落到这里。

**影响**：任何用户（聊天或 Web）可触发；播放无声停住，Web 显示"等待准备"，看门狗不介入（主循环活着）；`!skip` 无法自救（PC-04），需 `!play N`。

**修复建议**：等待分支 `else` 里补发：
```python
else:
    try:
        item = current.item()
        if not getattr(item, 'downloading', False):
            with self._download_lock:
                running = current.id in self._active_downloads
            if not running:
                self.async_download(current)   # 幂等，已有去重
    except Exception: pass
    self._loop_status = 'Wait for the next item to be ready'
```
对 (b) 还需 `_download` 结束时若 `var.cache.get(id) is not item` 则把新对象同步置 ready（或 `_active_downloads` 存对象标识）。

---

### PC-01  High  Confirmed  【合并 worker PB-03】— 下载中的条目被二次 validate() 时文件被删，播放循环永久卡住

**位置**：media/url.py:98-116（validate 不看 `downloading`）、242-247（_discard_incomplete_download）、87-96（is_ready 回落 'validated'）。触发入口：media/playlist.py:66-100（append/insert/extend → async_validate）、58-62（from_list 调用被覆盖的 extend → 全队列重新校验）、commands/playback.py:192-198（`!repeat` 把当前 wrapper 再 insert）、commands/sources.py:127-128 / web_api.py:305（同 URL 再加一次，`get_cached_wrapper_from_scrap` 返回同一 item 对象）、commands/playback.py:215 / web_api.py:103-108（`!mode`/Web 切模式）。

**问题陈述**：默认 `stream_while_downloading=True`（configuration.default.ini:71）时 yt-dlp 用 `nopart` 直接写最终路径并放 `.incomplete` 标记；`validate()` 对 `ready=='preparing'` 的条目看到"文件存在 + 标记存在"就当成崩溃残留，`glob(path+"*")` 全删，并把 `ready` 覆盖为 'validated'。

**证据**（media/url.py:101-116, 139）：
```python
if self.ready in ['yes', 'validated']:
    return True
if os.path.exists(self.path):
    if os.path.exists(self._incomplete_marker_path()):
        self._discard_incomplete_download()      # 删掉正在写的文件 + 标记 + 封面
    else:
        self.ready = "yes"; return True
...
self.ready = "validated"                          # 'preparing' 被覆盖
```
复现：组长 `ValidateDiscardsInProgressDownload::test_second_validate_deletes_growing_file`、工人 `test_F8_validate_mid_download_deletes_growing_file`（真实 URLItem）；组长 `test_mode_switch_requeues_every_item_for_validation` 证明切模式把全部条目重新塞进 `pending_items`。

交错（`!repeat` 为例）：
1. 主循环 `start_download(A)` → Prepare 线程 `_download()`：`ready='preparing'`, `downloading=True`，创建 `.incomplete`，yt-dlp 开始写 `path`；主循环可能已流式 launch（player.py:793）。
2. 命令线程 `!repeat` → `insert()` → `pending_items.append` → 起 Validating 线程。
3. Validating 线程 `A.validate()`：不早退 → **删文件** → `_get_info_from_url()`（网络）→ `ready='validated'`、`stage='pending'`（覆盖 'downloading'）。
4. 主循环等待分支：`is_ready()` False；`playable_from()` 因 `os.path.exists(path)` False → False → 等待。正在播的 ffmpeg 读已 unlink 的 inode 直到追上写入 → `_stream_rewait` → 等待。
5. Prepare 线程完成：`ready='yes'`（写入的是已 unlink 的 inode）→ 主循环 `is_ready()` 文件不在 → 回落 'validated' → False；`is_failed()` False → 落入 PC-13 永久等待；若之后有人 `!play N` 重新 prepare 则整首重复下载。

非流式配置（无 marker）：`validate()` 会再跑一次 `_get_info_from_url`（网络数秒），其 `ready='validated'` 若写入晚于下载线程的 `ready='yes'` 同样降级 → PC-13；此外覆盖 `stage/_info` 让 Web 进度显示错。

**影响**：默认配置 + 极常见操作（`!repeat`、重复点同一链接、切模式）→ bot 静默停播；长视频（流式播放的目标场景）最易中招。

**修复建议**：url.py:101 改为 `if self.ready in ('yes', 'validated', 'preparing') or self.downloading: return True`（在 `validating_lock` 内）；`_check_valid` 对已在队列中的同一对象可跳过；配合 PC-13 的补发让残留状态可自愈。

---

### PC-02  High  Confirmed  【合并 worker PB-02】— 删除"当前曲"后主循环再 `next()`：紧随其后的一首被跳过（one-shot/autoplay 下被直接删除）

**位置**：bot/player.py:740-755（解码失败）、784-786（`is_failed` 分支）；根因 media/playlist.py:142-143（`remove()` 只在 `current_index > index` 时回退）、331-343（Oneshot.next 先删 `self[current_index]`）。

**证据**（player.py:749-755）：
```python
var.playlist.remove_by_id(current.id)   # 指针此时已指向 B
var.cache.free_and_delete(current.id)
...
if not self.wait_for_ready:
    if var.playlist.next():               # 再 +1 → C（one-shot：删除 B）
```
复现：组长 `FfmpegFailureSkipsFollowingItem::*`（repeat 期望 g1 得 g2；one-shot 队列只剩 ['g2']）；工人 `test_F2_oneshot_decode_failure_drops_following_song`、`test_F2_repeat_decode_failure_skips_following_song`、**`test_F2_is_failed_branch_also_skips`**（下载失败分支同样）。对照 commands/playback.py:140-145 的 `cmd_remove` 删当前项时显式 `current_index -= 1`，是正确做法。

**影响**：任何 ffmpeg 非零退出（坏缓存、不支持编码、cleanup 删掉文件）**或任何下载失败（yt-dlp 失效、视频不可用、地区限制——线上最常见的失败类型）** 都会连带吞掉下一首；one-shot（默认模式）是不可恢复的删除。

**严重度裁决**：组长原定 Medium（只考虑了解码失败），工人补充的 `is_failed` 分支把触发面扩大到所有下载失败，且默认模式下后果是无声删除用户点的歌，**采纳 High**。

**修复建议**：新增 `playlist.remove_current()`：持锁删除后 `current_index -= 1`（one-shot/autoplay 变 -1 让 `next()` 不再删）；player.py:749/785 改用它。注意 web_api.py:162-179 与 playback.py:128-153 已手动 `-= 1`，统一到同一 helper 避免双重回退。

---

### PC-03  Medium  Confirmed  【合并 worker PB-04】— `remove_by_id` 有重复条目时删错歌，并留下指向已释放缓存的"孤儿" wrapper，孤儿再级联杀掉刚开播的歌

**位置**：media/playlist.py:155-165；级联 player.py:322-335（`async_download_next` 只捕获 ValidationFailedError）、749-750（`free_and_delete`）。

**证据**：
```python
for index, wrapper in enumerate(self):          # 在 playlist_lock 之外
    if wrapper.id == id: to_be_removed.append(index)
for index in to_be_removed:
    self.remove(index)                          # 升序删除，后续索引已左移
```
复现：组长 `RemoveByIdWithDuplicates`（[A,B,A,C] 删 A 得 ['B','A']）；工人 `test_F1_remove_by_id_with_duplicates_removes_wrong_items`（`!repeat 3` 后 [A,A,A,A,B,C]，A 失败 → ['A','A','C']，**B 被删**，残留 A 的 `item()` 抛 ItemNotCachedError）；级联 `test_F1b_orphan_next_item_aborts_fresh_launch`：孤儿成为"下一首"时 player.py:783 `async_download_next()` → 328 `next.is_ready()` 抛 ItemNotCachedError → 冒到 player.py:592 兜底 → **杀掉刚开始播放的那首并跳过**。

**影响**：重复条目 + 任一失败路径（校验失败 player.py:334/358/402、ffmpeg 失败 749、曲库删除 commands/library.py:278/300）→ 误删他人点的歌、坏条目留下反复触发、刚开播的歌被杀。

**修复建议**：`for index in reversed(to_be_removed)`，收集与删除放进同一 `with self.playlist_lock`；`async_download_next` 的 except 增加 `ItemNotCachedError`（移除该条目）。

---

### PC-04  Medium  Likely  【并入 worker PB-14】— `interrupt()` 在 ffmpeg 未运行时是空操作、运行时是协作式：等待期 skip 无效、launch 窗口内 pause/stop/clear 丢失、网络流卡住时延迟 30s+

**位置**：bot/player.py:915-922（interrupt）、883-897（play）、899-913（clear/stop）、924-932（pause）、670（阻塞读）；commands/playback.py:109-114 与 web_api.py:126-131（skip）；主循环 704-798。

**证据**：
```python
def interrupt(self):
    if self.thread:                 # thread 为 None → 什么也不做
        self.on_interrupting = True
        time.sleep(0.1)             # 不 kill，只置位
```
(a) 等待下载：`wait_for_ready=True, thread=None`，`cmd_skip` 未暂停分支只调 `interrupt()` → 无效；长视频默认 4 次尝试 × yt-dlp `retries:15` × `socket_timeout:20` 可能等很多分钟，用户无法跳过（PC-13/PC-01 卡死无法自救的原因）。
(b) launch 窗口交错（命令线程 vs 主循环）：
1. 主循环 line 704 判定通过 → 进入 775-793 `launch_music`，`self.thread` 仍为 None（Popen 在 292 才创建）；`launch_music` 内含同步网络（直播 `uri()`、电台 `get_radio_title`，见 PC-05）。
2. 命令线程 `pause()` → `interrupt()` 空操作 → `is_pause=True`。
3. 主循环 Popen 完成；下一轮 `if self.thread:` 读取并 `add_sound`；`not is_pause and not raw_music` 为 False 不处理。**暂停状态下持续出声**直到曲终；曲终后 thread 残留，每 0.1s 空读。
同窗口 `clear()`：队列已清但 ffmpeg 把 `current` 播完；`stop()` 同 pause；skip 丢失。窗口：本地文件毫秒级，电台/直播秒级。
(c)【worker PB-14】ffmpeg 运行但主循环阻塞在 `stdout.read()`（电台/直播 `-rw_timeout 30000000` + `-reconnect_delay_max 10`）时，skip/pause/stop 要等读返回才被处理，最多延迟 ~30s+；超时退出后 rc≠0 还可能走 `_livestream_retry` 重连，把用户的 skip 吞掉（`_livestream_retry` 不看 `on_interrupting`）。

**影响**：用户/自动暂停（`users_changed` 线程 core.py:453-480）偶发失效；UI 与实际不一致；等待期无法跳过；网络流控制延迟。

**修复建议**：`interrupt()` 改为设置粘性标志 `_interrupt_pending=True` 并在 `self.thread` 存在时直接 `kill()`（读立即 EOF；`on_interrupting` 只用于淡出），主循环 `launch_music` 返回后立即检查该标志；skip 在 `thread is None` 时直接 `playlist.next()` + `start_download` + `wait_for_ready=True`。

---

### PC-05  Medium  Likely  【合并 worker PB-16】— 主循环（音频线程）里做同步网络 I/O：直播 `uri()` 与电台标题

**位置**：bot/player.py:210（`uri()`）、239（announce → `format_current_playing`）；media/livestream.py:381-396（每次启动/重连 `extract_info`，`_base_ydl_opts` 未设 `socket_timeout`）；media/radio.py:66-90（`requests.get(stream=True, timeout=10)` 读 icy 元数据，响应从不 close）。

**证据**：`launch_music` 在 `_loop_iteration` 内被调用；`LiveStreamItem.uri()` 每次（含 `_livestream_retry` 的每次重连，player.py:26-65）都跑一遍 yt-dlp 提取（常 2-10s）；`uri()` 抛 `DownloadError`（未捕获）走 loop() 兜底跳过整首直播。`RadioItem.format_song_string` 里 `get_radio_title` 连接电台读 `icy-metaint` 字节数的数据再读元数据。

**影响**：每次电台/直播开播与每次直播重连，音频线程停摆数秒（发送缓冲只维持 ~0.5s → 必然断音，`last_loop_at` 不更新）；网络极差时超过 `watchdog_timeout=120` 整进程重启。直播每 >10s 掉一次会无限重连（player.py:51-52）→ 反复断音。

**修复建议**：在等待分支由后台线程预解析直播直链/电台标题并缓存到 item（直链带过期时间），主循环只在解析完成后 launch；`_base_ydl_opts` 加 `socket_timeout`；`get_radio_title` 用 `with requests.get(...) as r:` 并移出 launch 路径。

---

### PC-14  Medium  Confirmed  【来源 worker PB-05】— single 模式下最后一首失败 → 每 0.1s 一次 IndexError 的死循环

**位置**：media/playlist.py:432-444（`SingleLoopPlaylist.next` 不校验 `current_index < len`）+ playlist.py:142（删当前曲不回退）+ player.py:749/785。

**证据**：`[A,B]` 当前为 B（index 1），B 失败 → 删除后 `len==1, current_index==1` → `next()` 执行 `self[1]` → IndexError → loop() 兜底置 `wait_for_ready=False` → 下一轮再 `next()` → 再抛。复现 `test_F3_single_mode_failure_of_last_item_wedges_loop`（50 轮 ≥49 次 IndexError，`launched==[]`）。组长核对：`BasePlaylist.current_item()` 同样 `self[1]` 抛出，Web `/api/status` 随之 500。

**影响**：播放停止 + 每秒 10 条 traceback（Docker stdout 日志无上限）；需手动 `!play N` 或加歌。

**修复建议**：PC-02 的统一回退即可修复；另在 `SingleLoopPlaylist.next` 加 `if self.current_index >= len(self): self.current_index = 0`。

---

### PC-15  Medium  Confirmed  【来源 worker PB-06】— 等待中删除当前曲（Web/聊天）→ 重播上一首，或（repeat/random）播放队尾那首

**位置**：web_api.py:162-179、commands/playback.py:128-153（删当前曲后先 `current_index -= 1`，依赖 interrupt 后主循环 `next()`）；根因 player.py:915-922（interrupt 空操作，PC-04）+ playlist.py:167-172（`current_index==-1` 时 `self[-1]`）。

**证据**：复现 `test_F4_remove_current_while_waiting_replays_previous`（调用真实 `web_api._queue_remove(1)`，`[A,B,C]` 正在等 B → 播放 A）、`test_F4_remove_first_item_while_waiting_plays_last_item_repeat_mode`（删 index 0 → `current_index=-1` → `self[-1]` → 播放 C）。组长核对交错：`remove(1)` 后指针仍 1（指向 C）→ `interrupt()` 空操作 → `current_index -= 1` → 0 → 等待分支 `current_item()`=A → ready → launch A。

**影响**：Web 队列页"删除正在缓冲的歌"即触发；播错歌。

**修复建议**：两个处理器在删除后若 `bot.thread is None` 改为 `bot.play(var.playlist.current_index)`（或 `wait_for_ready=True; start_download(current)`），不要依赖 interrupt 副作用；`BasePlaylist.current_item()` 对 `-1` 返回 False 而非 `self[-1]`。

---

### PC-16  Medium  Confirmed  【来源 worker PB-07】— `resume()` 的早退路径不设 `wait_for_ready`，主循环随即多跳一首

**位置**：bot/player.py:934-949。

**证据**（三个早退分支只改 `playhead`，`wait_for_ready` 保持暂停前的 False，下一轮走 player.py:754 `next()`）：
- `current_index == -1`（936-939）：自己 `next()` 一次，主循环再 `next()` 一次 → repeat 跳过第 1 首、one-shot 直接删掉第 1 首（`test_F5_resume_from_minus_one_*`）。组长核对：作者已在 web_users.py:546-548 注释中承认并**只在该入口绕过**；`!play`（playback.py:52）、`/api/controls resume`（web_api.py:205）、idle 自动恢复（channels.py:321）、`users_changed`（core.py:466）仍走这条路。现实触发：`!stop`（one-shot + clear_when_stop）或 `!oust` 后加歌再 `!play`。
- `pause_at_id` 不匹配（暂停后删当前曲，`_queue_remove` 暂停分支不调整指针 → 当前变 B）→ resume 后跳过 B（`test_F5b_remove_current_while_paused_then_resume_skips_next`）。
- 流式条目暂停在下载边缘、`_stream_playable` 为假 → 整首被跳过且 playhead 归零（`test_F5c_resume_of_partially_downloaded_stream_skips_song`）。组长原把"resume 遇不 ready 跳下一首"当上游行为剔除；工人指出流式场景是本 fork 新增且现实（长视频暂停后恢复），采纳。

**修复建议**：early-return 前统一 `self.wait_for_ready = True`（当前曲存在时）；去掉 -1 分支的 `next()` 改 `point_to(0)`；流式条目不可播时保持 `wait_for_ready=True` 并保留 playhead，让等待分支在数据够了后续播。

---

### PC-17  Medium  Likely  【来源 worker PB-08 + PB-09 合并】— 控制方法与主循环推进段无互斥，状态发布顺序错误导致跳歌/播错歌

**位置**：bot/player.py:883-897（play：先 `point_to`，`start_download` 之后才 `wait_for_ready=True`）、935 与 948（resume：先 `is_pause=False`，最后才 `wait_for_ready=True`）vs 主循环 704、754-755。

**证据**：
- play(index) 竞争自然播完（`test_F7_play_index_races_natural_end`）：用户线程 `interrupt()`（sleep 0.1）→ `point_to(5)` → `start_download`（可能 `send_channel_msg`）；主线程此时 ffmpeg EOF/interrupt 已处理，下一轮 `wait_for_ready` 仍 False → `next()` → `current_index=6`；用户线程才 `wait_for_ready=True`。结果播第 7 首。窗口 = `start_download` 耗时（目标未就绪时含线程创建 + 聊天消息发送，毫秒级）对 0.1s 主循环节拍，组长估算每次 `!play N` 命中率约 1-5%，判 Likely。
- resume 顺序（`test_F9_resume_publishes_is_pause_before_wait_for_ready`）：`is_pause=False` 发布后主循环 704 看到未暂停、`wait_for_ready` False → `next()`；resume 随后 id 不匹配 → `playhead=0; return`。暂停的那首被跳过且不续播。组长核对窗口：resume() 从 935 到 948 之间只有 `current_item()`、`is_ready()`（`os.path.exists` 可释放 GIL）等几条语句，约 0.1ms 对 100ms 节拍，命中率 ~0.1%；单独看只够 Low，并入本条作为同一根因的子项。`users_changed` 每个事件一个线程并发 resume 会提高命中率。

**修复建议**：`play()` 把 `wait_for_ready=True` 放到 `point_to` 之前；`resume()` 先算好 `playhead/wait_for_ready/pause_at_id`，**最后**才写 `is_pause=False`；根治是给 bot 一把 `_state_lock`，主循环的"推进"段（704-800）与所有控制方法共用。

---

### PC-18  Medium  Confirmed  【来源 worker PB-10】— 断线后主循环卡在"等缓冲"内层循环，不再检查 `is_alive()`，只能靠看门狗 `os._exit`

**位置**：bot/player.py:653-656；core.py:124-127（未传 `reconnect`）。组长核对工人下载的 pymumble 源码（scratchpad/ext/pymumble/src/mumble/mumble.py:507 `reconnect: bool = False`，628-633 仅 `reconnect=True` 时重连），结论成立。

**证据**：主循环把发送缓冲维持在 ~0.5s；断线后 pymumble 线程退出、缓冲不再被消费，`get_buffer_size() > 0.5` 永真，内层 while 只检查 `self.thread` 和 `self.exit`。复现 `test_F11_disconnect_wedges_buffer_wait_loop`（buffer 恒 0.51、`is_alive()` False，`_loop_iteration` 0.5s 后仍未返回）。

**影响**：每次断线要等 `watchdog_timeout`（默认 120s）才 `os._exit(1)` 由 Docker 拉起；`watchdog_timeout=0` 时进程永远挂着（heartbeat 停写）。`os._exit` 跳过退出时的 `playlist.save()`（只剩 15s 一次的 autosave）。进程内不存在重连，因此不存在旧线程跨连接泄漏/节拍错位问题（节拍由 `get_buffer_size()` 反馈驱动）。

**修复建议**：player.py:653 条件加 `and self.mumble.is_alive()`；loop 退出后若非 `self.exit` 主动 `var.playlist.save()` 再 `sys.exit(1)`。

---

### PC-06  Low  Confirmed（已测量） — 队列自动保存在音频线程逐条 commit

**位置**：bot/player.py:621-637；media/playlist.py:218-225；database.py:228-258（每个 set/remove_section 独立 connect+commit+close）。

**证据**：保存 key 含 `current_index`，每换一首歌后 ≤15s 必保存一次；一次 save = N+2 次 sqlite 连接与提交。实测本容器：50 条 78 ms，200 条 325 ms。SD 卡/网络盘上 fsync 高一个量级。

**影响**：大队列 + 慢盘 → 每首歌切换后 0.3-3s 主循环停顿，断音（发送缓冲 0.5s）。

**修复建议**：`save()` 单连接单事务 `executemany`；或放后台线程带快照。

---

### PC-07  Low  Confirmed — `_active_downloads` 在线程启动失败时泄漏，条目永久不可下载

**位置**：bot/player.py:365-382。先 `add(item.id)` 再 `th.start()`；`Thread.start()` 抛 RuntimeError（fd/线程耗尽）时 id 永不被 finally 回收，之后 `async_download(item)` 永远返回 None → PC-13 永久等待。复现 `ActiveDownloadsLeakOnThreadStartFailure`。修复：`try: th.start() except: discard(id); raise`。

---

### PC-08  Low  Confirmed（代码路径无歧义） — 从队列移除正在下载的歌会弹出假的"无法下载"消息，且下载不取消

**位置**：media/playlist.py:145-152（remove → `var.cache.free`）；media/cache.py:146-150, 164-169；bot/player.py:413-428。Prepare 线程 `item.prepare()` → `CachedItemWrapper.prepare()` → `self.item()` 抛 ItemNotCachedError → 通用 except → `send_channel_msg(unable_download, <32 位 id>)`；yt-dlp 继续跑完（带宽浪费）。修复：单独捕获 `ItemNotCachedError` 静默返回；长期给 URLItem 加取消标志由 progress_hook 终止 yt-dlp。

---

### PC-09  Low  Confirmed — 边下边播重启与直播重连都重复播报"正在播放"

**位置**：bot/player.py:220-239（quiet 只由 SponsorBlock 置位）、184-200、26-65；configuration.default.ini:33 `announce_current_music = True`。播报条件与 `start_from`/`_skip_history_once` 无关；直播每 >10s 掉一次无限重连，每次一条。修复：`announce = ... and start_from == 0 and not skip_history and not quiet`。

---

### PC-19  Low  Confirmed  【来源 worker PB-11】— one-shot：队列放完后 0.1s 内加入的歌会被直接删除

**位置**：media/playlist.py:331-346（删完最后一首返回 False 时 `current_index` 仍为 0）。交错：主线程 `next()` 删掉 A，列表空、`current_index` 留 0 → 用户线程 append B → 主线程下一轮 `next()`：`current_index != -1` → 删 `self[0]`=B。复现 `test_F10_oneshot_item_added_right_after_queue_end_is_dropped`。组长核对：其他线程若在窗口内调用 `OneshotPlaylist.current_item()` 会把指针修成 -1，所以窗口 ≈ 0.1s 且取决于 Web 轮询时机。修复：338-339 在 `len==0` 时同时 `current_index = -1`。

---

### PC-20  Low  Likely  【来源 worker PB-12】— 上一个 ffmpeg 的 stderr 排空线程会污染新曲的 `last_ffmpeg_err`

**位置**：bot/player.py:290-296 与 298-311。`launch_music` 清空共享 deque 后起新排空线程，被 kill 的旧进程的排空线程仍可能之后 append 并覆盖 `last_ffmpeg_err`；新曲失败时 747 打出的是上一首的错误。修复：每个进程用自己的 deque 作为参数传入。

---

### PC-21  Low  Likely  【来源 worker PB-13】— 失败归因用 `current_item()` 而不是本次 ffmpeg 实际播放的 `_playing_id`

**位置**：bot/player.py:740-750、164-170、31-41。ffmpeg 退出到检查 rc 之间（`wait(timeout=1)`、网络流读阻塞最长 30s）若用户换了歌，`remove_by_id + free_and_delete`（删库 + 删文件，cache.py:90-101）作用在用户新选的那首上。修复：三处先比较 `current.id == self._playing_id`。

---

### PC-22  Low  Confirmed  【来源 worker PB-15】— autoplay 模式曲库为空（或全部被 `don't autoplay` 排除）时每 0.1s 查一次库

**位置**：media/playlist.py:511-514 + player.py:754-771。`AutoPlaylist.next()` 空列表时 `refresh()`（每次新开 sqlite 连接 + `ORDER BY RANDOM()`），主循环空队列每轮都 `next()`。修复：refresh 为空时记录时间，间隔数十秒再试。

---

### PC-10  Info — 重启后被打断的那首歌被跳过（repeat/random）或删除（one-shot）

media/playlist.py:227-241、321-328、331-343；bot/player.py:754-755。上游同款，但本 fork 的 15s 自动保存让用户预期"重启续播"，实际总从下一首开始，one-shot 下当前歌被 `next()` 删除。修复：load 后置 `wait_for_ready=True` 直接从 current 开始。

### PC-11  Info  【合并 worker PB-17】— ffmpeg 子进程生命周期 / returncode 解释

stdout 主循环读、stderr 每进程一个排空线程、stdin `-nostdin`，长时间播放不会因管道填满阻塞。主循环自己 kill 的路径（695-696、119-122、597-601）都不 `wait()`，但排空线程持有 proc 到 stderr EOF，之后 `Popen.__del__` 入 `subprocess._active`，下次 `Popen()` 回收 → 最多 1 个短暂僵尸，无累积。因自杀路径从不 wait，`(-9,-15)` 判断只会在**外部**杀死（OOM killer）时命中，被当作"主动 kill"静默前进；Windows 上外部终止 rc=1 会被判为解码失败。进程退出（watchdog `os._exit`/正常）时 stdout 管道关闭 → ffmpeg SIGPIPE 退出，不遗留。

### PC-12  Info  【合并 worker PB-18】— 流式边界与 `.incomplete`

`playable_from`（url.py:249-261）对 duration 0 返回 False，`_stream_playable` 还要求 `>= stream_min_duration`，`_stream_rewait` 的 `duration-2` 不会遇到 0；尾部 `min(duration-1, playhead+buffer)` 放宽；`total_bytes_estimate` 偏大/VBR 偏差只会退化为 rc0 → rewait 或 `no_stream` 等整下，不会卡死。崩溃重启：库里存 `'validated'`（封面线程 url.py:419 `version += 1` 可能让 `'preparing'` 落库）；`'validated'` 时 `validate()` 在 101 早退**不检查 marker**，随后 `_download` 交给 yt-dlp——工人核对 yt-dlp 2026.08.19 `process_info` 在 nopart 下走续传分支，残缺文件被续传而非当成已下载；残余风险：重启后重新解析选到不同格式/码率，HTTP Range 续传拼出损坏文件（Needs-verification）。nopart 与 Windows 并发读写无阻断；"文件被 ffmpeg 打开时删除"在 url.py 都包了 `except OSError`，`cache.free_and_delete`（cache.py:96-97）未包但只在 ffmpeg 退出后调用。

## 三、剔除 / 降级记录

**组长自审剔除**：
- `_sb_segments` 无上限增长：每首几十字节，不报。
- SponsorBlock 片段边界：`merge_segments` 已排序/合并重叠/丢弃 <1s；超出时长按"到结尾"处理；`segment_at` 0.5s 容差避免重启后再次命中；未发现缺陷。
- `stdout.read(n)` 短读被当曲终：`BufferedReader.read(n)` 阻塞管道只有 EOF 才返回不足 n 字节，上游依赖正确。
- `playlist_lock`/`validating_thread_lock`/`_download_lock` 死锁：锁顺序只有 validating→playlist 一个方向，无环。
- `_prefetch_upcoming` 检查-再加入非原子：最多超上限 1 个，不报（工人同样剔除）。
- `async_validate` 的 `locked()` 检查窗口漏校验：轮到时 Prepare 线程会再 validate，无害。
- `launch_music` 的 assert TOCTOU：极小且被 loop() 兜底，不报。
- 校验线程覆盖 `stage/_info`（非流式）：并入 PC-01。

**对工人 18 条的裁决**（全部成立，无剔除）：
- PB-01/05/06/07/10/11/12/13/15 新增为 PC-13/14/15/16/18/19/20/21/22，严重度与工人一致。
- PB-02 并入 PC-02 并**上调为 High**（理由见该条）。
- PB-03 并入 PC-01；PB-04 并入 PC-03（增补孤儿级联）；PB-16 并入 PC-05（增补电台标题 HTTP）；PB-14 并入 PC-04 子项 (c)；PB-17/18 并入 PC-11/12。
- PB-08（resume 发布顺序）**降为 PC-17 的子项**：窗口约 0.1ms/100ms 节拍，单独不够 Medium；与 PB-09 同根因合并为一条 Medium。
- PB-01 路径 (c) 单独标 Likely：组长核对命令/Web/导入入口在队列长度为 2 时和导入后都会调 `async_download_next`，需"超出预取窗口"条件才命中。
- 组长原将"resume 遇不 ready 跳下一首"按上游行为剔除，工人证明流式场景（PC-16 第三子项）是本 fork 新增且现实，撤回剔除。

## 四、覆盖范围声明

**逐行读过（组长）**：bot/player.py 全部、bot/core.py、bot/startup.py、bot/channels.py、bot/cache_store.py、bot/cleanup.py（仅交叉验证保护规则）、media/item.py、media/file.py、media/url.py、media/url_from_playlist.py、media/livestream.py、media/radio.py、media/sponsorblock.py、media/playlist.py、media/cache.py；调用方 commands/playback.py 全文、commands/streaming.py:20-60、commands/sources.py:120-140、web_api.py:90-215、300-312、web_users.py:538-560、interface.py 相关 grep 行；database.py 的 SettingsDatabase；tests/test_player.py、test_streaming.py、test_long_video.py、test_prefetch.py、test_livestream.py、test_stability.py、test_sponsorblock.py 全文。
**工人额外核对**：pymumble upstream 源码（scratchpad/ext，`reconnect` 默认值与缓冲实现）、已安装 yt-dlp 2026.08.19 的 nopart 续传分支、web_users.py:539-560。

**未看**：media/spotify.py（不在本组范围）、interface.py 旧前端 /post 分支完整逻辑（与 web_api.py 同构，结论同样适用于 interface.py:465-482 与 545-551）、pymumble 运行时行为（未安装，只读了源码）。

**单测覆盖缺口（供测试组参考）**：仓库内没有任何测试驱动 `_loop_iteration`/`interrupt`/`pause`/`resume`/`play` 的状态转换（PC-02/04/13-17 全部路径无覆盖，工人脚本可直接改成回归测试）；`remove_by_id` 重复 id 无覆盖；`validate()` 与进行中下载的交互无覆盖；test_streaming.py 用 FakeURLItem 复制了 `playable_from` 逻辑，与真实实现只靠 3 个用例对齐，有漂移风险。
