# 数据层、文件清理与导入组 审计报告

组长:Fable 5.1。本会话没有 Agent 工具(ToolSearch 查无 Agent/Task),范围内全部文件由组长亲自通读并复现;之后协调方另派一名 Opus 工人独立复审 cleanup/database(报告 `audit/reports/workers/cleanup-db-worker.md`),其新报 9 条我逐条核对行号、亲自运行其用例(`cleanup-worker/repro_deep_cleanup.py`,9/9 复现)与探针脚本后全部采纳,下文标注 **[来源:worker]**。
复现脚本:`audit/repro/data/repro_cleanup_data.py`(组长,9 用例 8 失败 1 通过)与 `audit/repro/data/repro_deep_cleanup.py`(worker,9 用例 9 失败),运行方式 `cd <repo-root> && python -m pytest -q -p no:cacheprovider <文件>`;每个用例断言的是"安全行为",**失败 = 复现成功**。仓库未做任何修改(`git status` 干净)。

## 总评

整体健康度:**中等偏好**。SQL 层没有注入面(所有用户值都走 `?` 占位,ORDER BY / 列名全部硬编码);sqlite 采用"每次调用开一条连接、用完即关"的模式,没有跨线程共享连接的问题;web_users 的持久化(用户/别名/绑定码/歌单)写得规矩(`_Closing` 提交/回滚、`secrets` 生成绑定码、锁定重试);playlist_import 的所有 `requests` 调用都带 timeout,外部 JSON 结构变化会被 `run_import` 的兜底 `except` 变成 `list_failed` 而不是崩线程;yt-dlp 设了 `updatetime: False`,清理用 mtime + 1 小时 MIN_AGE 兜底,时钟回拨安全;播放中/队列中/下载中的文件都受保护;spotdl 子进程用列表参数 + `--` 分隔,无 shell 注入,下载产物限定在 `req_*` 子目录并按 basename 搬出,无目录穿越。

最该先修的三件事(两个主题):

**主题 A:旧 `/library` delete 接口(interface.py:685-699)是整个数据层唯一能一次删光曲库的入口。** 它由上游 jQuery 旧 UI(`/legacy`)使用,默认 `delete_allowed = True`,任何过了 Web 鉴权的人都能 POST。同一段代码里有三个相互叠加的缺陷:(1) **DL-1(High,worker)** 过滤条件在类型含 url 时静默丢掉目录条件、目录 LIKE 不转义 `_`/`%` 且大小写不敏感,"删一个目录"实际删整库或兄弟目录;(2) Web 组已确认的 `os.rmdir(var.music_folder + payload['dir'])`(:698-699)可用 `../` 穿越删曲库外的空目录;(3) **DL-2(Medium,worker)** 同一文件里的 `/post delete_item_from_library`(:496-505)根本不看 `delete_allowed`。修复应一并做:delete 动作强制 `type == 'file'` 且 `dir` 非空、LIKE 加 ESCAPE、`rmdir`/`remove` 前 `realpath` 并确认在 `music_folder` 内、两个入口都检查 `delete_allowed`;更稳妥的是在新 UI 已覆盖功能后直接下线旧接口。

**主题 B:cleanup 的 Spotify 分支与 `media/spotify._prune_cache` 没有继承 tmp_folder 分支的"只删确定是我们的文件"原则。**
1. **F1(High)** `_sweep_spotify_folder` 把 `download_folder` 里"任何超过 keep_days 的普通文件"都删;只要管理员把 `download_folder` 设成 `/tmp/`(与默认 `tmp_folder` 相同)、`.`、`~` 之类非专用目录,就会删别的进程的文件、配置文件、以及本应受 pin/常听保护的缓存条目。正常配置下它也不看播放记录(**CL-6**,worker)。
2. **F2(Medium)** `_prune_cache` 在每次 spotdl 下载时按容量删最旧文件,完全没有 music_folder 重叠保护,`download_folder` 放进曲库就会删曲库。
3. **F3(Medium)** 三处 music_folder 保护都用 `abspath` 字符串比较,符号链接可绕过。

其次是 **RACE-1(Medium,worker)**:Cache 页按提示"强制删除队列中的缓存"后,若当前曲处于已停止状态,恢复播放会永久卡在等待——这推翻了我初稿里对"删除与播放竞态"的整体剔除(正在播放时仍安全,见剔除记录改写);以及 **DB-1(Medium,worker,与命令组重复)** `!dropdatabase` 之后直到重启整个 bot 处于损坏状态。

计数:High 2(F1、DL-1),Medium 6(F2、F3、F4、DL-2、RACE-1、DB-1),Low 11(F5–F10、CL-4–CL-7、DB-2),Info 若干。

## 发现列表

### F1 · High · Spotify 清理分支删除 download_folder 内任意普通文件,绕过 tmp_folder 的全部保护
- **位置**:`bot/cleanup.py:234-262`(`_sweep_spotify_folder`),对比 `bot/cleanup.py:180-232`(`_sweep_tmp_folder`);配置默认值 `configuration.default.ini:75`(`tmp_folder = /tmp/`)、`:123`(`download_folder = spotdl_cache/`)。
- **问题**:tmp_folder 分支只删 32-hex 名或 DB 里登记过的路径,并跳过 pinned / 常听 / 最近播放过的条目;Spotify 分支对 `download_folder` 里**所有**普通文件只看 mtime,没有"确实是我们下载的"判定,也没有 pin / 常听 / `known_paths` 保护,更没有"download_folder 与 tmp_folder 相同"的检查。
- **证据**(`bot/cleanup.py:248-261`):
  ```python
  for name in names:
      path = os.path.join(folder, name)
      if not os.path.isfile(path) or os.path.islink(path):
          continue
      if name.lower().endswith(PARTIAL_SUFFIXES):
          continue
      if os.path.abspath(path) in protected:
          continue
      if not self._is_expired(path, now):
          continue
      if self._remove(path):
  ```
  复现(`repro_cleanup_data.py::SpotifyFolderEqualsTmpFolder`,把 `[spotify] download_folder` 设为 tmp_folder):
  - `some-other-app.sock`(30 天)被删 → `test_foreign_tmp_file_is_never_touched` 在 tests/test_cleanup.py 里保证的"不碰别人的 /tmp 文件"被 Spotify 分支推翻;
  - `cache_store.set_pinned()` 固定的 hex 条目(30 天)被删;
  - 播放 5 次的常听条目(20 天前)被删。
  变体(`::SpotifyFolderIsArbitraryDir`):`download_folder` 指向含 `configuration.ini`(30 天)/`mumbleBot.py`(90 天)的目录,两者都被删。`util.solve_filepath('.')`(`util.py:31-41`)对存在的相对路径原样返回,所以 `download_folder = ./` 就是 CWD。
- **影响**:管理员配置错误(最自然的错法就是把两个"下载缓存"目录设成同一个 `/tmp/`)即导致:Docker 里以 root 删掉容器内 /tmp 所有 7 天以上的文件;裸机部署删掉其他用户/进程的 /tmp 文件;用户在 Cache 页 pin 住的条目和常听条目被周期性清掉(用户感知为"pin 没用")。每 `cleanup_interval_days`(默认 7 天)触发一次,启动时若已到期立刻触发。
- **修复建议**:(1) 在 `_sweep_spotify_folder` 开头增加 `if folder == os.path.abspath(var.tmp_folder): return []`(或当两者相同时复用 tmp 分支的保护集合);(2) 候选文件限定为 `name.lower().endswith(media.spotify._AUDIO_EXTENSIONS)` 且(更严格)其绝对路径出现在 music_db 中 `type='file'` 且 `os.path.isabs(path)` 的记录里(这正是 `_invalidate_db_records` 用来识别 Spotify 记录的条件);(3) 把 `pins` / `usage` / `keep_plays` 的判定抽成公共函数,两个分支共用。
- **置信度**:Confirmed(4 个用例复现)。

### DL-1 · High · 旧 `/library action=delete` 的删除范围远大于用户选择的过滤条件(可删整库) **[来源:worker]**
- **位置**:`interface.py:598-604`(`build_library_query_condition`),`interface.py:685-699`(delete 分支);`configuration.default.ini:44`(`delete_allowed = True`);旧 UI `web/js/main.mjs:636-646,709-719`。
- **问题**:(a) 只有 `form['type'] == 'file'` **严格相等**时才加目录条件;旧 UI 可同时按下 file+url 两个过滤按钮(`type=file,url`),此时目录被静默忽略,删掉**所有**本地文件;(b) 目录条件是 `path LIKE '<dir>/%'`,`_`/`%` 不转义,且 SQLite LIKE 对 ASCII 不区分大小写,删 `my_band/` 也删 `myXband/`、`MY_BAND/`;(c) 末尾 `os.rmdir(var.music_folder + payload['dir'])` 原样拼接请求参数——Web 组已确认可用 `../` 穿越删曲库外空目录(跨组事实),且目录不存在时抛 500,但此时文件已删完。
- **证据**(`interface.py:598,604,692-694`):
  ```python
  if form['type'] == 'file':
      ...
      condition.and_like('path', folder + '%')
  ...
  if os.path.isfile(item.uri()):
      os.remove(item.uri())
  ```
  复现 `cleanup-worker/repro_deep_cleanup.py::LegacyLibraryDelete`(真实 Flask 路由 + 真实 sqlite + 真实 MusicCache,组长亲自运行):`type='file,url', dir='Rock'` → `Rock/a.mp3` **和** `Jazz/b.mp3` 都被删;`type='file', dir='my_band'` → `myXband/d.mp3`、`MY_BAND/e.mp3` 也被删(survivors 0/2)。
- **影响**:任何通过 Web 鉴权的用户(Web 组 WEB-06:默认 `auth_method=none`)在旧 UI 正常操作或直接发一个 POST,即可**不可逆**删除整个本地曲库;普通用户也会误删(选"本地+URL"再挑目录点删除 → 全库消失)。
- **修复建议**:`build_library_query_condition` 改为 `if 'file' in types and form.get('dir')` 时加目录条件;delete 动作**要求** `type == 'file'` 且 `dir` 非空;LIKE 改为 `path LIKE ? ESCAPE '\\'` 并先转义 `\ % _`(或 `substr(path,1,len)=?` 精确前缀 + Python 侧 `os.path.commonpath` 二次校验);`rmdir` 前 `realpath` 并确认在 `music_folder` 内。
- **置信度**:Confirmed(组长复跑 2 用例)。

### F2 · Medium · `media.spotify._prune_cache` 按容量删文件时没有任何 music_folder / 身份保护
- **位置**:`media/spotify.py:127-192`(`_prune_cache`),调用点 `media/spotify.py:295-297`(`download_tracks`,每次 `!spotify` 下载前执行)。
- **问题**:`_prune_cache` 列出 `folder` 内所有普通文件按 mtime 从旧到新删到 `max_cache_size`(默认 2048 MB)以下,只跳过当前队列引用的路径;不检查 folder 是否在 music_folder 内/等于 music_folder、不限定扩展名、不跳过符号链接、不同步 DB。`bot/cleanup.py:238-241` 对同一目录专门写了"refuse to sweep anything that overlaps the user's library",说明作者也认为这是必须防的情况,但 `_prune_cache` 漏掉了。
- **证据**(`media/spotify.py:181-189`):
  ```python
  files.sort(key=lambda entry: entry[2])  # oldest first
  for path, size, _ in files:
      if total <= limit:
          break
      if os.path.abspath(path) in protected:
          continue
      try:
          os.remove(path)
  ```
  复现 `repro_cleanup_data.py::PruneCacheHasNoLibraryGuard::test_prune_inside_music_folder_deletes_library_files`:folder = `music_folder/spotify`,两个 2 MB 的 .flac,上限 3 MB → 老的曲库文件被删。
- **影响**:管理员把 `download_folder` 放进曲库(例如想让 spotdl 下的歌直接进库、被 `build_dir_cache` 扫到)后,曲库该目录超过 2 GB 时,每次 `!spotify` 都会删最旧的曲库文件;任何能用 `!spotify` 的 Mumble 用户都能触发。另:被删文件对应的 music_db `file` 记录不会被清理,`FileItem.validate` 报 `file_missed` 后由 playlist 校验线程 `free_and_delete`。
- **修复建议**:在 `download_tracks` 解析完 `download_folder` 后、调用 `_prune_cache` 前,复用 cleanup 的重叠判定(建议抽到 util:`realpath` + `os.path.commonpath`),重叠则跳过 prune 并 `log.warning`;`_prune_cache` 内只统计/删除 `_AUDIO_EXTENSIONS` 结尾且非符号链接的文件。
- **置信度**:Confirmed。

### F3 · Medium · music_folder 重叠保护用 `abspath` 字符串比较,符号链接可绕过
- **位置**:`bot/cleanup.py:159`(`_spotify_folder` 用 `abspath`)、`:167`(music_folder)、`:184,187-188`(tmp 分支)、`:238-239`(spotify 分支)。
- **问题**:`os.path.abspath` 不解析符号链接;`tmp_folder` 或 `download_folder` 是一个指向曲库(或其子目录)的 symlink 时,`folder == music_folder` / `startswith(music_folder + os.sep)` 全部为假,保护失效。文件级的 `os.path.islink(path)` 检查只能挡住目录里的链接文件,挡不住目录本身是链接。
- **证据**:`repro_cleanup_data.py::SymlinkBypassesMusicFolderGuard` 两个用例:`download_folder -> music_folder` 时 365 天的 `My Song.flac` 被删;`tmp_folder -> music_folder` 时曲库里 32-hex 名的文件被删(后者需要文件名恰好是 hex,危害小;前者叠加 F1 危害大)。
- **影响**:NAS 上用 symlink 组织目录很常见(例如 `/volume1/music/spotify -> /volume1/@downloads/spotdl`,或反过来);一旦 download_folder 解析到曲库内,F1 的"删任意旧文件"就落到曲库上。
- **修复建议**:三个目录统一 `os.path.realpath()` 后再比较,并用 `os.path.commonpath([a, b]) == a` 代替 `startswith(a + os.sep)`;`protected_paths()` 与候选 `path` 也统一 realpath,避免队列里引用 symlink 路径时保护失配。
- **置信度**:Confirmed。

### F4 · Medium · 歌单导入作业无并发/频率限制,可被任意 Web 用户用来耗尽 CPU 并触发 YouTube 限流
- **位置**:`playlist_import.py:400-410`(`start_import` 每次 POST 直接 `threading.Thread(...).start()`),`:458-459`(每个作业 4 个 worker),`web_users.py:632-651`(`/api/playlists/import`,只要 `requires_auth`)。
- **问题**:没有"每用户同时一个作业"、没有全局并发上限、没有冷却;`_jobs` 1 小时后被踢出字典(`:403-404`)但线程照跑。一个作业最多 1000 首 × 最多 3 次 `ytsearch6`(`:368-370`)。对比聊天侧的 `!spotify` 有 `_spotify_download_sem` + 5 秒冷却(`commands/streaming.py:21-28`),Web 侧的导入是同类重操作却没有任何限制。
- **证据**:
  ```python
  threading.Thread(target=run_import, args=(job_id, owner, url, playlist_id, name),
                   name="PlaylistImport", daemon=True).start()
  ```
  任何通过 `requires_auth` 的身份循环 POST `{"url": "<任意 1000 首的 QQ/网易歌单>"}` 即可。
- **影响**:每个作业持续一两分钟的 yt-dlp 搜索(作者注释:"几百首的匹配要一两分钟");几十个并发作业足以把小 NAS 的 CPU 打满、拖慢正在播放的 ffmpeg,并让 YouTube 对机器人出口 IP 限流/验证码,进而影响正常点歌。
- **修复建议**:`start_import` 里用 `threading.Semaphore(2)` 限制全局并发,并拒绝同一 owner 已有 `status in ('listing','matching')` 的作业(返回 429);`_jobs` 踢出前先给作业打 `cancel` 标记,`resolve()` 看到后提前返回。
- **置信度**:Likely(代码路径无歧义,未实际压测)。

### DL-2 · Medium · `/post delete_item_from_library` 不检查 `delete_allowed`,管理员关闭删除后仍可逐个删曲库文件 **[来源:worker]**
- **位置**:`interface.py:496-505`;对比 `interface.py:686`(`/library` 检查了开关)。
- **证据**:
  ```python
  elif 'delete_item_from_library' in payload:
      _id = payload['delete_item_from_library']
      var.playlist.remove_by_id(_id)
      item = var.cache.get_item_by_id(_id)
      if os.path.isfile(item.uri()):
          os.remove(item.uri())
  ```
  复现 `repro_deep_cleanup.py::DeleteItemIgnoresDeleteAllowed`:`delete_allowed=False` 时 POST `{'delete_item_from_library': md5('Album/track.flac')}` 后文件已删除。id = `md5(相对路径)`,可经 `/library action=query` / `/api/library` 枚举。另 id 不存在时 `item` 为 None → AttributeError 500。
- **影响**:`delete_allowed=False` 只是隐藏了 UI 按钮,任意 Web 用户仍可逐个删除曲库文件。
- **修复建议**:分支开头 `if not var.config.getboolean('bot','delete_allowed'): abort(403)`;`item is None` → 404;删除前确认 `realpath(item.uri())` 位于 `music_folder` 或 `tmp_folder` 内。
- **置信度**:Confirmed(组长复跑)。

### RACE-1 · Medium · 当前曲在"已停止"状态下缓存被强制删除后,恢复播放永久卡住(不会重新下载) **[来源:worker]**
- **位置**:`bot/player.py:906-913`(`stop` 设 `wait_for_ready=True`)、`:934-946`(`resume`:`is_ready()` 为假时只把 playhead 归零后 return,不调用 `start_download`)、`:774-798`(等待分支只设 `_loop_status`,不发起下载);下载只在 `:760`(`next()`)、`:893`(`play(index)`)和 `:318-360`(对"下一首"的预取)发起;删除方 `bot/cache_store.py:236-254`(`delete_entry(force=True)` 允许删队列中条目)、`web_cache.py:42-50`;UI 承诺 `webui/src/components/CachePage.vue:83`("It will be downloaded again when it plays")。
- **问题**:当前曲停止前已 ready,之后文件被删,`URLItem.is_ready()` 把 ready 改为 `validated` 并返回 False;此后 `_loop_iteration` 每轮只走到 `'Wait for the next item to be ready'`,`!play`(无参,`commands/playback.py:52`)与 Web resume(`web_api.py:205`)都走 `resume()` 的早退分支,没有任何代码再为它发起下载。
- **证据**:`repro_deep_cleanup.py::StopDeleteResumeStall`(真实 `PlayerMixin._loop_iteration` + 真实 `URLItem`/`CachedItemWrapper`/`cache_store.delete_entry`):`stop()` → `delete_entry(id, force=True)` → `resume()` → 50 轮循环既无 `async_download` 也无 `launch_music`,状态停在 `'Wait for the next item to be ready'`。组长复跑并逐行核对了 player.py 的三个下载触发点,确认无其他路径。
  同一结局的其他触发:cleanup / evict 的保护快照(`cleanup.py:166` vs `:230`,`cache_store.py:259` vs `:276`)与逐个删除之间没有复查,窗口内被加入队列并成为当前曲的条目会落到同一分支(Likely,毫秒到秒级窗口)。
- **影响**:用户按 UI 提示强删后点继续,机器人一直静音,直到有人 `!play <index>` / 跳歌。与 UI 承诺相反。
- **修复建议**:`resume()` 早退分支里对 `music_wrapper` 调用 `self.start_download(music_wrapper)` 并设 `wait_for_ready=True`;更稳的是在等待分支里当 `not current.is_ready() and not item.downloading` 时 `self.async_download(current)`(自愈);cleanup/evict 在 `_remove` 前重新判断一次 `protected`。
- **置信度**:Confirmed(stop/force-delete/resume 路径);快照 TOCTOU 为 Likely。

### DB-1 · Medium · `!dropdatabase` 之后直到重启整个 bot 处于损坏状态 **[来源:worker;与命令组重复,跨组]**
- **位置**:`commands/library.py:307-316`;`database.py:272-276,492-496`(`drop_table` 只 DROP 不重建)。
- **证据**:
  ```python
  var.db.drop_table()
  var.db = SettingsDatabase(var.settings_db_path)
  var.music_db.drop_table()
  var.music_db = MusicDatabase(var.settings_db_path)   # 应为 music_db_path
  ```
  复现 `repro_deep_cleanup.py::DropDatabase` 与 `cleanup-worker/drop_db_probe.py`(组长复跑):`music_db path -> .../settings.db`;之后 `db.set/get/has_option` → `no such table: botamusique`,`music_db.query/insert` → `no such table: music`,`cleaner.seconds_until_due()` 抛 OperationalError(`_loop` 在 try 外调用 → 清理线程退出)。
- **影响**:管理员执行后,调音量、`URLItem.validate`(`has_option('url_ban')`)、加歌保存、Web 状态全部抛错,清理线程死亡;重启后迁移重建表才恢复。
- **修复建议**:`var.music_db = MusicDatabase(var.music_db_path)`;紧接 `DatabaseMigration(var.db, var.music_db).migrate()`;`var.cache.db = var.music_db` 并 `var.cache.free_all()`;`CacheCleaner.last_run` 捕获 `Exception`。
- **置信度**:Confirmed。

### F5 · Low · 删除缓存文件后 `url_from_playlist` 记录不会被重置为 validated
- **位置**:`bot/cleanup.py:283-284`(只查 `type='url'`/`'file'`)、`bot/cache_store.py:225`(只处理 `type == 'url'`);`_sweep_tmp_folder:192-193` 的 `known_paths` 同样只取 `url`。
- **问题**:YouTube 播放列表展开的条目 type 是 `url_from_playlist`(`media/url_from_playlist.py:109`),文件同样放 tmp_folder、hex 命名,会被删,但 DB 里 `ready` 仍是 `yes`。
- **证据**:`repro_cleanup_data.py::UrlFromPlaylistNotInvalidated` —— 文件被删后 `query_music_by_id()['ready'] == 'yes'`。
- **影响**:自愈:`URLItem.is_ready()`(`media/url.py:88-93`)和 `validate()`(`:108`)发现文件不在会自行回到 `validated` 并重新下载,所以只是 DB 与磁盘短暂不一致、多一条 "music file missed" 日志、Cache 页/统计可能把它当"已缓存"。
- **修复建议**:两处 Condition 加 `.or_equal('type', 'url_from_playlist')`,`cache_store.invalidate` 的判断改为 `record.get('type') in ('url', 'url_from_playlist')`。
- **置信度**:Confirmed。

### F6 · Low · `insert_music` 的 SELECT-then-INSERT 不是原子操作
- **位置**:`database.py:306-321`。
- **问题**:先 `SELECT 1 FROM music WHERE id=?` 再决定 INSERT/UPDATE,两条语句之间没有事务(SELECT 不开启隐式事务);两个线程对同一个尚未入库的 id 同时首次保存(playlist 校验线程的 `lib.save` + Web 上传/标签编辑)会让后者 `sqlite3.IntegrityError: UNIQUE constraint failed: music.id` 抛到调用线程。
- **证据**:代码路径无歧义;用 4 线程 × 200 次同 id 插入压测(`audit/repro/data/race_dump.py`)0 错误、0.5 s 完成,窗口只有微秒级,实际触发概率低。
- **影响**:偶发一次保存失败并打印线程异常(startup 的 excepthook 只记日志,不会崩),下次保存会成功。
- **修复建议**:改为单条 `INSERT INTO music (...) VALUES (...) ON CONFLICT(id) DO UPDATE SET type=excluded.type, ...`(sqlite ≥ 3.24),去掉前置 SELECT;`create_at` 自然保留。
- **置信度**:Likely。

### F7 · Low · 设置库 1→2 迁移用 f-string 拼 ATTACH 路径
- **位置**:`database.py:629`。
- **证据**:`cursor.execute(f"ATTACH DATABASE '{self.music_db.db_path}' AS music_db")`。
- **影响**:music_db 路径含 `'` 时迁移抛 `OperationalError`,启动失败;只影响仍是 settings 版本 1(2020 年前上游)且 `music.db` 不存在的实例,路径来自配置/命令行,不是攻击面。
- **修复建议**:`cursor.execute("ATTACH DATABASE ? AS music_db", (self.music_db.db_path,))`。
- **置信度**:Confirmed(读码)。

### F8 · Low · `save_music_library` 用 `config.get` 判断,字符串 "False" 为真,`:memory:` 分支是死代码
- **位置**:`bot/startup.py:164-167`、`bot/player.py:616-617`;注释 `bot/startup.py:175-176` 与 `database.py:688-706`(`PlayHistoryDatabase` 每次 `sqlite3.connect(self.db_path)`)。
- **问题**:`var.config.get("bot", "save_music_library")` 返回 `'False'`,非空字符串为真,所以永远走磁盘库;配置项实际无效。若有人把它改成 `getboolean` "修好",`PlayHistoryDatabase(":memory:")` 会在每次 `record()`/`usage()` 时连到一个全新的空内存库 → `no such table: play_history`(`record` 被 player 的 try/except 吞掉,统计页则 500);`var.user_db` 的注释已注意到这一点但 play_history 没有。
- **修复建议**:改 `getboolean`,并让 `PlayHistoryDatabase` 在 music_db 为内存库时落到 `var.settings_db_path`(与 `user_db` 一样)。
- **置信度**:Confirmed(读码)。

### F9 · Low · `/download?id=<不存在>` 返回 500 而非 404
- **位置**:`interface.py:815-816`。
- **证据**:`dicts_to_items(var.music_db.query_music(Condition().and_equal('id', request.args['id'])))[0]` —— 空列表下标 IndexError。
- **影响**:已登录用户用任意 id 请求即得 500(栈回溯进日志);无数据泄露。
- **修复建议**:`items = ...; if not items: abort(404)`。
- **置信度**:Confirmed(读码)。

### F10 · Low · `build_dir_cache` 会删掉所有绝对路径的 `file` 记录(Spotify 下载登记的记录)
- **位置**:`media/cache.py:118-122`;触发点 `bot/startup.py:210-211`(`refresh_cache_on_startup`)、`commands/library.py:321`、`commands/sources.py:76,111`、`interface.py:537`。
- **问题**:`files` 是 `music_folder` 的相对路径列表,而 Spotify 下载经 `get_cached_wrapper_from_scrap(type='file', path=<绝对路径>)`(`commands/streaming.py:156,246`)建成的 FileItem 一旦入库(加标签 / `validate` 时 duration==0 触发 `version += 1`)就是绝对路径,`result['path'] not in files` 恒为真 → 每次 recache 都把这些记录连同标签删掉;`bot/cleanup.py:292-297` 与 `tests/test_cleanup.py:insert_file_record` 又明确把"绝对路径 file 记录"当作合法的 Spotify 缓存记录,两处语义冲突。
- **影响**:用户给 Spotify 下载的歌打的标签在重启(默认 `refresh_cache_on_startup`)后丢失;文件本身不受影响。
- **修复建议**:`build_dir_cache` 的删除循环跳过 `os.path.isabs(result['path'])` 的记录(与 cleanup 的判定对齐)。
- **置信度**:Likely(读码,未跑端到端)。

### CL-4 · Low · 32-hex 文件名不能证明文件属于机器人:共享 /tmp 里外来的 `<32hex>[.ext]` 会被清理,也会被容量淘汰立即删除 **[来源:worker]**
- **位置**:`bot/cleanup.py:216-217`(`base = name.split('.',1)[0]`,任意扩展名放行);`bot/cache_store.py:126-137`(`scan_files` 同规则)、`:257-283`(`evict` 无 MIN_AGE,每次 URL 下载前后由 `media/url.py:268` 调用);默认 `tmp_folder = /tmp/`。
- **证据**:`repro_deep_cleanup.py::ForeignHexFiles`(组长复跑):30 天前的 `<md5>.tar.gz` 被 `run_once()` 删除;刚创建的外来 `<md5>` 文件在 `tmp_folder_max_size=0` 时被 `enforce_size_limit()` 立即删除。外来文件还计入缓存总量。
- **影响**:非 Docker、默认 `/tmp/` 时,同 uid 其他程序以 md5 命名的临时文件会被删。`tests/test_cleanup.py:113` 只覆盖了非 hex 外来文件。
- **修复建议**:候选条件改为"basename 是 `<id>` 或 `<id>.jpg`/已知后缀 **且** `<id>` 存在于 music_db(`type in ('url','url_from_playlist')`)";`evict` 排除 mtime < MIN_AGE 的文件。
- **置信度**:Confirmed。

### CL-5 · Low · 下载崩溃留下的孤儿半成品(`<id>.part` / `.incomplete`)永不回收且永久计入容量 **[来源:worker]**
- **位置**:`bot/cleanup.py:214-215`(PARTIAL 后缀无条件跳过);`bot/cache_store.py:155,174`(有 partial 即标记 `downloading`)、`:264`(evict 跳过 downloading)。
- **证据**:`repro_deep_cleanup.py::OrphanPartials`(组长复跑):30 天前的 `<md5>.part`、无任何条目在下载,清理与 `tmp_folder_max_size=0` 淘汰后依然存在。
- **影响**:kill / 看门狗重启 / 断电留下的 `.part`(可达数百 MB)永久占盘并挤掉正常缓存;Cache 页显示为"下载中"。
- **修复建议**:半成品判定改为"有 partial 后缀 **且**(`id` ∈ `downloading_ids()` 或 mtime 在 1 天内)";超期且无下载线程的 partial 当普通过期文件处理。
- **置信度**:Confirmed。

### CL-6 · Low · Spotify 缓存不看播放记录/常听次数,昨天播过 5 次的歌也会连记录和标签一起删 **[来源:worker]**
- **位置**:`bot/cleanup.py:234-262`(无 `usage`/`keep_plays`/`last_played` 判断)、`:310-319`(`delete_music`);文档 `configuration.example.ini:100-101` 明确承诺"keep cached files played … within the last N days (pinned and frequently played items are always kept)"且覆盖 Spotify download_folder。
- **证据**:`repro_deep_cleanup.py::SpotifyIgnoresPlayHistory`(组长复跑):mtime 10 天前、昨天播放 5 次(`play_history.item_id = md5(path)`)、带标签 `favourite` 的 Spotify 文件被删,`query_music_by_id` 返回 None。
- **影响**:正常配置下(与 F1 的误配不同),常听 Spotify 歌每 keep_days 删一次、重下一次,标签随之丢失。
- **修复建议**:与 F1 建议 (3) 相同——把 tmp 分支的 `usage`/`keep_plays`/`last_played` 判断抽成公共函数,Spotify 分支用 `md5(path)` 查同一份 usage。
- **置信度**:Confirmed。

### CL-7 · Low · "存入曲库"对较长 CJK 标题必然失败(ENAMETOOLONG) **[来源:worker]**
- **位置**:`bot/cache_store.py:318-321`(`[:120]` 按字符截断)、`:340-348`。
- **证据**:`cleanup-worker/probe_misc.py`(组长复跑):90 个汉字标题 → `OSError [Errno 36] File name too long`;`web_cache.py:78-82` 转成 500 `{'error':'io'}`。CJK 标题超过约 83 字即超 255 字节 NAME_MAX。
- **影响**:本 fork 面向中文用户,长标题无法存入曲库;无数据损坏。
- **修复建议**:按字节截断 `name.encode('utf-8')[:200].decode('utf-8','ignore')`,为 ` (n)` 与扩展名预留空间。
- **置信度**:Confirmed。

### DB-2 · Low(潜在) · `Condition` 的 OR 不加括号,与 `id != 'info' AND %s` 拼接后会把 info 行查出来,`json.loads(None)` 让查询崩溃 **[来源:worker]**
- **位置**:`database.py:51-63`(`or_equal` 只拼 `" OR col=?"`)、`:376-387`(`"WHERE id != 'info' AND %s"`)、`:470`(`json.loads(result[3])`,info 行 metadata 为 NULL);`delete_music`(`:484-490`)完全无 info 保护;`query_random_music`(`:458-461`)用调用方条件时也无。
- **证据**:`probe_misc.py`(组长复跑):`Condition().or_equal('type','nope').or_equal('title','4')` → SQL `id != 'info' AND type=? OR title=?` → `TypeError: the JSON object must be str, bytes or bytearray, not NoneType`。现有调用方 `bot/cleanup.py:284` 的 `type='url' OR type='file'` 因 info 行 type 为 NULL 侥幸无事。若将来 `delete_music(<OR 条件>)` 删到 info 行,下次启动走 0→1 迁移(`database.py:645-657`,丢 path/keywords)再走 2→4(url 时长再乘 60)。
- **影响**:当前无触发路径;属易踩坑的写法。
- **修复建议**:`Condition.sql()` 返回 `f"({self._sql})"`;`query_*`/`delete_music` 统一加 `id != 'info'`。
- **置信度**:Confirmed(潜在缺陷,当前不可触发)。

### F11 · Info · 杂项(无需单独修复,顺手处理)
- `database.py:389` `_query_music_by_plain_sql_cond` 直接拼 SQL,但全仓无调用者 → 删掉以免将来被误用。
- `database.py:272,492` `drop_table` 不 commit;无调用者。
- `web_cache.py:52-62` `/api/cache/cleanup` `mode=expired` 在 Flask 线程里直接 `var.cleaner.run_once()`,与 `CacheCleanupThread` 没有互斥;同时跑只会多几条 "could not remove" warning,无危害,但建议 `run_once` 加 `threading.Lock`。
- `bot/cleanup.py:110-116` 异常分支里的 `_record_run()` 本身若抛(settings.db 锁定)会让清理线程退出,之后直到重启不再清理;建议再包一层 try。
- `playlist_import.py:403-404` 作业 1 小时后被踢出 `_jobs`,仍在跑的作业前端轮询会得到 404。
- **[worker]** 迁移不幂等:`music_table_migrate_from_1_to_2`/`2_to_4`(`database.py:659-688`)的 `insert_music(item)` 未传 `conn`,逐条独立提交、版本号最后才写;中途崩溃后重跑会把 url 时长再乘 60。版本号大于代码版本(降级运行)时 `while` 不执行但随后 UPDATE 把版本改回旧值(`:537-538,566-567`)。
- **[worker]** `playlist.save()`(`media/playlist.py:218-226`)先 `remove_section` 再逐条 `set`,每条单独提交,每 15 s 自动保存;中途被 kill 会丢队列尾部。
- **[worker]** `media/cache.py:95-97` `free_and_delete` 信任 DB 中 url 记录的 path;若历史配置 tmp_folder 曾等于 music_folder,ffmpeg 解码失败会删库内文件。需历史误配。
- **[worker]** `media/spotify.py:140` 对 download_folder 下 mtime > 1h 的 `req_*` 目录 `rmtree`,目录非专用时会删用户同名目录(并入 F1/F2 修复)。
- `tests/test_play_history.py:152` `test_record_on_fresh_start_only` 顺序依赖:单独运行该文件失败(`1 != 0`),全套运行通过——它依赖前面测试泄漏到 `variables` 的全局状态(`var.config` 等),`launch_music` 在单跑时于 record 块之前就抛异常。建议在 setUp 里显式设置 `var.config`。

## 剔除/降级记录
- "SQL 注入(interface.py 库查询 / commands/sources.py `and_like`)" —— 剔除:所有值都走 `?` 占位,列名和 `order_by('create_at')`/`order_by('path')` 硬编码;用户能注入的只有 LIKE 通配符 `%`/`_`,只影响搜索范围,不越过 music 表。
- "sqlite 多线程 check_same_thread / 连接共享" —— 剔除:全部是按调用新建连接、用完关闭,无共享;`UserDatabase` 还设了 `timeout=10`。默认 5 s busy timeout 在本仓库写入量下足够。
- "yt-dlp 保留服务器 Last-Modified 导致新下载文件 mtime 过老被立刻清掉" —— 剔除:`media/url.py:306` 设了 `'updatetime': False`。
- "时钟回拨" —— 剔除:`seconds_until_due` 对 `last > now` 立即到期,`_is_expired` 对负 age 返回 False,安全。
- "删除与播放竞态" —— **初稿整体剔除,现改为部分剔除**:*正在播放*的当前曲仍安全(在 `var.playlist` 内由 `uri()` 保护;自动清理/evict 都排除队列条目;Linux 下删除已打开文件不影响 ffmpeg);但 worker 复现了*已停止 + Cache 页强删 + 恢复*会永久卡住的路径,已作为 RACE-1 列入发现。初稿剔除时只考虑了自动清理路径,漏看了 `delete_entry(force=True)` 这个 UI 明示允许的手动入口,以及 `resume()` 不会为非 ready 的当前曲发起下载。
- "spotdl 输出目录穿越 / 歌名含 `../`" —— 剔除:输出模板固定在 `req_*` 子目录,搬运按 `os.listdir` 的 basename,且 spotdl 自身清洗文件名。
- "`_prune_cache` 删除符号链接文件会删到目标" —— 剔除:复现显示 `os.remove` 只删链接,目标保留(`test_prune_follows_symlinked_file` 通过)。
- "绑定码可暴力猜测" —— 剔除:`commands/personal.py:70-76` 有 `_locked` 锁定,码由 `secrets` 生成,TTL 600 s,属鉴权组范围。
- "insert_music 并发竞态" —— 降为 Low/Likely:4×200 压测未触发。
- worker 自己剔除的 4 条(运行时改 interval 空转、ready 不同步死循环、整条记录回写丢更新、Spotify 新文件被 prune)—— 组长复核同意剔除,理由同其报告。
- worker 的 9 条新发现 **无一剔除、无一降级**:行号逐条核对一致;9 个用例与 2 个探针均由组长亲自复跑,结果与其 `pytest_output.txt` 一致。

## 覆盖范围声明
- 通读:`bot/cleanup.py`(全文)、`database.py`(全文)、`bot/cache_store.py`(全文)、`media/spotify.py`(全文)、`media/file.py`(全文)、`media/cache.py`(全文)、`playlist_import.py`(全文)、`web_users.py:150-445`(UserDatabase / `_Closing`)及 `:625-660`(导入端点)、`media/url.py:40-100,300-310`、`media/url_from_playlist.py:95-125`、`media/playlist.py:218-250,280-296`、`bot/startup.py:155-260`、`interface.py:126-150,585-625,805-825`、`commands/streaming.py:100-160,225-260`、`commands/personal.py:59-90`、`web_cache.py`(全文)、`util.py:31-41,44-64,671-717`、`configuration.default.ini` 相关项;测试 `tests/test_cleanup.py`、`test_cache_store.py`(fixture 部分)、`test_clear_tmp_folder.py` 全文,其余 4 个测试文件只运行未逐行读。范围内 7 个测试文件全部通过。
- worker 额外逐行通读并经组长按行号抽核:`bot/player.py:136-160,200-232,318-432,652-800,880-951`、`interface.py:496-505,588-720`、`commands/library.py:268-316`、`media/playlist.py:58-120,321-420`、`web_api.py:13-60,196-275`、旧 UI `web/js/main.mjs:385-412,630-720`、`webui/src/components/CachePage.vue`。
- 未看:`web_upload.py`(上传文件名清洗属 Web 组)、`webui/` 其余前端、`media/item.py` 以外的 media 模块、`commands/library.py` 标签编辑细节。
- 限制:无法连接 Mumble / YouTube / spotdl,F4 与 F10 仅靠读码;其余删除类发现均有可运行的 pytest 复现。
