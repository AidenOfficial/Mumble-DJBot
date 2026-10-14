# 播放核心与并发 —— 工人深挖报告(Opus)

范围:bot/player.py、bot/core.py、bot/startup.py、bot/channels.py、media/{url,livestream,radio,playlist,cache}.py,以及它们与控制入口(web_api.py `_skip/_queue_remove/_set_mode`、commands/playback.py)的交互。只读;仓库 `git status` 全程干净。

复现脚本:`audit/repro/playback/repro_playback_audit.py`(19 个用例,**每个用例断言的是"当前的错误行为"**,通过 = bug 复现成功)
```
cd <repo-root> && python -m pytest -q -p no:cacheprovider \
  audit/repro/playback/repro_playback_audit.py
# -> 19 passed
```
用例直接驱动真实的 `PlayerMixin._loop_iteration / resume / pause / play`、真实的 `media.playlist.*Playlist`、真实的 `CachedItemWrapper`、真实的 `web_api._queue_remove`、真实的 `media.url.URLItem.validate`;只把 ffmpeg(FakeProc)、pymumble(FakeMumble)、下载线程 和 `time.sleep` 换成了假的。

---

## 总评

播放核心的“单曲内”健壮性(stderr 排空、ffmpeg 网络超时、输入端 seek、主循环兜底 except、看门狗)已经比上游好很多;**问题集中在“队列指针 + 等待态”这套隐式状态机**:

1. **等待态(`wait_for_ready=True`)只会被动轮询 `is_ready()`,从不(重新)发起下载**。任何把 `current_index` 挪到一首"没人在下载"的歌上的路径(暂停时连按 skip、等待中删当前曲、下载线程校验失败删掉当前曲、清空后重加同一 URL、校验线程在下载中途把文件删掉)都会让主循环**永久**停在 `Wait for the next item to be ready`。(PB-01、PB-03)
2. **“删除当前曲”后主循环还会再 `next()` 一次**,于是每次解码失败/下载失败都会额外跳过(one-shot 模式下是直接删除)紧随其后的那首歌;single 模式下若失败的是最后一首,则变成每 0.1 s 抛一次 IndexError 的死循环。(PB-02、PB-05)
3. **`URLItem.validate()` 不知道自己正在下载**:同一 URL 被重复添加 / `!repeat` 时,校验线程会把边下边播中的文件和 `.incomplete` 一起删掉,并把状态改回 `validated`,随后落入第 1 条的永久等待。(PB-03)

最该先修的三件事:① 在等待分支里"没在下载就补发下载"(一处修改消解 PB-01 整类);② `BasePlaylist.remove_by_id` 倒序删除 + "删当前曲"统一回退指针(PB-02/04/05);③ `URLItem.validate()` 在 `downloading` 或 `ready=='preparing'` 时直接返回(PB-03)。

---

## 1. 线程清单

| 线程 | 创建位置 | daemon | 生命周期 / 备注 |
|---|---|---|---|
| MainThread(播放主循环) | startup.py:298 `var.bot.loop()` | – | `while not exit and mumble.is_alive()`;所有 ffmpeg 读、launch、`next()`、idle_tick 都在这里 |
| Mumble(pymumble 控制线程) | core.py:143 `self.mumble.start()` | **否** | `reconnect` 未传 → 默认 False(pymumble `mumble.py:507`),断线即退出;文本消息/用户事件回调都在此线程执行 → `message_received` → 所有聊天命令(`play/pause/skip/remove/mode…`)都在**这个线程**里改队列 |
| MumbleUDPThread / MumbleServerInfoThread | pymumble 内部 | 是 | – |
| sound_received 回调 | pymumble 音频线程 | – | `ducking_sound_received` 写 `on_ducking/ducking_release/_max_rms`(无锁,浮点/布尔写,良性) |
| WebThread(Flask/werkzeug,threaded) | startup.py:244 | 是(且在 excepthook 中被视为 fatal) | 每个请求一个线程,调用 `bot.play/pause/resume/interrupt/clear`、`_skip/_queue_remove/_set_mode`(重新赋值 `var.playlist`) |
| Prepare-<id> | player.py:371 `async_download` | 是 | 每个下载一个;`_download` → `item.validate()/prepare()`(yt-dlp 在进程内跑);可能在条目已被删后继续跑完 |
| Progress-<id> | player.py:377 | 是 | 每个下载一个,2 s 轮询,上限 7200 s |
| ffmpeg-stderr | player.py:293 | 是 | 每次 launch 一个,读到 stderr EOF 结束 |
| SponsorBlock-<id> | player.py:103 | 是 | 一次 HTTP |
| Thumb-<id> | url.py:423 | 是 | 下载封面,写 `item.thumbnail/version` |
| Validating | playlist.py:255 | 是 | 每次 append/insert/extend 若锁空闲则起一个;会 `remove_by_id` + `cache.free_and_delete` |
| users_changed | core.py:305 | 是 | **每个**用户换频道/离开事件一个线程,可能调用 `pause()/resume()/clear()` |
| follow Timer | channels.py:233 | 是 | 2.5 s 防抖后 `move_in()` |
| UpdateThread | core.py:219 | 是 | 一次 |
| CacheCleanupThread | cleanup.py:99 | 是 | 定时删文件(另组负责) |
| Watchdog | startup.py:295 | 是 | `last_loop_at` 超过 `watchdog_timeout`(默认 120 s)→ `os._exit(1)` |

ffmpeg 子进程:只有 `launch_music` 一处 `sp.Popen`(player.py:292)。yt-dlp 是进程内库调用(下载线程 / 主线程的 `LiveStreamItem.uri()`),不是子进程。spotdl 子进程不在本组范围。

## 2. 共享可变状态与锁覆盖

| 状态 | 写入方 | 是否同一把锁 |
|---|---|---|
| `var.playlist` 列表内容、`current_index` | 主循环 `next()`、聊天线程、Flask 线程、Validating 线程、Prepare 线程(`remove_by_id`) | `playlist_lock`(RLock)只覆盖**单个方法**;`remove_by_id` 的"收集下标"在锁外(playlist.py:156-165);调用方的复合操作(`remove(i)` → `interrupt()` → `current_index -= 1`,web_api.py:162-179 / playback.py:128-153;`next(); wait_for_ready=True`)全无锁;`_queue_move` 是唯一整段持锁的 |
| `var.playlist` 对象引用本身 | `_set_mode`/`cmd_mode` 直接重新赋值 | 无锁;主循环一个迭代内多次读 `var.playlist`,可能跨两个对象;旧对象上的 Validating 线程继续对旧对象 `remove_by_id` |
| `bot.wait_for_ready / is_pause / playhead / song_start_at / on_interrupting / thread / read_pcm_size` | 主循环 + 所有控制线程 | **完全无锁**;`interrupt()` 只是置位 `on_interrupting`,真正 kill 由主循环下一次读后完成 |
| `item.ready / downloading / progress / stage / no_stream` | Prepare 线程、Validating 线程、主循环(`no_stream`)、cleanup | `URLItem.validating_lock` 只保护 `validate()`;`prepare()/_download()` 不持锁,且 `validate()` 不检查 `downloading`(→ PB-03) |
| `_active_downloads` | `async_download`(加)、`_download` finally(删)、`_prefetch_upcoming`(读) | `_download_lock` ✔;但按 **id** 去重,而 item 对象可被替换(→ PB-01 触发路径 c);`_prefetch_upcoming` 检查长度后放锁再 `async_download`(TOCTOU,只会让并发上限超 1,影响小,不单列) |
| `var.cache`(dict) | 各线程 `free/free_all/free_and_delete/get_item` | 无锁(dict 单操作原子);问题在语义:free 掉仍被引用的 id(→ PB-04) |
| `last_ffmpeg_err / _ffmpeg_stderr_lines` | 每个 ffmpeg-stderr 线程 + launch_music | 无锁,旧线程会污染新曲(PB-12) |
| `_sb_segments` | SponsorBlock 线程 | dict 单键写,良性;无上限增长(Info) |

---

## 3. 发现列表(按严重度)

### PB-01 High — 等待态从不补发下载:当前曲没人在下载时主循环永久卡住
- **位置**:bot/player.py:772-798(等待分支只有 `is_ready / is_failed / _stream_playable / else 等待`);触发方:commands/playback.py:109-114、web_api.py:123-131(暂停时 skip)、player.py:402/410(下载线程校验失败删当前曲)、player.py:365-369(按 id 去重)、playlist.py:198-208(randomize 置 -1)。
- **问题**:`wait_for_ready=True` 时主循环假定"当前曲已经有人在下载",只轮询;凡是把 `current_index` 换到一首未就绪且不在下载中的条目、而又没调用 `start_download()` 的路径,都会让播放永久停住(直到用户手动 `!play N`/skip)。
- **证据**:
  ```python
  # player.py:775-798
  current = var.playlist.current_item()
  if current:
      if current.is_ready(): ... launch
      elif current.is_failed(): ...
      elif self._stream_playable(current): ...
      else:
          self._loop_status = 'Wait for the next item to be ready'   # 永不补发下载
  ```
  触发路径(均已复现):
  - (a) **暂停时连按 skip 超过预取窗口**(`test_F4_skip_while_paused_then_resume_waits_forever`):`cmd_skip` 暂停分支只做 `var.playlist.next(); bot.wait_for_ready = True`(playback.py:112-114),不调 `start_download`;`resume()` 因 `pause_at_id` 不匹配直接 return(player.py:943-946)。预取只覆盖后 `prefetch_count=2` 首,第 3 次 skip 落到的歌没人下载。1000 次迭代后 `launched == [] and downloads == []`。
  - (b) **清空/删除后重加同一 URL,而旧下载仍在跑**(`test_F6_readd_during_download_never_becomes_ready`):交错——
    1. 主线程 player.py:760 `start_download(W1)` → Prepare-X 线程开始下载旧对象 I1;
    2. 聊天线程 `!clear` → playlist.py:216 `var.cache.free_all()`(I1 被逐出缓存,下载线程仍持有 I1);
    3. 聊天线程重新 `!url X` → cache.py:56 `fetch()` 从库里重建**新对象 I2**(`ready='validated'`,item.py:61);
    4. 主线程 player.py:760 `start_download(W2)` → player.py:366 `X in _active_downloads` → 直接 return(去重);
    5. Prepare-X 完成,把 **I1** 置 `ready='yes'`,player.py:431 移出 `_active_downloads`;
    6. 主线程每轮 `I2.is_ready()` → False,`is_failed()` → False,`_stream_playable` → `I2.downloading=False` → False → **永久等待**。
  - (c) **下载线程校验失败删掉当前曲**:Prepare 线程 player.py:402 `remove_by_id(current)` → `current_index` 现在指向下一首 B(PB-02 的同一机制);主线程 player.py:775 取到 B,B 未被预取则永久等待(YouTube 播放列表条目不在入队时校验,恰好走这条路,见 player.py:323-325 注释)。
  - (d) PB-03 的结局也落到这里。
- **影响**:任何用户(聊天或 Web)可触发;播放无声停住,Web 显示"等待准备",看门狗不会介入(主循环本身是活的)。
- **修复建议**:在等待分支的 `else` 里补发:
  ```python
  else:
      try:
          item = current.item()
          if not getattr(item, 'downloading', False):
              with self._download_lock:
                  running = current.id in self._active_downloads
              if not running:
                  self.async_download(current)   # 幂等,已有去重
      except Exception: pass
      self._loop_status = 'Wait for the next item to be ready'
  ```
  对 (b) 还需让 `_active_downloads` 存 **item 对象 id**(`id(item)`)或在 `_download` 结束时若 `var.cache[id] is not item` 则把新对象一起置为 ready/重新 validate。
- **置信度**:Confirmed(a、b 单测复现;c 由 PB-02 机制 + 代码路径推出)。

### PB-02 High — 删除"当前曲"后主循环再 `next()`:紧随其后的一首被跳过(one-shot/autoplay 下被直接删除)
- **位置**:bot/player.py:749-755(解码失败)、player.py:784-786(`is_failed`);根因 media/playlist.py:142-143(`remove()` 只在 `current_index > index` 时回退)、playlist.py:336-343(one-shot `next()` 删 `self[current_index]`)。
- **问题**:`remove(current_index)` 后指针已经指向"下一首",随后的 `next()` 又前进一格。
- **证据**:
  ```python
  # player.py:749-755
  var.playlist.remove_by_id(current.id)   # 指针此时已指向 B
  ...
  if not self.wait_for_ready:
      if var.playlist.next():               # 再 +1 → C(one-shot:删除 B)
  ```
  复现:`test_F2_oneshot_decode_failure_drops_following_song`(one-shot `[A,B,C]`,A 解码失败 → 队列变 `['C']`,B 未播放即被删)、`test_F2_repeat_decode_failure_skips_following_song`(repeat → 当前变 C)、`test_F2_is_failed_branch_also_skips`(下载失败分支同样)。
- **影响**:任何导致 ffmpeg 非零退出(坏文件、不支持的编码、被 cleanup 删掉的缓存文件)或下载失败的歌,都会连带吞掉下一首;one-shot 是不可恢复的删除。
- **修复建议**:播放器里删除当前曲时同时回退指针,例如新增 `playlist.remove_current()`:持锁删除后 `current_index -= 1`(one-shot/autoplay 置 -1,让 `next()` 不再删);或在 player.py:749/785 之后显式 `var.playlist.current_index -= 1`(注意 web_api.py:162-179 与 playback.py:128-153 已手动 `-= 1`,两套逻辑要统一到同一个 helper,避免双重回退)。
- **置信度**:Confirmed。

### PB-03 High — `URLItem.validate()` 在下载进行中被调用:删掉正在边下边播的文件并把状态改回 `validated`
- **位置**:media/url.py:98-141(尤其 101、108-113、139);调用方 media/playlist.py:268-299(Validating 线程)、commands/playback.py:187-199(`!repeat` 插入同一 wrapper)。
- **问题**:`validate()` 只看 `ready in ['yes','validated']`,不看 `downloading`;下载中 `ready='preparing'`(url.py:284)+ 文件存在 + `.incomplete` 存在 → 被当成"崩溃残留"删除。
- **证据**:
  ```python
  # url.py:108-113
  if os.path.exists(self.path):
      if os.path.exists(self._incomplete_marker_path()):
          self._discard_incomplete_download()   # 删 <id>* —— 包括正在写的文件
  ...
  self.ready = "validated"                      # url.py:139,覆盖 'preparing'
  ```
  交错(`stream_while_downloading=True` 为默认值,configuration.default.ini:71):
  1. Prepare-X 线程 url.py:315 以 nopart 写 `<tmp>/<id>`,主线程已在 player.py:793 流式 launch;
  2. 用户觉得"没反应"又发一次 `!url X`(或 `!repeat`)→ 同一缓存对象被 append → playlist.py:72 `async_validate` → Validating 线程 playlist.py:284 `item.validate()`;
  3. url.py:113 删除 `<id>` 与 `<id>.incomplete`;url.py:139 `ready='validated'`;
  4. yt-dlp 继续写入已 unlink 的 inode;完成后 url.py:358 `ready='yes'`,但 url.py:90 `os.path.exists(path)` 为假 → url.py:93 改回 `validated` → `is_ready()` 永远 False;
  5. 主线程:`_stream_rewait`/等待分支 → PB-01 永久等待;若后来有人重新 `prepare()`,则整首**重复下载**一遍。
  复现:`test_F8_validate_mid_download_deletes_growing_file`(真实 URLItem,断言文件被删、`ready=='validated'`、之后 `is_ready()` 为 False)。
  非流式配置下(无 marker):`validate()` 会再跑一次 `_get_info_from_url`(网络,数秒),若其 `ready='validated'` 写入晚于下载线程的 `ready='yes'`,同样降级 → PB-01。
- **影响**:普通用户重复添加同一链接即可触发;长视频(正是流式播放的目标场景)最容易中招。
- **修复建议**:url.py:101 改为
  `if self.ready in ['yes', 'validated', 'preparing'] or self.downloading: return True`;
  另外 `_check_valid` 对已在播放列表中的同一对象可跳过。
- **置信度**:Confirmed(单测)。

### PB-04 Medium — `remove_by_id` 正序按下标删除:有重复条目时删错歌,且留下指向已释放缓存的"孤儿" wrapper
- **位置**:media/playlist.py:155-165;后果传播 player.py:322-335、player.py:749-750。
- **问题**:先收集全部下标再正序 `remove(index)`,每删一个后面的下标就错位一格;调用方随后 `free_and_delete(id)` 把仍留在队列里的同 id 条目的缓存删掉。
- **证据**:
  ```python
  for index, wrapper in enumerate(self):
      if wrapper.id == id: to_be_removed.append(index)
  for index in to_be_removed:
      self.remove(index)          # 下标已错位
  ```
  `test_F1_remove_by_id_with_duplicates_removes_wrong_items`:`!repeat 3` 后队列 `[A,A,A,A,B,C]`,A 解码失败 → 结果 `['A','A','C']`(**B 被删**,两个 A 残留),残留 A 的 `item()` 抛 `ItemNotCachedError`。
  级联(`test_F1b_orphan_next_item_aborts_fresh_launch`):孤儿成为"下一首"时,player.py:783 `async_download_next()` → player.py:328 `next.is_ready()` 抛 `ItemNotCachedError`(只捕获了 `ValidationFailedError`)→ 冒到 player.py:592 兜底 → **杀掉刚开始播放的那首**并跳过它。
- **影响**:重复条目(`!repeat`、重复添加)+ 任一失败即触发;误删他人点的歌、刚开播的歌被跳过。
- **修复建议**:`for index in reversed(to_be_removed)`,并把收集与删除放进同一个 `with self.playlist_lock`;`async_download_next` 的 except 增加 `ItemNotCachedError`(移除该条目)。
- **置信度**:Confirmed。

### PB-05 Medium — single 模式下最后一首失败 → 每 0.1 s 一次 IndexError 的死循环
- **位置**:media/playlist.py:432-444(`SingleLoopPlaylist.next` 不校验 `current_index < len`)+ playlist.py:142(删当前曲不回退)+ player.py:749/785。
- **问题**:`[A,B]` 当前为 B(index 1),B 解码失败 → 删除后 `len==1, current_index==1` → `next()` 执行 `self[1]` → IndexError → loop() 兜底置 `wait_for_ready=False` → 下一轮再 `next()` → 再抛……
- **证据**:`test_F3_single_mode_failure_of_last_item_wedges_loop`:50 次迭代 ≥49 次 IndexError,`launched == []`。Web 侧任何 `current_item()` 也会抛 IndexError。
- **影响**:播放停止且日志每秒 10 条 traceback(Docker stdout 日志无上限);需用户手动 `!play N` 或加歌才能恢复。
- **修复建议**:PB-02 的统一回退即可修复;另在 `SingleLoopPlaylist.next` 里 `if self.current_index >= len(self): self.current_index = 0`。
- **置信度**:Confirmed。

### PB-06 Medium — 等待中删除当前曲(Web/聊天)→ 重播上一首,或(repeat/random)播放队尾那首
- **位置**:web_api.py:162-179、commands/playback.py:128-153;根因 player.py:915-922(`interrupt()` 在 `thread is None` 时是空操作)+ playlist.py:167-172(`current_index==-1` 时 `self[-1]`)。
- **问题**:这两个处理器假设"interrupt 之后主循环会 `next()`",于是先 `current_index -= 1`;但在等待态(没有 ffmpeg)下主循环不会 `next()`,而是直接 launch 回退后的那首。
- **证据**:`test_F4_remove_current_while_waiting_replays_previous`(调用真实 `web_api._queue_remove(1)`,`[A,B,C]` 正在等 B → 播放 A)、`test_F4_remove_first_item_while_waiting_plays_last_item_repeat_mode`(删 index 0 → `current_index=-1` → `self[-1]` → 播放 C)。
- **影响**:Web 队列页"删除当前(正在缓冲)的歌"即触发;播错歌。
- **修复建议**:这两个处理器在删除后若 `bot.thread is None` 则改为 `bot.play(var.playlist.current_index)`(或调用 `bot.wait_for_ready=True; start_download(current)`),不要依赖 interrupt 的副作用;`BasePlaylist.current_item()` 对 `current_index == -1` 返回 False 而不是 `self[-1]`。
- **置信度**:Confirmed。

### PB-07 Medium — `resume()` 的早退路径不设 `wait_for_ready`,主循环随即多跳一首
- **位置**:bot/player.py:934-949。
- **问题**:三个早退分支只改 `playhead`,`wait_for_ready` 保持暂停前的 False,下一轮主循环走 player.py:754 `next()`。
- **证据**(均复现):
  - `current_index == -1`(player.py:936-939):自己 `next()` 一次,主循环再 `next()` 一次 → repeat 跳过第 1 首、one-shot 直接删掉第 1 首(`test_F5_*`)。作者已在 web_users.py:546-548 的注释里承认并**只在该入口绕过**,`!play`(playback.py:52)、`/api/controls resume`(web_api.py:205)、idle 自动恢复(channels.py:321)、`users_changed`(core.py:466)仍走这条路。
  - `pause_at_id` 不匹配(暂停后删掉当前曲,`_queue_remove` 暂停分支不调整指针 → 当前变成下一首 B)→ resume 后跳过 B(`test_F5b_remove_current_while_paused_then_resume_skips_next`)。
  - 流式条目暂停在下载边缘、`_stream_playable` 为假 → 整首被跳过且 playhead 归零(`test_F5c_resume_of_partially_downloaded_stream_skips_song`)。
- **修复建议**:early-return 前统一 `self.wait_for_ready = True`(当前曲存在时),并去掉 `-1` 分支里的 `next()`,改为 `point_to(0)`(one-shot 的 `current_item()` 自己会处理 -1);流式条目不可播时应保持 `wait_for_ready=True` 并保留 playhead,让等待分支在数据够了后续播。
- **置信度**:Confirmed。

### PB-08 Medium — `resume()` 先发布 `is_pause=False` 再设 `wait_for_ready`:与主循环竞争导致跳歌
- **位置**:bot/player.py:935 与 948。
- **交错**:
  1. 控制线程(Flask / users_changed / 聊天)player.py:935 `self.is_pause = False`;
  2. 主线程 player.py:704 看到 `not is_pause and not raw_music`,player.py:754 `wait_for_ready` 仍为 False → player.py:755 `next()` → 指针移到 B;
  3. 控制线程 player.py:941-946:当前曲 B 的 id ≠ `pause_at_id` → `playhead=0; return`。
  结果:暂停的那首被跳过,且不是从断点续播。
- **证据**:`test_F9_resume_publishes_is_pause_before_wait_for_ready`(在 `current_item()` 处注入一次主循环迭代,模拟 GIL 切换)。
- **影响**:窗口很窄(主循环大多在 `sleep(0.1)`),但每次恢复都有概率;`users_changed` 每个事件起一个线程,并发 resume 更容易命中。
- **修复建议**:先计算好 `playhead/wait_for_ready/pause_at_id`,**最后**才写 `self.is_pause = False`;更彻底的做法是给 bot 增加一把 `self._state_lock`,主循环的"推进"段与所有控制方法共用。
- **置信度**:Likely(交错已用注入复现,真实命中率未测)。

### PB-09 Medium — `play(index)` 与自然播完竞争:播放的是 index+1
- **位置**:bot/player.py:883-897(先 `point_to`,`start_download` 之后才 `wait_for_ready=True`)vs player.py:754-755。
- **交错**:
  1. 用户线程 player.py:885 `interrupt()`(置 `on_interrupting`,sleep 0.1)→ player.py:889 `point_to(5)`;
  2. 用户线程进入 player.py:893 `start_download` → `async_download` 起线程 + `send_channel_msg`(网络发送,可能慢);
  3. 主线程:ffmpeg 读到 EOF(或 interrupt 后 0.1+0.1 s 的下一轮)→ player.py:754 `wait_for_ready` 仍为 False → player.py:755 `next()` → `current_index=6`,并 `start_download(6)`;
  4. 用户线程 player.py:895 `wait_for_ready = True`。结果播放第 7 首而不是第 6 首。
- **证据**:`test_F7_play_index_races_natural_end`(让第一次 `send_channel_msg` 阻塞来固定交错,最终 `current_index == 6`)。
- **影响**:点歌/Web 点"播放此曲"偶发播错;`start_download` 在当前曲未就绪时要发聊天消息,窗口 = 消息发送耗时。
- **修复建议**:`play()` 中把 `self.wait_for_ready = True` 放到 `point_to` 之前(主循环在 `wait_for_ready=True` 时不会 `next()`);或用 PB-08 的状态锁包住整个 `play()` 与主循环推进段。
- **置信度**:Likely。

### PB-10 Medium — 断线后主循环卡在"等缓冲"内层循环,不再检查 `is_alive()`,只能靠看门狗 `os._exit`
- **位置**:bot/player.py:653-656;core.py:124-127(未传 `reconnect`,pymumble 默认 False:pymumble `src/mumble/mumble.py:507`,`run()` 在 652-654 行断线即 break)。
- **问题**:主循环把 send 缓冲维持在 ~0.5 s;断线后 pymumble 线程退出,缓冲不再被消费,`get_buffer_size() > 0.5` 永真,内层 while 只检查 `self.thread` 和 `self.exit`。
- **证据**:`test_F11_disconnect_wedges_buffer_wait_loop`(buffer 恒 0.51、`is_alive()` 为 False,`_loop_iteration` 0.5 s 后仍未返回)。
- **影响**:每次断线都要等 `watchdog_timeout`(默认 120 s)才 `os._exit(1)` 由 Docker 拉起;`watchdog_timeout=0` 时进程永远挂着(heartbeat 停写,只有配置了 autoheal 才会重启)。`os._exit` 跳过退出时的 `playlist.save()`(只剩 15 s 一次的 autosave)。
  关于"重连后旧线程泄漏 / sleep 节拍错位":**进程内不存在重连**——断线 = 主循环退出 = 进程重启,因此不存在旧线程跨连接泄漏或节拍错位的问题;节拍完全由 `get_buffer_size()` 反馈驱动,不依赖绝对时间。
- **修复建议**:player.py:653 条件加 `and self.mumble.is_alive()`;循环退出后若非 `self.exit` 主动 `var.playlist.save()` 再 `sys.exit(1)`。
- **置信度**:Confirmed(代码 + pymumble 源码 + 单测)。

### PB-11 Low — one-shot:队列放完后 0.1 s 内加入的歌会被直接删除
- **位置**:media/playlist.py:331-346(删完最后一首返回 False 时 `current_index` 仍为 0)。
- **交错**:主线程 player.py:755 `next()` 删掉 A,列表空、`current_index` 留在 0 → 用户线程 append B → 主线程下一轮 player.py:755 `next()`:`current_index != -1` → 删除 `self[0]`(B)。
- **证据**:`test_F10_oneshot_item_added_right_after_queue_end_is_dropped`。
- **修复建议**:playlist.py:338-339 在 `len(self)==0` 时同时 `self.current_index = -1`。
- **置信度**:Confirmed(交错确定,窗口 ~0.1 s)。

### PB-12 Low — 上一个 ffmpeg 的 stderr 排空线程会污染新曲的 `last_ffmpeg_err`
- **位置**:bot/player.py:290-296 与 298-311。
- **问题**:`launch_music` 清空共享 deque 后启动新排空线程,但被 kill 的旧进程的排空线程仍可能在之后 append 并覆盖 `self.last_ffmpeg_err`;新曲失败时 player.py:747 打出的是上一首的错误。
- **修复建议**:每个进程用自己的 deque(作为参数传入排空线程),失败时读该进程对应的 deque。
- **置信度**:Likely。

### PB-13 Low — 失败归因用 `current_item()` 而不是本次 ffmpeg 实际播放的 `_playing_id`
- **位置**:bot/player.py:740-750、164-170、31-41。
- **问题**:ffmpeg 退出到检查 rc 之间(`wait(timeout=1)`、网络流读阻塞)若用户换了歌,`remove_by_id + cache.free_and_delete`(**删库 + 删文件**,cache.py:90-101)作用在用户新选的那首上。
- **修复建议**:三处都先比较 `current.id == self._playing_id`,不一致则不做失败处理。
- **置信度**:Likely(窗口小;网络流 `-rw_timeout 30s` 时窗口变大)。

### PB-14 Low — `interrupt()` 是协作式的:网络流卡住时 skip/pause/stop 最多延迟 ~30 s+
- **位置**:bot/player.py:915-922、670。
- **问题**:`interrupt()` 不 kill,只置位;主循环阻塞在 `stdout.read()` 时(电台/直播 `-rw_timeout 30000000` + `-reconnect_delay_max 10`)要等读返回才会处理。
- **修复建议**:`interrupt()` 里直接 `self.thread.kill()`(读会立即 EOF),保留 `on_interrupting` 只用于淡出。
- **置信度**:Likely。

### PB-15 Low — autoplay 模式曲库为空(或全部被 `don't autoplay` 排除)时每 0.1 s 查一次库
- **位置**:media/playlist.py:511-514 + player.py:754-771。
- **问题**:`AutoPlaylist.next()` 在空列表时 `refresh()`(database.py:450 每次新开 sqlite 连接 + `ORDER BY RANDOM()`),主循环在空队列状态每轮都调用 `next()`。
- **修复建议**:refresh 结果为空时记录时间,至少间隔数十秒再试。
- **置信度**:Confirmed(代码路径无歧义)。

### PB-16 Low — 直播 `uri()` 在主线程同步跑 yt-dlp
- **位置**:bot/player.py:210 → media/livestream.py:117-132。
- **问题**:每次直播启动/重连(`_livestream_retry` 最多 3 次)都在主循环里做完整的 `extract_info`,未设置 `socket_timeout`;期间不发声、`last_loop_at` 不更新,网络很差时可能撞上 120 s 看门狗。`uri()` 抛 `DownloadError`(未捕获)会走 loop() 兜底跳过整首直播。
- **修复建议**:在等待分支里由后台线程预解析流地址,launch 时只取结果;给 `_base_ydl_opts` 加 `socket_timeout`。
- **置信度**:Needs-verification。

### PB-17 Info — ffmpeg 子进程生命周期 / returncode 解释
- stdout:主循环读;stderr:每个进程一个排空线程(player.py:293-296)→ 长时间播放**不会**因 stderr 填满而阻塞。stdin:`-nostdin`。
- 主循环自己 kill 的两条路径(淡出分支 player.py:695-696、SponsorBlock player.py:119-122、loop() 兜底 597-601)都 `kill()` 后直接丢弃 Popen,**不 wait**;但排空线程持有 proc 引用直到 stderr EOF,之后 `Popen.__del__` 把它放进 `subprocess._active`,下次 `Popen()` 时 `_cleanup()` 回收。结论:最多存在 1 个短暂僵尸,无累积。
- 因为自杀路径从不 `wait()`,`_stream_rewait` / `_livestream_retry` / 解码失败判断里的 `(-9, -15)` 只会在**外部**杀死 ffmpeg(如 OOM killer)时出现,此时被当作"主动 kill"→ 正常前进。Windows 上外部终止 rc=1 会被判为解码失败。与注释意图一致,未发现不一致的分支。
- 进程退出(看门狗 `os._exit`、正常退出):ffmpeg 的 stdout 管道关闭 → 写入时 SIGPIPE 退出,不会遗留。

### PB-18 Info — 流式边界与 `.incomplete`
- `playable_from`(url.py:249-261):`duration` 为 0/未知 → False;`_stream_playable` 还要求 `duration >= stream_min_duration`,因此 `_stream_rewait` 中 `duration - 2` 不会遇到 0。尾部用 `min(duration-1, playhead+buffer)` 放宽,`total_bytes_estimate` 偏大时只会退化为"等下完",不会卡死(下载完成 `ready='yes'` 即 True)。
- progress 不单调:换片/续传失败时 yt-dlp 若从 0 重下(nopart 下会截断文件),ffmpeg 读到 EOF → rc 0 → `_stream_rewait` 回到等待 → 按新 progress 重新等到水位再 `-ss playhead` 续播,可自愈(中间有一段静音)。按"字节比例 ≈ 时间比例"估算对 VBR 有偏差,同样靠 rc0 → rewait 自愈。
- 崩溃重启:库里通常存的是 `ready='validated'`(下载中 `'preparing'` 不会单独 bump version;但封面线程 url.py:419 `version += 1` 可能让 `'preparing'` 落库)。`'validated'` 时 `validate()` 在 url.py:101 提前返回,**不检查 marker**;随后 `_download` 交给 yt-dlp:已核对 yt-dlp 2026.08.19 `YoutubeDL.process_info`(3584-3588)在 `dl_filename == temp_filename`(nopart 下二者相同,已脚本验证 `prepare_filename` 与 `'temp'` 返回同一路径)时走续传分支,所以残缺文件会被续传而不是被当成"已下载"。残余风险:重启后重新解析可能选到不同格式/码率,HTTP Range 续传会拼接出损坏文件——Needs-verification。`'preparing'` 落库的情形会走 url.py:108-113 丢弃重下,正确。
- nopart 与 Windows:从代码看,CPython `open()` 与 ffmpeg 的 `win32_open`(`_wsopen(..., SH_DENYNO)`)都允许并发读写,边下边播本身没有 Windows 专属阻断;在 Windows 上会失败的是"文件被 ffmpeg 打开时删除"——url.py 的删除都包了 `except OSError: pass`(失败时残留文件),`cache.free_and_delete`(cache.py:96-97)没包,但它只在 ffmpeg 已退出后调用。要确认仍需真机复验。

---

## 4. 各播放模式的终止条件与饿死

| 模式 | 空队列 | 正常终止 | 饿死/卡死风险 |
|---|---|---|---|
| one-shot | 主循环每 0.1 s `next()`,`'Empty queue'` | 放完一首删一首 | PB-02(失败吞掉下一首)、PB-11(0.1 s 窗口)、PB-07(resume 删首曲) |
| repeat | 同上 | 无限循环 | PB-02 跳歌;PB-06 `current_index=-1` 时 `self[-1]` |
| single | `next()` 置 -1 | 只有 skip 才前进 | **PB-05 IndexError 死循环**;`upcoming_items` 返回 [] 所以 skip 后的歌永远不会被预取 → skip 后总要现下(不是 bug,但 PB-01 的路径 a 在 single 下更容易命中) |
| random | 同上 | 到尾 `randomize()` 后从 0 开始(可能连续两次同一首) | `cmd_random`/`_set_mode('random')` 在等待态把指针置 -1 → `self[-1]` 未下载 → PB-01 |
| autoplay | `refresh()` 拉随机曲 | 同 one-shot | PB-15(空库轮询);其余同 one-shot |

## 5. `except Exception` 吞异常后的状态评估

| 位置 | 吞掉后 | 结论 |
|---|---|---|
| player.py:592-607(主循环兜底) | kill ffmpeg、`wait_for_ready=False` → 下一轮 `next()` | 能前进;但若异常发生在等待分支或 `async_download_next`,被跳过的是**刚 launch 的那首**(PB-04 级联);若异常来自 `next()` 本身则形成死循环(PB-05) |
| player.py:405-411(校验意外异常) | `remove_by_id`(有 PB-04 错位 bug),不 `free`、不置 failed | 若删的是当前等待曲 → PB-01(c) |
| player.py:421-428(下载意外异常) | 置 `ready='failed'` | 正确,等待分支会移除 |
| player.py:418-420(`PreparationFailedError`) | 依赖 item 自己已置 failed | URLItem 是这样做的(url.py:374);其他类型若不这样做会永久等待(本次范围内未发现) |
| player.py:711-719(wait 超时) | kill,`ffmpeg_rc=None` | 正确,不会被误判为解码失败 |
| player.py:322-335 `async_download_next` | 只接 `ValidationFailedError` | `ItemNotCachedError` 漏网 → PB-04 级联 |
| playlist.py:292-296(校验线程) | `remove_by_id` 后继续 | 同 PB-04 错位 |
| channels.py:237-241 / core.py:454-457 / core.py:315-318 | 只记日志 | 无状态残留问题 |

## 6. 剔除 / 不报
- `_prefetch_upcoming` 长度检查后放锁(TOCTOU):最多让并发下载数超 1,影响极小。
- ducking 回调无锁写浮点/布尔:良性。
- `ctrl_caught` 在信号处理器里调用 `playlist.save()`,与 autosave 嵌套(RLock 可重入、写入内容相同):未发现实际损坏路径。
- `_sb_segments` 无上限增长:每首一个小条目,不报。
- `RandomPlaylist` 到尾重洗可能连续两次同一首:行为问题,非并发 bug。

## 7. 覆盖范围
已逐行读:bot/player.py、bot/core.py、bot/startup.py、bot/channels.py、media/playlist.py、media/cache.py、media/url.py、media/livestream.py、media/item.py;media/radio.py 只看了网络调用与超时(其余是标题解析);交互调用方读了 web_api.py:95-215、commands/playback.py:30-225、web_users.py:539-560;并下载 pymumble-upstream 分支源码(scratchpad/ext,只读)核对 `reconnect` 默认值与缓冲实现;核对了已安装 yt-dlp 2026.08.19 的 nopart 续传分支。
未看:bot/cleanup.py / bot/cache_store.py 的删除逻辑细节(只确认了它们保护 `downloading` 条目和 `.incomplete`)、media/spotify.py、media/url_from_playlist.py、media/file.py、interface.py 的 /post 分支(与 web_api.py 同构,结论同样适用于 interface.py:465-482 与 545-551)。
