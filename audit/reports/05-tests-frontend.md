# 测试质量与前端工程组 — 审计报告(已合并前端工人报告)

分支 claude/quirky-fermi-6nou7s,只读审计。组长(Fable)亲自完成测试线 + 前端/构建线;另一名 Sonnet 工人独立审前端与构建(frontend-worker.md,14 条),组长逐条打开文件核对行号后合并:**新增 8 条**(FE-01/03/04/06/08/09/11/12,标注"来源 worker")、**与组长条目重复合并 4 条**(FE-02→F2 并升为 Medium、FE-05→F1、FE-07→F3、FE-10→F3)、**Info 并入 2 条**(FE-13/14→B5,C 节→B4)、**剔除 0 条**,行号更正 3 处(见剔除/降级记录)。

合并后共 **22 条**:Medium 5(T1、T2、F1、F2、F6)/ Low 13(T3、T4、F3、F4、F5、F7–F13、B1)/ Info 4(B2–B5)。

## 总评

**测试套件**:253 个用例、全量 6 秒、AST 扫描 0 条空断言 / 0 条永真断言,深 mock 处(test_stability、test_sponsorblock 对 Popen/Thread 打桩)仍断言真实构造出的 ffmpeg 命令行,整体质量不错。但**套件存在隐藏的顺序依赖**:单独运行 `tests/test_play_history.py`、`tests/test_livestream.py` 各失败 1 条,反序运行全量失败 1 条——它们靠其他测试模块泄漏到 `variables` 的全局状态(`var.config`、`media.url` 的注册副作用)才通过。两条 `PytestUnhandledThreadExceptionWarning` 来自测试假对象不完整(非生产 bug),但派生的守护线程会在"下一条"用例运行时触碰 `var.bot`/`var.cache`,是潜在的跨用例干扰源。覆盖率 51%,最大空白是 interface.py(17%,新 UI 仍在调用的 `/post`、`/library`、鉴权与静态托管均无测试)和 commands/*(6%–22%)。

**前端**:无 v-html / window.open / `any`,XSS 面干净;dist 与源码**逐字节一致**(组长与工人独立构建结论相同),`npm ci && npm run build` 在 node 22 通过;Flask 托管路由正确、路径穿越被 send_from_directory 挡住。主要问题是**多人共享场景下的并发正确性与韧性**:队列按数组下标提交且服务端不校验(别人刚删一首,你点的 ✕ 就落到另一首上);状态轮询 fetch 无超时(半开连接让 UI 永久停在旧状态且不报错);上传取消既显示错文案又不清理服务端 staging(默认 4G 上限、默认开启,可占满磁盘);/api/me 首次失败后永不重试导致整页降级为 Guest;若干写操作无错误路径。

**最该先修的 3 件事**:
1. T1/T2:让 test_play_history、test_livestream 自给自足,并给 test_commands_wiring / test_livestream 的模块级 `var.config` 加 teardown 还原。
2. F6 + F2:队列操作带 `item.id`/`version` 让服务端拒绝陈旧请求(409);上传取消/失败路径调用 `DELETE /api/upload/<id>` 并把 abort 统一抛 `UploadError('aborted')`。
3. F1:useStatus 的轮询加 AbortController 超时(例如 10 s)+ `visibilitychange` 降频,否则网络半开时整个页面静默冻结。

## 覆盖率(`coverage run -m pytest` → `coverage report --include='*.py' --omit='tests/*'`)

| 模块 | Stmts | Miss | Cover | 备注 |
|---|---|---|---|---|
| interface.py | 552 | 458 | 17% | 无任何测试 import 它:requires_auth(password/token/封禁)、`/`、`/assets`、`/app/`、`/legacy`、`/post`、`/library`、`/library/info` 全未覆盖,而新 UI LibraryPage 仍调用后三者 |
| commands/library.py | 249 | 233 | 6% | 聊天命令整体只有 wiring 冒烟 |
| commands/sources.py | 267 | 241 | 10% | |
| commands/streaming.py | 180 | 155 | 14% | |
| commands/volume.py | 73 | 63 | 14% | |
| commands/playback.py | 132 | 110 | 17% | |
| commands/web.py | 64 | 52 | 19% | |
| commands/admin.py | 93 | 73 | 22% | |
| media/spotify.py | 215 | 190 | 12% | |
| media/cache.py | 196 | 145 | 26% | CachedItemWrapper / get_cached_wrapper 只被间接触及 |
| media/radio.py | 110 | 81 | 26% | |
| media/url_from_playlist.py | 71 | 51 | 28% | |
| util.py | 519 | 333 | 36% | |
| media/playlist.py | 374 | 203 | 46% | |
| bot/player.py | 629 | 327 | 48% | 播放主循环、真实 ffmpeg 进程路径未覆盖 |
| database.py | 510 | 250 | 51% | |
| media/file.py | 145 | 67 | 54% | |
| media/item.py | 84 | 30 | 64% | |
| media/url.py | 362 | 123 | 66% | |
| web_search.py | 106 | 31 | 71% | `cookie_file` 分支(:112-128)无测试 |
| playlist_import.py | 332 | 78 | 77% | |
| bot/cleanup.py | 219 | 47 | 79% | |
| web_cache.py | 66 | 13 | 80% | |
| web_api.py | 232 | 43 | 81% | |
| web_channels.py | 71 | 12 | 83% | |
| web_upload.py | 230 | 37 | 84% | |
| media/livestream.py | 105 | 17 | 84% | |
| commands/personal.py | 128 | 20 | 84% | |
| bot/cache_store.py | 233 | 31 | 87% | |
| web_users.py | 533 | 68 | 87% | |
| bot/channels.py | 230 | 20 | 91% | |
| media/sponsorblock.py | 111 | 4 | 96% | |
| commands/__init__.py / variables.py | — | — | 100% | |
| **TOTAL** | **7790** | **3816** | **51%** | |

webui/:**零** JS/TS 测试(package.json 无 test 脚本、无 vitest/jest、无 *.spec/*.test 文件)。

### PROGRESS.md "待本机复验"项 vs 测试覆盖

| 复验项 | 已有测试 | 缺什么 |
|---|---|---|
| A2 边下边播 | test_streaming.py 22 条(水位、rewait、降级、skip 路径,全部假对象) | 没有任何测试对"增长中的文件"真的拉起 ffmpeg;Windows `nopart` 行为无覆盖(无 Windows CI) |
| A1 定时清理 | test_cleanup.py 16 条(注入时钟) | 真实 scheduler 线程/启动时机未测 |
| 频道跟随 / 无人暂停 | test_channels.py 21 条(防抖决策逻辑) | pymumble 回调层未装 pymumble,无法测;"没权限的频道被拒后留在原地"依赖真机 |
| SponsorBlock | test_sponsorblock.py 12 条(fetch/hash、`_sponsorblock_skip`、静默重启不播报) | 跳段后 ffmpeg 重启是否卡顿只能真机 |
| `!bind` 绑定 | test_bind.py 10 条 | — |
| 新 Web UI 实机 / Access 邮箱头 | test_web_users.py 16 条(JWT、别名) | interface.py 路由与托管 0 测试;前端 0 测试 |
| B 站搜索 412 → cookie_file | 无 | web_search.py:112-128 cookie 注入分支无测试 |

## 发现列表(按严重度)

### T1 · Medium · 测试顺序依赖:test_play_history 靠其他模块泄漏的 `var.config` 才通过
- **位置**:tests/test_play_history.py:118-152;泄漏源 tests/test_commands_wiring.py:18-21(`setup_module` 设 `var.config`,无 `teardown_module`)、tests/test_livestream.py:19-23(`setUpModule` 同样不还原)。
- **问题**:`test_record_on_fresh_start_only` 不设置 `var.config`,却调用 `PlayerMixin.launch_music`,该函数在记录播放历史之前(bot/player.py:218 `_sponsorblock_prefetch` → :76 `var.config.getboolean`)就需要 `var.config`;测试用 `except Exception: pass` 吞掉一切异常,所以只要 `var.config` 是 None 就静默记不到行、断言 `1 != 0`。
- **证据**:
  ```
  $ pytest -q tests/test_play_history.py                      → 1 failed (AssertionError: 1 != 0, :152)
  $ pytest -q tests/test_commands_wiring.py tests/test_play_history.py → 11 passed
  $ pytest -q tests/test_play_history.py tests/test_commands_wiring.py → 1 failed
  $ pytest -q $(ls -r tests/test_*.py)                        → 1 failed, 252 passed
  ```
  tests/test_play_history.py:146-150:
  ```python
  for start_from in (0, 42.5):
      try:
          player.launch_music(FakeWrapper(), start_from)
      except Exception:
          pass
  self.assertEqual(1, len(recorded))
  ```
- **影响**:任何重排/并行(pytest-xdist)/只跑单文件都会假失败;反过来模块级泄漏的 `var.config`(读的是 configuration.default.ini)会让其他本该失败的测试被"喂活"。这也是 PROGRESS 里"234 → 253 全绿"结论可信度的隐患。
- **修复**:(1) 测试里自行 `var.config = ConfigParser()` + 必要节,并在 finally 还原;把 `except Exception: pass` 收窄为只吞 ffmpeg/Popen 那一步(或直接 patch `bot.player.sp.Popen`、`threading.Thread`,像 test_stability.py:90-93 那样,根本不需要吞异常);(2) test_commands_wiring / test_livestream 增加 `teardown_module` 还原 `var.config`。
- **置信度**:Confirmed。

### T2 · Medium · 测试顺序依赖:test_livestream 依赖别的测试文件导入 media.url
- **位置**:tests/test_livestream.py:62;注册点 media/url.py:39 `item_id_generators['url'] = url_item_id_generator`。
- **问题**:`test_id_differs_from_plain_url_item` 读 `item_id_generators['url']`,但本文件只 `import media.livestream`(tests/test_livestream.py:26),'url' 生成器只有 media.url 被 import 时才注册;全量运行时由 test_long_video / test_stability / test_streaming(经 bot.player → media.url)顺带完成。
- **证据**:
  ```
  $ pytest -q tests/test_livestream.py                         → 1 failed: KeyError: 'url' (:62)
  $ pytest -q tests/test_long_video.py tests/test_livestream.py → 24 passed
  ```
- **影响**:同 T1;单文件跑红。
- **修复**:tests/test_livestream.py 顶部加 `import media.url  # noqa: F401  注册 'url' 生成器`。
- **置信度**:Confirmed。

### F1 · Medium · 状态轮询 fetch 无超时、无乱序/可见性处理:网络半开时 UI 永久冻结且无任何提示(合并 worker FE-05)
- **位置**:webui/src/composables/useStatus.ts:24-30(`poll`)、:49-52(`pollLoop`)、webui/src/api.ts:60-64(`getJson`)。
- **问题**:`pollLoop` 是 `await poll()` 后才 `setTimeout` 下一轮,而 `fetch` 没有 `AbortSignal.timeout`;一旦某次请求卡在半开 TCP 连接上(手机切网、Cloudflare/代理挂起),整个轮询链停住,`error` 不会被设置,页面停在最后一次状态、进度条照常走(`tick` 继续用 `lastSync` 外推),用户看不到任何异常。浏览器自身的 fetch 超时在分钟级。隐藏标签页仍以 1~3 s 轮询(浏览器后台节流兜底),切回前台不会立刻 refresh。
- **证据**:
  ```ts
  async function pollLoop() {
    await poll()                       // poll 内 fetch 无 signal/timeout
    setTimeout(pollLoop, nextDelay())  // 卡住即永不执行
  }
  ```
- **影响**:所有 Web 用户;表现为"页面看着正常但按钮没反应、进度到头不切歌"。
- **修复**:`fetch(url, { signal: AbortSignal.timeout(10_000) })`(api.ts getJson/postJson 统一加),超时走 catch 置 `error`;`pollLoop` 用 try/finally 保证重新排程;监听 `visibilitychange`:hidden 降频(15 s),visible 立刻 `poll()`。
- **置信度**:Likely(代码路径无歧义,未在真实半开网络下复现)。

### F2 · Medium · 上传取消/失败后客户端从不调用 DELETE,staging 文件残留可占满磁盘;取消文案也不对(合并 worker FE-02,严重度采用 worker 的 Medium)
- **位置**:webui/src/upload.ts:54(abort 检查)、:69-72(catch/重试)、:81-88(finish/轮询);webui/src/components/LibraryPage.vue:133-136、:217(取消按钮);服务端 web_upload.py:292-301(`DELETE /api/upload/<id>` 存在但前端无任何调用,`grep -n DELETE webui/src/upload.ts` 为空)、:110-119(`prune_stale` 仅在下一次 init 时按 mtime>24h 运行)、:39 `STALE_SECONDS`;configuration.default.ini:95 `max_upload_file_size = 4G`、:97 `upload_enabled = True`。
- **问题**:(a) 用户点 ✕ 触发 `ctrl.abort()`,进行中的 `fetch` 以 `DOMException(AbortError)` 拒绝,upload.ts 的 catch 判断 `opts.signal?.aborted` 为真后**原样 rethrow 这个 DOMException**(不是 `UploadError('aborted')`),LibraryPage 里 `err instanceof UploadError` 为假 → 文案 "Upload failed.";`UPLOAD_ERRORS.aborted = 'Cancelled.'` 只有在两片之间恰好 abort 时才会出现。(b) 取消、5 次重试失败、关闭标签页三种路径都不通知服务端,`.part` + `.json` 留在 `tmp_folder/.uploads`,24 h 内不会被 prune;init 只检查"此刻剩余空间"(web_upload.py:218-219)。
- **证据**:
  ```ts
  } catch (e) {
    if (opts.signal?.aborted || e instanceof UploadError) throw e   // AbortError 原样抛出
  ```
- **影响**:任何已登录用户反复"选 4G 文件→传到一半取消"可在一天内把 bot 的 tmp_folder 所在磁盘占满(默认开启上传),随后 yt-dlp 下载/缓存全部失败。取消后的误导文案让用户以为是故障。
- **修复**:`uploadFile` 用 try/finally,除 `done` 外的失败/取消路径 `fetch(\`${BASE}/api/upload/${id}\`, { method: 'DELETE', keepalive: true })`;abort 时统一 `throw new UploadError('aborted')`;服务端把 `prune_stale` 改为定时任务并对 `status != done` 且 >1 h 无更新的 `.part` 提前清理。
- **置信度**:Confirmed(代码路径)。

### F6 · Medium · 队列操作按数组下标提交且服务端不校验,陈旧列表会删/播错曲目(来源 worker FE-01)
- **位置**:webui/src/components/QueueList.vue:36(move)、:140(play)、:147(top)、:149(AddToPlaylist queue index)、:154(remove)、:15(`dragFrom` 在 dragstart 记录旧下标);服务端 web_api.py:245-275(`api_queue_edit` 只取 index/to)、:162-178(`_queue_remove` 仅范围校验)。
- **问题**:前端把"我点的是第 i 行"当成 `{action, index: i}` 发出,服务端只校验 `0 <= index < len`,不校验该下标上还是不是用户看到的那首。列表只在 `/api/status` 的 `version` 变化(≤3 s 轮询)、10 s 定时器、自己 act 之后刷新。
- **证据**:
  ```
  QueueList.vue:154  @click="act({ action: 'remove', index: i })"
  web_api.py:166-167 if not (0 <= index < len(playlist)): abort(400)   # 仅此校验
  ```
  路径:A 看到 [X,Y,Z] → B 在 Mumble `!rm 1` 或 Web 端删 X(media/playlist.py:135 `version += 1`)→ A 在下一次轮询到达前点 Z 行 ✕ → index=2 越界(400,被 `act` 的 catch 静默 reload,:68-69)或列表已变、删掉另一首。拖拽同理:拖动中 reload 替换 `items`(:47)后 drop 仍用旧 from。
- **影响**:共享 DJ 机器人的典型多人场景下误删/误跳他人点的歌,无任何提示。
- **修复**:请求带 `id: item.id`(或列表来源的 `version`),服务端 `_queue_remove/_queue_move/play` 入口前 `if body_id and var.playlist[index].id != body_id: abort(409)`;前端 409 时刷新并提示"队列已变化"。
- **置信度**:Likely(代码路径无歧义;竞争窗口 ≤ 一次轮询 + RTT)。

### T3 · Low · 两条 PytestUnhandledThreadExceptionWarning 的根因:假 wrapper 不完整,守护线程在"下一条"用例里崩
- **位置**:
  - 源 A:tests/test_single_loop.py:66 `get_playlist("single", pl)` → media/playlist.py:35 `from_list` → :97 `extend` → :255 `async_validate` 起 `Validating` 线程 → :272 `item.item()`,假类 `W`(tests/test_single_loop.py:13-15)没有 `item()` → `AttributeError: 'W' object has no attribute 'item'`。
  - 源 B:tests/test_web_api.py:259-265(`test_controls_mode_switch_*`)→ web_api.py:108 `media.playlist.get_playlist(mode, var.playlist)` 把 `FakePlaylist` 转成真 `RepeatPlaylist` → 同上线程 → :280 `item.format_debug_string()`,`FakeWrapper`(tests/test_web_api.py:28-40)没有该方法。
- **证据**:线程在派生它的用例返回后才死,所以 pytest 把警告记在下一条用例上:
  ```
  $ pytest -v -W error::pytest.PytestUnhandledThreadExceptionWarning tests/test_web_api.py
  test_controls_mode_switch_repeat PASSED
  test_controls_pause_resume FAILED      ← 收到的是 mode_switch 派生线程的异常
  $ pytest -q -W error::... tests/test_single_loop.py → 6 passed, 1 error(error 落在 setup/teardown 阶段)
  ```
- **是否真实 bug**:**不是生产 bug**——生产 wrapper 是 `CachedItemWrapper`,有 `format_debug_string`,其 `item()` 只抛 `ItemNotCachedError`(media/cache.py:146-150),且 `_check_valid` 已用 finally 释放锁(media/playlist.py:259-266)。但:(a) 这些守护线程在下一条用例运行时仍可能执行 media/playlist.py:284-289 的 `var.bot.send_channel_msg` / `var.cache.free_and_delete`,碰的是下一条用例刚换上的假对象,是跨用例干扰源;(b) media/playlist.py:280 的 `format_debug_string()` 在 try 之外,任何非 `ItemNotCachedError` 异常都会让整条校验线程退出、`pending_items` 里剩余项不再校验(直到下一次 append 再起线程)。
- **修复**:给 `W`/`FakeWrapper` 补 `item()`、`format_debug_string()`、`validate()`,或在这些测试里 `mock.patch.object(BasePlaylist, 'async_validate')`;源码侧可把 :280 并入 :271 的 try 并在 `except Exception` 里 `log.exception` 后 `continue`。
- **置信度**:Confirmed。

### T4 · Low · 模块级全局状态泄漏(与 T1 同源,单列便于修)
- **位置**:tests/test_commands_wiring.py:18-21、tests/test_livestream.py:19-23。
- **问题**:两处在模块 setup 里给 `var.config` 赋值且永不还原;其余 19 个文件都用 setUp 保存 / tearDown 还原(核对过 test_cache_store.py:40-66、test_cleanup.py:38-72、test_web_api.py:131-156、test_stability.py:78-85 等)。临时目录全部用 `TemporaryDirectory` 并在 tearDown `cleanup()`,sqlite 文件随之删除;未发现遗留。
- **修复**:加 `teardown_module` 恢复原值。
- **置信度**:Confirmed。

### F3 · Low · 多个写操作无错误路径;曲库查询把故障伪装成"空曲库"且无乱序保护(合并 worker FE-07、FE-10)
- **位置**:webui/src/components/PlaylistsPage.vue:77-84(`remove`,:80 `await deletePlaylist` 无 try)、:169-173(`dropItem`,:171 `await removeFromPlaylist`);webui/src/components/UserChip.vue:85-89(`unlink`,:87 `await unbindMumble`);webui/src/components/LibraryPage.vue:33-34(`/library/info` 不检查 `rv.ok`)、:47-69(`query`::49 `page.value = toPage` 在请求前写入,:60 `rv.json()` 不检查 `rv.ok`)、:278。
- **问题**:`deletePlaylist`/`removeFromPlaylist`/`unbindMumble` 任一失败(另一端已删 → 404、会话过期 401、5xx)都变成 `unhandledrejection`,UI 无提示、列表不变;LibraryPage 在 401/5xx 返回 HTML 时 json 解析抛错被吞,显示 "Nothing in the library matches.";快速翻页/切类型时慢的旧响应后到会覆盖新响应,页码标签与内容不一致(SearchPage 有 seq 守卫,这里没有)。
- **修复**:仿同文件 `play()`/`rename()` 用 try/catch + `say('…failed')`;LibraryPage `if (!rv.ok) throw` 并显示错误态;加 seq 守卫,`page.value` 成功后再赋值。
- **置信度**:Confirmed(代码路径)。

### F4 · Low · SettingsPage 定时刷新与保存竞争,可能用旧快照覆盖刚保存的设置
- **位置**:webui/src/components/SettingsPage.vue:30-35(每 4 s `reload`,无 in-flight 守卫)、:54-63(`apply` 用 POST 响应覆盖 `data`)。
- **问题**:t0 定时 GET 发出(慢);t0+0.5 s 用户切换跟随模式,POST 返回新设置写入 `data`;t0+2 s 旧 GET 返回(服务器在 POST 之前算出的旧设置)再次覆盖 `data` → UI 回弹到旧值,最多 4 s 后才纠正;用户会误以为没保存再点一次。
- **修复**:`reload` 加序号/in-flight 标志丢弃过期响应,或 `busy` 期间跳过定时刷新。
- **置信度**:Likely。

### F5 · Low · 反向代理子路径前缀下 API 相对路径逃逸
- **位置**:webui/src/api.ts:56-58(`const BASE = '..'`,注释声称保留代理前缀且说"mounted at /app/")、webui/src/upload.ts:4;interface.py:29-60(`ReverseProxied` 支持 `X-Script-Name`)、:266-271(新 UI 挂在 `/`)。
- **问题**:页面在 `/myprefix/` 时,`../api/status` 解析为 `/api/status`(跳出前缀),只有旧挂载点 `/myprefix/app/` 才正确;注释已过时。当前部署文档(deploy/WEBUI.md)走根域名,故仅在启用 `is_proxified` + 子路径时触发。(worker 在 B 节认为"反代前缀下也成立",组长按 URL 解析语义核对:`new URL('../api/x', 'https://h/myprefix/')` → `https://h/api/x`,worker 结论不成立,维持本条。)
- **修复**:`BASE = new URL('.', location.href).pathname.replace(/\/app\/$/, '/')` 或由 Flask 在 index.html 注入 base;至少改注释。
- **置信度**:Confirmed(URL 解析语义),影响范围受配置限制。

### F7 · Low · 上传"处理中"阶段无重试/超时:网络抖动即误报失败,服务端异常则无限轮询(来源 worker FE-03)
- **位置**:webui/src/upload.ts:81-88;web_upload.py:156(`finalize` 的 `meta = _load(upload_id)` 在 try 之外)、:137(ffmpeg `timeout=3600`)。
- **问题**:chunk 阶段有 5 次退避重试,finish 与轮询阶段完全没有:finish 响应丢失 → `TypeError` → UI "Upload failed.",但服务端 finalize 线程照常入库(重传产生 `name (1)` 重复);轮询中任一次 fetch/JSON 失败同样整条判失败。反向:finalize 线程在 `_load` 处因 meta 被并发 DELETE 抛 NotFound、或 bot 重启,meta 永远停在 `processing`,客户端 1 次/秒无限轮询(无上限,且取消按钮只在 `phase==='uploading'` 显示,LibraryPage.vue:217)。
- **修复**:轮询包 try/catch 容忍 N 次失败并加总超时;finish 失败时先 GET 状态再判定;处理中阶段也显示取消。
- **置信度**:Confirmed(代码路径),触发需网络抖动/异常。

### F8 · Low · 轮询响应与控制响应乱序时,旧状态覆盖新状态(来源 worker FE-04)
- **位置**:webui/src/composables/useStatus.ts:16-22(`applyStatus` 无条件覆盖)、:24-30(poll)、:62-68(control);同类:SearchPage.vue:72、LibraryPage.vue:150、QueueList.vue:66 都 `applyStatus(响应)`。服务端已提供 `server_time`(web_api.py:66),前端从未使用。
- **问题**:poll 在 t0 发出,用户 t0+ε 点 Pause → control 响应先到 `applyStatus(play=false)` → t0 的 poll 响应(服务端在 Pause 之前生成)后到,把 play 改回 true,UI 显示"仍在播放"最长 3 s。经 Cloudflare Tunnel 延迟大时概率明显。
- **修复**:`applyStatus` 里 `if (status.value && s.server_time < status.value.server_time) return`。
- **置信度**:Likely。

### F9 · Low · 搜索:清空输入后旧结果"复活";Enter 与防抖双发(来源 worker FE-06)
- **位置**:webui/src/components/SearchPage.vue:36-40(q<2 提前 return 且不 `++seq`、不置 `loading=false`)、:41(`++seq`)、:47(`mySeq !== seq`)、:29-32 + :100(`@input` 防抖 + `@keydown.enter="search"` 不 `clearTimeout(debounce)`)。
- **问题**:输入 "ab" → 400 ms 后发出请求(seq=1,yt-dlp+bilibili 通常数秒);在途时删光输入 → `search()` 走 q<2 分支只清空 results;之后 seq=1 的响应到达,`mySeq === seq` 成立 → 搜索框为空却显示旧结果。打字后立刻回车向后端发两次重型搜索;无 AbortController,后端也不取消。
- **修复**:q<2 分支里 `++seq; loading.value = false`;Enter 时 `clearTimeout(debounce)`;新搜索前 abort 上一次 fetch。
- **置信度**:Confirmed(代码路径)。

### F10 · Low · /api/me 首次失败后永不重试,整页永久显示 Guest 且个人歌单功能消失(来源 worker FE-08)
- **位置**:webui/src/composables/useMe.ts:9-15(`reloadMe` catch 吞错)、:30-33(`started` 只启动一次);UserChip.vue:15(`signedIn = !!me?.identity`)、:200("You're browsing as a guest");AddToPlaylist.vue:56(`v-if="me?.can_have_playlists"`);PlaylistsPage.vue:197。
- **问题**:页面加载瞬间网络抖动/后端重启 → `reloadMe` 失败,`me` 永远 null;状态轮询恢复后 UI 其余部分正常,但所有 ♡ 按钮消失、头像显示 Guest,并提示"No Cloudflare Access identity reached the bot"(误导)。直到手动刷新。
- **修复**:`reloadMe` 失败时退避重试,或 useStatus 的 `error` 由有变无时顺带 `reloadMe()`;`me === null` 时区分"加载中/失败"与 Guest。
- **置信度**:Confirmed。

### F11 · Low · 无防重复点击:Skip 连跳两首、双击创建同名歌单、重复导入(来源 worker FE-09)
- **位置**:webui/src/components/Controls.vue:52(skip,`control()` useStatus.ts:62 无 in-flight 保护);AddToPlaylist.vue:35-46 与 PlaylistsPage.vue:51-62(`newName` 在 await 之后才清空,:56/:40);PlaylistsPage.vue:120、:144(`importRunning` 要等第一次 `fetchImportJob` 后才为 true)。
- **问题**:双击 ⏭ → 两个 `POST /api/controls {action:'skip'}` → `_skip`(web_api.py:123-129)非幂等,连跳两首;创建歌单表单往返期间回车两次 → 两个同名歌单(组长核实 web_users.py:338-346 `INSERT INTO user_playlist` 无 name 唯一约束,成立);Import 在 `startImport` 返回前可再次提交 → 两个导入任务。
- **修复**:`control` 加 `inflight` 标志或对 skip 节流 300 ms;创建/导入按钮在请求期间 `:disabled`。
- **置信度**:Confirmed。

### F12 · Low · 切换页签销毁 LibraryPage,上传进度 UI 与取消入口丢失而上传继续后台跑(来源 worker FE-11;App.vue 行号已更正)
- **位置**:webui/src/components/LibraryPage.vue:99(`uploads` 为组件局部 ref)、:110-141(`onUpload` 无 `onBeforeUnmount` abort);webui/src/App.vue:100-104(`v-else-if` 切换,无 `<KeepAlive>`)、:21(`view` 内存状态)。
- **问题**:上传中切到 Search 再回来:`uploads` 已重置为空,async 循环仍在跑,既看不到进度也无法取消;完成后 `query(page.value)` 在已卸载组件上执行;关闭标签页则留下 F2 的残留。
- **修复**:上传队列改模块级单例(像 useStatus),或 `<KeepAlive>` 包裹视图;卸载/`beforeunload` 时 abort 并 DELETE。
- **置信度**:Confirmed。

### F13 · Low · 队列拖拽:乐观重排在 busy 时被静默丢弃;key 含下标导致整段重挂载;触屏不可用(来源 worker FE-12)
- **位置**:webui/src/components/QueueList.vue:34-36 + :63(`act` 在 `busy` 时直接 return)、:95(`:key="\`${item.id}-${i}\`"`)、:106(`draggable="true"`)。
- **问题**:`onDrop` 先 `items.splice` 乐观重排再 `await act(...)`,`act` 若 busy 直接 return——界面显示已移动但请求从未发出,直到 in-flight 操作结束后的 `reload()` 把顺序弹回;key 含 `i` 使任何前移/删除让其后所有行重新挂载(缩略图重请求、♡ 下拉被销毁);HTML5 drag&drop 在多数手机浏览器不触发,窄屏下只能靠 ⤴,无键盘替代。
- **修复**:busy 时排队或禁用拖拽;服务端 /api/queue 给每行返回稳定 `uid` 作 key;触屏加上下移按钮。
- **置信度**:Confirmed。

### B1 · Low · npm audit:4 high / 1 moderate(均非运行时暴露,建议升级锁文件)
- **位置**:webui/package-lock.json:1501(vue 3.5.40)、nanoid <3.3.18、postcss ≤8.5.22、source-map-js ≤1.2.1。
- **证据**:`npm audit` → `@vue/server-renderer <3.5.42 XSS (GHSA-g2v6-rqmx-r4w6)`,其余为构建期依赖的 DoS 类。本项目不做 SSR,worker 另核实 bundle 中 grep 不到 `renderToString`/server-renderer;`npm ci` 成功说明 lock 与 package.json 一致。
- **修复**:`npm audit fix`(package.json 的 `^3.5.39` 已允许升到 3.5.42)后重建并提交 dist。
- **置信度**:Confirmed。

### B2 · Info · `npm run dev` 下 Library 页不可用(proxy 只配了 /api)
- **位置**:webui/vite.config.ts:10-13;webui/src/components/LibraryPage.vue:33、:59、:72、:147 调用 `../library/info`、`../library`、`../post`。
- **问题**:dev 代理只转发 `/api`,上述旧接口打到 Vite 自己 → 404/返回 index.html,Library 页在开发服务器上永远"Nothing in the library matches."。生产由 Flask 直接托管无此问题。
- **修复**:proxy 增加 `/library`、`/post`,或把这三条迁到 web_api。
- **置信度**:Confirmed(配置)。

### B3 · Info · 构建与 dist 一致性、Flask 托管检查(通过;组长与 worker 独立验证结论一致)
- `npm ci && npm run build`(node 22.22 / vite 8.1.5 / vue-tsc 3.3.5 / TS 6.0)成功;构建产物 `index.html`、`assets/index-BjzSu08G.js`(149,228 B)、`assets/index-CFiRT3aR.css`(22,662 B)与提交的 webui/dist **逐字节一致**(`diff -rq` 为空,`git status` 干净),**dist 未过期,无需还原**。node_modules 位于 webui/node_modules(已被 webui/.gitignore 忽略,未 git add)。
- Dockerfile:4-9、:35 在独立 node stage 重新构建并覆盖 dist,镜像不会带旧产物。
- interface.py:266-304:`/` → dist/index.html,`/assets/<path>` → dist/assets,`/app`→302→`/app/`(旧挂载点保留),`/favicon.svg` 免鉴权、`/assets/*` 受 requires_auth。dist 用 `base: './'` 生成相对引用,两个挂载点都能加载。无客户端路由,**不需要 SPA fallback**(代价是无法深链到某个页签,刷新总回 Now Playing)。`send_from_directory` 挡住穿越:实测 `/assets/../index.html` → 404。
- 缓存头:Flask 默认对 index.html 与带哈希的 assets 都发 `Cache-Control: no-cache` + ETag(组长与 worker 均用 test client 实测,If-None-Match → 304),每次打开都要校验 149 KB JS;可对 `/assets/` 加 `max_age=31536000, immutable`。`/api/thumbnail` 为 `private, max-age=86400`(web_api.py:343)。属优化,不计缺陷。

### B4 · Info · 旧前端 web/ 仍被引用,但默认镜像里 `/legacy` 永远 404;web/ 在 Node ≥17 上无法构建(合并 worker C 节)
- **位置**:interface.py:75(template_folder)、:257-262(`_legacy_index`)、:274-277(`/legacy`)、:307(`/playlist`)、`/upload`、`/download`;Dockerfile.local:23(`node:14-bullseye-slim`)、:26-28;.drone.yml:24,104;README.md:125;web/package-lock.json:6253(webpack 5.6.0)。
- **事实**:web/ 不是死代码——`/legacy` 路由、Dockerfile.local、drone、README 都指向它;`/post`、`/library`、`/library/info` 更是**新 UI 的活依赖**(LibraryPage.vue:33/:59/:72/:147),删旧路由前必须先迁到 /api。但 `git ls-files web/templates` 只有两个 `*.template.html`,真正被服务的 `index.<lang>.html` 是 webpack + translate_templates.py 生成物且未提交,主 Dockerfile 不构建 web/ → 主镜像里 `/legacy` 只会返回 "Legacy interface not built on this host"。`/playlist`、`/upload`、`/download` 只有旧 jQuery 前端使用,新 UI 与 tests/ 均不引用。web/ 的 webpack 5.6.0 用 md4 哈希,Node ≥17(OpenSSL 3)抛 `ERR_OSSL_EVP_UNSUPPORTED`(组长在本机 Node 22 `crypto.createHash('md4')` 复现),只有 Dockerfile.local 钉死 Node 14 才能构建。
- **建议**:在 PROGRESS/README 标注;迁移 LibraryPage 到 /api 后整体删除 web/、`/legacy`、Dockerfile.local 与 .drone.yml 旧构建步骤;或补主 Dockerfile 构建。
- **置信度**:Confirmed。

### B5 · Info · 类型安全、XSS 面与其他小项(未发现缺陷;合并 worker FE-13、FE-14)
- `grep` 全 webui/src:无 `any`/`as any`/`@ts-ignore`、无 `v-html`/`innerHTML`、无 `window.open`/`location.`;唯一动态 `:href` 在 CachePage.vue:198 且带 `rel="noopener noreferrer"`,其 URL 来自 music_db 的 url 条目(bot/cache_store.py:162),所有入口都经 `util.get_url_from_input`(util.py:314-329 只放行 http/https)或 web_api.py:301 / web_users.py:638、:723 的 `startswith(('http://','https://'))`,现实中 `javascript:` 进不了库;worker 建议防御性加 `/^https?:\/\//i.test(e.url)` 再渲染 `<a>`,以防将来新增入口忽略 scheme。`<img :src>` 来自搜索缩略图(http)与 `data:` 缩略图,`referrerpolicy="no-referrer"`。
- 响应校验:api.ts 的泛型只是编译期断言,`rv.json()` 结果不做运行时校验;SearchPage.vue:48-51、LibraryPage.vue:34/:60、StatsPage.vue:29、upload.ts:48/66 绕过 api.ts 直接 fetch,`data` 为隐式 `any`;StatsPage.vue:58-59、LibraryPage.vue:254 对字段缺失会 TypeError(仅前后端版本不一致时触发)。
- Cloudflare Access 会话过期:fetch 跟随 302 到跨域 `<team>.cloudflareaccess.com` → CORS 失败 → 永远 "Can't reach the bot: Failed to fetch"(worker 的判断比组长原先写的"Unexpected token <"更准确,采用),无"请重新登录/刷新"提示(api.ts:60-64)。连接错误只在 Now Playing 页显示(NowPlaying.vue:160-162),其它页签断连无提示;control 失败文案把 4xx 说成"连不上"。
- App.vue:28/:31/:41 `localStorage` 未包 try/catch(Safari 隐私模式抛 SecurityError,仅控制台报错);主题在 onMounted 后才应用,偏好 light 而系统 dark 时首屏闪烁。rAF 循环(useStatus.ts:40)播放中每帧触发重渲染,手机耗电偏高。SearchPage `added[key]==='done'` 永久禁用按钮(:145),同一结果页内无法再点同一首。上传进度按 32 MB 分片粒度更新,小于 32 MB 的文件进度条 0→100% 跳变。web_upload.py:187 `error=str(e)[:200]` 原样显示到 UI(LibraryPage.vue:135),可能带出服务器本地路径(仅已认证用户)。
- 轮询其余检查:`useStatus` 顺序 await 不会堆积请求;`QueueList`/`SettingsPage`/`UserChip` 的 setInterval 都在 `onBeforeUnmount` 清理;`PrepPanel` 的 rAF 已 cancel;上传按 `file.slice` 分片不整文件入内存;`PlaylistsPage.runImport` 的 `for(;;)` 轮询无取消但有 done/error 终止条件。
- **跨组提示(后端组范围,组长已核实成立)**:interface.py:640-641 `library_info` 的 `var.bot.is_admin(user)` 用的是模块全局 `user`(interface.py:78),该变量在 `requires_auth` 里被 `global user` 逐请求改写(:129/:139/:157/:163),多线程 Flask 下会串用户。

## 剔除/降级记录

- worker FE-02 严重度 Medium **采纳**(组长原 F2 为 Low):默认 4G 上限 + 默认开启 + 24 h prune 窗口,任何已登录用户可在一天内占满磁盘,理由充分。
- worker FE-05 **合并入 F1**(重复,组长保留 Medium;worker 的 visibilitychange 建议并入)。
- worker FE-07、FE-10 **合并入 F3**(重复;组长原 F3 的 PlaylistsPage 行号 81-87/177-181 **写错**,以 worker 的 77-84/169-173 为准,已更正)。
- worker FE-13 **降为 B5 内的防御性建议**:当前所有入口均校验 http(s) scheme,无利用面,不单列。
- worker FE-14 **并入 B5**(Info 合集);其中 Cloudflare 302 的表现以 worker 的 "CORS → Failed to fetch" 为准,替换组长原描述。
- worker C 节 **并入 B4**:webpack 5.6.0 / md4 / Node 14 事实已在本机复现。
- worker B 节"API 基址 `..` 在反代前缀下也成立"**不采纳**:`new URL('../api/x', 'https://h/myprefix/')` 解析为 `https://h/api/x`,维持组长 F5。
- worker 行号更正:FE-01 "top" 在 QueueList.vue:147(非 146);FE-11 引用的 App.vue:513-523/:439 不存在(文件仅 108 行),实为 :100-104/:21;FE-02 的 DELETE 路由 web_upload.py:292-301 正确,组长原写 285-292 有误,已更正。
- 组长自查剔除(与第一版相同):"测试间 sqlite/临时目录未清理"(11 个 TemporaryDirectory 均在 tearDown cleanup)、"upload.ts 轮询未知 id 死循环"(`_load` abort(404) 返回 HTML,`.json()` 抛错即退出)、"24 h prune 删掉处理中文件"(概率可忽略)、"playlist.py:42 裸 raise"(白名单保护,pyflakes 级)、"Vue server-renderer XSS"(SSR 未使用,降为 B1 升级建议)、"App.vue localStorage 未 try/catch"(并入 B5)。

## 覆盖范围声明

- **组长看了**:tests/ 全部 21 个文件的 setUp/tearDown 与断言结构(AST 脚本 scan_tests.py 扫描 253 个用例;逐文件独立运行 + 反序全量运行;`-W error` 定位线程警告);完整阅读 test_web_api.py、test_single_loop.py、test_player.py、test_prefetch.py、test_commands_wiring.py、test_stability.py:60-135、test_sponsorblock.py:140-175、test_play_history.py:118-152、test_livestream.py:1-70;源码 media/playlist.py:14-110/240-300、media/cache.py CachedItemWrapper.item、bot/player.py:202-237、web_api.py 路由骨架与 queue/skip 段、web_upload.py 全部路由、web_users.py:338-346、interface.py:29-160/240-310/636-642、util.py:314-329。
- **前端**:webui/src 全部 20 个文件通读(api.ts、upload.ts、两个 composable、App.vue、13 个组件),webui/index.html、vite.config.ts、package.json、package-lock.json 关键条目、dist/index.html、web/package-lock.json webpack 条目;Dockerfile、Dockerfile.local、.drone.yml、deploy/WEBUI.md 相关行。worker 报告 14 条逐条 grep 核对行号。
- **没看 / 未做**:未在浏览器里真实跑页面(无 bot 实例);未验证 Cloudflare Access 302 行为(按 fetch/CORS 规范推断);未逐条审 commands/*、media/spotify.py 等低覆盖模块的业务逻辑(属其他组范围,这里只报覆盖率);未跑 Windows;未审 web/js 旧前端源码。
- 产物:coverage.txt、build.log、scan_tests.py、dist-committed/ 备份均在 scratchpad/audit 或 scratchpad 下;仓库未改动(`git status` 为空)。
