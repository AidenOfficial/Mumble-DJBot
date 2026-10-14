# 深挖工人报告:cleanup / database / 缓存文件操作(Opus)

范围:`bot/cleanup.py`、`database.py`,兼顾 `bot/cache_store.py`、`media/cache.py`、`media/file.py`、`media/spotify.py`,以及它们的删除调用方(`interface.py` 旧 Web 删除端点、`bot/player.py` 等待/恢复逻辑、`commands/library.py`)。
只读:`git status` 干净;基线 `pytest -q` 仍为 253 passed。
复现:`audit/repro/data/repro_deep_cleanup.py`(每个用例断言**安全行为**,失败 = 复现成功;9/9 失败)。辅助探针:`cleanup-worker/drop_db_probe.py`、`cleanup-worker/probe_misc.py`。

运行:
```
cd <repo-root> && python -m pytest -q -p no:cacheprovider \
  audit/repro/data/repro_deep_cleanup.py
```

**与组长初稿(data-cleanup-import.md F1–F11)的关系**:F1–F3、F5–F7、F10 我独立核对过,结论一致,下文不重复。新增发现是 DL-1、DL-2、RACE-1、DB-1、CL-4~CL-7、DB-2。**RACE-1 推翻了初稿里"删除与播放竞态——剔除"那条**:当前曲*正在播放*时确实安全,但"已停止 + 强制删缓存 + 恢复"会让播放器永久卡在 `Wait for the next item to be ready`(已复现)。

---

## 0. 删除调用清单(每处 os.remove / rmdir / rmtree 的候选集合来源与结论)

| 位置 | 候选集合来源 | 保护 | 结论 |
|---|---|---|---|
| `bot/cleanup.py:267` `_remove`(tmp 分支) | `os.listdir(tmp_folder)` 顶层;名字 `split('.',1)[0]` 为 32-hex,**或** abspath ∈ DB `type='url'` 记录路径 | 非 symlink、非 PARTIAL 后缀、不在队列/下载中、非 pin、非常听、`last_played` 与 mtime 都超过 keep_days、MIN_AGE 1h;tmp==music 或 tmp 在 music 内则整段跳过 | 基本正确;hex 白名单不是"所有权证明"(CL-4);孤儿半成品永不回收(CL-5);symlink 目录绕过见初稿 F3 |
| `bot/cleanup.py:267` `_remove`(Spotify 分支) | `os.listdir(download_folder)` 顶层**所有**普通文件 | 非 symlink、非 PARTIAL、不在队列、mtime 过期 + MIN_AGE;与 music 重叠则跳过 | 不看 pin/播放记录(CL-6);目录非专用时删任意文件(初稿 F1) |
| `bot/cache_store.py:211` `_remove_files` ← `delete_entry`/`evict`/`clear_unpinned` | `scan_files`:tmp 顶层 32-hex 前缀文件,按 id 分组 | 非 symlink;evict 排除 in_queue/downloading/pinned;**无 MIN_AGE、无 music_folder 重叠检查**;`delete_entry(force=True)` 可删队列中条目 | 外来 hex 文件被淘汰(CL-4);force 删当前曲导致卡死(RACE-1) |
| `media/spotify.py:188` `_prune_cache` | download_folder 顶层所有普通文件,按 mtime 最旧先删到 max_cache_size | 只跳过队列引用 | 初稿 F2(无曲库重叠检查),同意 |
| `media/spotify.py:140` rmtree `req_*` | download_folder 下 `req_*` 目录且 mtime > 1h | 无 | download_folder 非专用时会删用户的 `req_*` 目录(并入初稿 F1/F2) |
| `media/spotify.py:268,353` rmtree | `tempfile.mkdtemp` 自建目录 | — | 安全 |
| `media/spotify.py:117` | spotdl 配置旁的 `.spotipy` | — | 安全 |
| `media/url.py:245` `_discard_incomplete_download` | `glob(self.path + "*")`,path 来自 DB | 仅当 `path` 存在且 `.incomplete` 标记存在 | 安全(`path==''` 时 `exists('')` 为假不会走到) |
| `media/url.py:354,371` | `<tmp>/<id>.incomplete`、`glob(<tmp>/<id>*)` | 下载失败才删 | 安全 |
| `media/cache.py:97` `free_and_delete` | url 条目的 DB `path` | 无路径校验 | 播放器 ffmpeg 非 0 退出 / 校验失败即调用;DB path 若曾指向库内(旧配置 tmp==music)会删库文件,Info |
| `interface.py:694` 旧 `/library action=delete` | `build_library_query_condition(payload)` 的 DB 查询结果 → `item.uri()` | 只有 `delete_allowed`(默认 True) | **DL-1**:目录过滤失效/LIKE 通配 |
| `interface.py:699` `os.rmdir(music_folder + payload['dir'])` | 原样拼接请求参数 | 无 | 可 rmdir 曲库外的空目录(DL-1 附带) |
| `interface.py:503` `/post delete_item_from_library` | 请求里的 id → `item.uri()` | **无** `delete_allowed` 检查 | **DL-2** |
| `web_upload.py:*`、`util.py:681,714` | 上传暂存 / `clear_tmp_folder` | — | web_upload 属 Web 组;`util.clear_tmp_folder` 全仓无非测试调用者(死代码) |

对抗性构造逐项结论(未列入发现的 = 已验证安全):
- tmp 内的 symlink 指向曲库文件:`isfile(path) and not islink(path)` 排除;`os.remove` 删的是链接本身,不跟随;`getmtime` 跟随链接但链接已被排除在外。安全。
- tmp 内的硬链接指向曲库文件:`os.remove` 只删目录项,曲库文件保留。安全。
- `tmp_folder == music_folder`、tmp 在 music 内:cleanup 整段跳过(已有测试)。music 在 tmp 内:只扫 tmp 顶层,曲库文件在子目录里,安全。**但 cache_store 没有这些检查**:tmp==music 时曲库顶层 32-hex 名的文件会被 evict(只影响 hex 名,并入 CL-4)。
- 末尾斜杠 / 相对与绝对路径混用:各处统一 `os.path.abspath`,`var.tmp_folder` 与 `URLItem.path` 来源一致,未发现失配。经 symlink 后相等:初稿 F3,同意。
- Windows 大小写:`abspath` 不做 `normcase`,`C:\Music` 与 `c:\music` 会绕过重叠检查;但 `FileItem.uri()` 用 `path[0] != "/"` 判断绝对路径(`media/file.py:69`),本 fork 实际不支持 Windows。Info。
- mtime vs ctime:用 mtime;yt-dlp 设了 `'updatetime': False`(`media/url.py`),下载中文件 mtime 持续更新 + `downloading` 标志 + MIN_AGE 三重保护;流式下载的 `<id>` 无后缀,但受 `downloading` 保护。时钟回拨:`last > now` 立即到期,`_is_expired` 对负 age 返回 False,安全。时钟错误地**向前**跳(例如 NTP 误同步到未来)时,所有缓存看起来都过期并被删除;这是 mtime 判断的固有局限,Info。
- MIN_AGE:两个 cleanup 分支都走 `_is_expired`,都生效;cache_store 的容量淘汰按设计没有 MIN_AGE。

---

## 发现列表(按严重度)

### DL-1 · High · 旧 `/library action=delete` 的删除范围远大于用户选的过滤条件(会删整库)
- **位置**:`interface.py:598-604`(`build_library_query_condition`),`interface.py:685-699`(delete 分支);`configuration.default.ini:44`(`delete_allowed = True`);旧 UI `web/js/main.mjs:636-646,709-719`、`web/templates/index.template.html:319`("delete all files" 按钮)。
- **问题**:(a) 只有 `form['type'] == 'file'` **严格相等**时才加目录条件,类型选了 `file,url`(旧 UI 里两个过滤按钮都按下,目录下拉框依然可选)时目录被**静默忽略**,删掉所有匹配的本地文件;(b) 目录条件是 `path LIKE '<dir>/%'`,`_`/`%` 没有转义,且 SQLite 的 LIKE 对 ASCII 不区分大小写,删 `my_band/` 时也会删 `myXband/`、`MY_BAND/`;(c) 最后 `os.rmdir(var.music_folder + payload['dir'])` 原样拼接请求参数(`../x` 可 rmdir 曲库外的空目录;目录不存在时抛 500,而这时文件已经删完了)。
- **证据**:
  ```python
  if form['type'] == 'file':                       # 598
      ...
      condition.and_like('path', folder + '%')     # 604
  ...
  for item in items:                               # 688-696
      ...
      if os.path.isfile(item.uri()):
          os.remove(item.uri())
  ```
  复现 `repro_deep_cleanup.py::LegacyLibraryDelete`(真实 Flask 路由 + 真实 sqlite + 真实 MusicCache):
  - `type='file,url', dir='Rock'` → `Rock/a.mp3` **和** `Jazz/b.mp3` 都被删;
  - `type='file', dir='my_band'` → `myXband/d.mp3`、`MY_BAND/e.mp3` 也被删(survivors 0/2)。
- **影响**:任何通过 Web 鉴权的用户(默认 `auth_method=none`,见 WEB-06)在旧 UI(`/legacy`,`Dockerfile.local` 会构建)里正常操作,或直接发一个 POST,就能**不可逆地**删掉整个本地曲库;正常用户也会误删:选了"本地+URL"再挑一个目录点删除,结果全库消失。
- **修复建议**:`build_library_query_condition` 改为 `if 'file' in types and form.get('dir')` 时加路径条件,并且 delete 动作**要求**类型只能是 `file`、`dir` 非空;LIKE 改为 `path LIKE ? ESCAPE '\'`,先把 `\ % _` 转义(或者改用 `substr(path,1,len)=?` 精确比较前缀,再在 Python 里做 `os.path.commonpath` 二次校验);`rmdir` 前先 `realpath`,并确认结果在 `music_folder` 之内。
- **置信度**:Confirmed。

### DL-2 · Medium · `/post delete_item_from_library` 不检查 `delete_allowed`,即使管理员关闭删除仍可从磁盘删除曲库文件
- **位置**:`interface.py:496-505`;对比 `interface.py:686`(`/library` 检查了开关)、`interface.py:641`(UI 按开关隐藏删除按钮)。
- **证据**:
  ```python
  elif 'delete_item_from_library' in payload:
      _id = payload['delete_item_from_library']
      var.playlist.remove_by_id(_id)
      item = var.cache.get_item_by_id(_id)
      if os.path.isfile(item.uri()):
          os.remove(item.uri())
  ```
  复现 `repro_deep_cleanup.py::DeleteItemIgnoresDeleteAllowed`:`delete_allowed=False` 时 POST `{'delete_item_from_library': md5('Album/track.flac')}` 后,文件已被删除。id 等于 `md5(相对路径)`,可通过 `/library action=query` 或 `/api/library` 枚举。(另外 id 不存在时 `item` 为 None,会出 AttributeError 500。)
- **影响**:管理员通过 `delete_allowed=False` 关闭删除后,UI 只是隐藏了按钮,任意 Web 用户仍能逐个删除曲库文件。WEB-06 只把这个端点列为"等权",没有指出它绕过了开关。
- **修复建议**:在分支开头加 `if not var.config.getboolean('bot','delete_allowed'): abort(403)`;`item is None` 时返回 404;删除前确认 `realpath(item.uri())` 位于 `music_folder` 或 `tmp_folder` 之内。
- **置信度**:Confirmed。

### RACE-1 · Medium · 当前曲的缓存在"已停止"状态下被删除后,恢复播放会永久卡住(不会重新下载)
- **位置**:`bot/player.py:906-913`(`stop` 设 `wait_for_ready=True`)、`:934-946`(`resume`:`is_ready()` 为假时只把 playhead 归零后 return,**不会**调用 `start_download`)、`:774-798`(等待分支:不 ready、未失败、不能流式播放时只设置状态,也不会发起下载);删除方:`bot/cache_store.py:236-254`(`delete_entry(force=True)` 可以删队列里的条目)、`web_cache.py:42-50`;UI 提示 `webui/src/components/CachePage.vue:82-83`("It will be downloaded again when it plays")。
- **问题**:只有 `start_download()`(切歌或 `play()` 时调用)和下一首的预取会发起下载。当前曲已经过了 `start_download`(停止前它是 ready 的),之后文件消失,`URLItem.is_ready()` 会把 ready 改成 `validated` 并返回 False;此后主循环每一轮都只走到 `_loop_status = 'Wait for the next item to be ready'`,没有任何代码再为它发起下载。
- **证据**:复现 `repro_deep_cleanup.py::StopDeleteResumeStall`(真实 `PlayerMixin._loop_iteration` + 真实 `URLItem`/`CachedItemWrapper`/`cache_store.delete_entry`):`stop()` → `delete_entry(id, force=True)` → `resume()` → 跑 50 轮循环,既没有调用 `async_download`,也没有调用 `launch_music`,状态停在 `'Wait for the next item to be ready'`。
  其它触发同一结局的路径:cleanup / evict 先拍下保护快照,再逐个删除(`cleanup.py:166` 与 `:230`,`cache_store.py:259` 与 `:276`),中间没有复查;如果某个条目在快照之后被加入队列并立刻成为当前曲,窗口内删除会落到同一个卡死分支(Likely,窗口为毫秒到秒级)。如果文件在 `launch_music` 生成 ffmpeg 之后、ffmpeg 打开文件之前被删,ffmpeg 非 0 退出,`player.py:749-750` 会调用 `free_and_delete`,连同 DB 记录和标签一起删掉。
- **影响**:Web 用户按 UI 提示"强制删除队列中的缓存",再点继续(或在聊天里 `!play`),机器人就一直静音,直到有人手动跳歌或重新播放。这与 UI 的承诺相反。
- **修复建议**:最小改动是在 `resume()` 的早退分支里,对 `music_wrapper` 调用 `self.start_download(music_wrapper)`(`is_ready()` 为假时)并设 `wait_for_ready=True`;更稳的改法是在 `_loop_iteration` 的等待分支里,当 `not current.is_ready() and not item.downloading and current.id not in self._active_downloads` 时调用 `self.async_download(current)`(自愈)。另外 cleanup / evict 在 `_remove` 前对该路径**重新**判断一次 `protected`,缩小 TOCTOU 窗口。
- **置信度**:Confirmed(stop/force-delete/resume 路径);cleanup/evict 快照 TOCTOU 为 Likely。

### DB-1 · Medium · `!dropdatabase` 之后机器人一直处于损坏状态:settings 表不重建,`var.music_db` 被指到了 settings.db
- **位置**:`commands/library.py:307-316`;`database.py:272-276,492-496`(`drop_table` 只做 DROP)。
- **证据**:
  ```python
  var.db.drop_table()
  var.db = SettingsDatabase(var.settings_db_path)
  var.music_db.drop_table()
  var.music_db = MusicDatabase(var.settings_db_path)   # ← 应为 music_db_path
  ```
  两张表都没有重新建(没有重跑 `DatabaseMigration`)。复现 `repro_deep_cleanup.py::DropDatabase`(`music_db now points at .../settings.db`),以及 `cleanup-worker/drop_db_probe.py` 的输出:
  ```
  music_db path -> /tmp/.../settings.db
  db.set volume -> OperationalError no such table: botamusique
  db.get w/ fallback -> OperationalError no such table: botamusique
  db.has_option url_ban -> OperationalError no such table: botamusique
  music_db.query_music_by_id -> OperationalError no such table: music
  music_db.insert_music -> OperationalError no such table: music
  cleaner seconds_until_due -> OperationalError no such table: botamusique (uncaught in _loop -> thread dies)
  ```
- **影响**:管理员执行这条"清空数据库"命令后,直到重启前:每次调音量、`URLItem.validate`(`has_option('url_ban')`)、加歌保存(`var.cache.db` 仍指向表已被删的旧对象)、Web 状态都会抛 OperationalError;`CacheCleanupThread` 的 `_loop` 在 try 外调用 `seconds_until_due()`,线程直接退出;`last_run()` 只捕获 ValueError/TypeError。重启后迁移会重建表,恢复正常。
- **修复建议**:改为 `var.music_db = MusicDatabase(var.music_db_path)`;紧接着执行 `DatabaseMigration(var.db, var.music_db).migrate()`,并设置 `var.cache.db = var.music_db`、调用 `var.cache.free_all()`;`CacheCleaner.last_run` 改为捕获 `Exception`。
- **置信度**:Confirmed。

### CL-4 · Low · 32-hex 文件名白名单不能证明文件属于机器人:共享 /tmp 里外来的 `<32hex>[.ext]` 文件会被定期清理删除,也会被容量淘汰**立即**删除
- **位置**:`bot/cleanup.py:216-217`(`base = name.split('.',1)[0]`,任意扩展名都放行);`bot/cache_store.py:126-137`(`scan_files` 同样规则),`:257-283`(`evict` 没有 MIN_AGE,每次 URL 下载前后都会通过 `media/url.py` 的 `_enforce_cache_limit` 调用);默认 `tmp_folder = /tmp/`(`configuration.default.ini:75`)。
- **证据**:`repro_deep_cleanup.py::ForeignHexFiles`:30 天前的 `<md5>.tar.gz` 被 `run_once()` 删除;外来的 `<md5>` 文件(刚创建)在 `tmp_folder_max_size=0` 时被 `enforce_size_limit()` 立刻删除。外来文件还会计入缓存总量,挤掉真正的缓存。
- **影响**:非 Docker 部署、默认 `/tmp/` 时,其它程序或用户放在 /tmp、以 32 位 hex 命名的文件(md5 命名的临时文件在各类工具里很常见)会被机器人删掉;/tmp 有 sticky 位,所以只限于与机器人同一 uid 的文件。`tests/test_cleanup.py:113` 只覆盖了非 hex 的外来文件。
- **修复建议**:候选条件改为"basename 恰好是 `<id>` 或 `<id>.jpg`/已知后缀,**并且** `<id>` 存在于 music_db(`type in ('url','url_from_playlist')`)";没有 DB 记录的 hex 文件只在 tmp_folder 不是 `/tmp` 时才清理。`evict` 也应排除 mtime 小于 MIN_AGE 的文件。
- **置信度**:Confirmed。

### CL-5 · Low · 下载崩溃留下的孤儿半成品(`<id>.part` / `<id>.incomplete`)永不回收,还在容量统计里"永久占位"
- **位置**:`bot/cleanup.py:214-215`(PARTIAL 后缀无条件跳过,不看文件年龄);`bot/cache_store.py:155,174`(只要有 partial 文件就标记 `downloading`),`:264`(evict 跳过 downloading)。
- **证据**:`repro_deep_cleanup.py::OrphanPartials`:30 天前的 `<md5>.part`(此时没有任何条目在下载)在清理和 `tmp_folder_max_size=0` 淘汰之后依然存在。
- **影响**:进程被 kill / 看门狗重启 / 断电时留下的 `.part`(可能有数百 MB)会一直占盘;它们计入 `total`,导致其它正常缓存被提前淘汰;Cache 页把它们显示成"下载中"。只有同一个 URL 再次点播时才会被续传或丢弃。
- **修复建议**:半成品的判定改为"有 partial 后缀 **且** (`id` ∈ `downloading_ids()` 或 mtime 在 MIN_AGE(或 1 天)以内)";超过这个期限、且没有对应下载线程的 partial 文件当作普通过期文件处理(cleanup 删除、evict 视为可淘汰)。
- **置信度**:Confirmed。

### CL-6 · Low · Spotify 缓存不看播放记录 / 常听次数,昨天刚播过 5 次的歌也会被删除(连同 DB 记录和标签)
- **位置**:`bot/cleanup.py:234-262`(Spotify 分支没有 `usage`/`keep_plays`/`last_played` 判断)、`:310-319`(`delete_music` 删掉记录);文档 `configuration.example.ini:100-101`("keep cached files played or downloaded within the last N days (… frequently played items are always kept)",说的是 tmp_folder **和** Spotify download_folder)。
- **证据**:`repro_deep_cleanup.py::SpotifyIgnoresPlayHistory`:mtime 10 天前、昨天播放 5 次(`play_history` 中 item_id = `md5(path)`)、带标签 `favourite` 的 Spotify 文件被删除,`query_music_by_id` 返回 None。
- **影响**:常听的 Spotify 歌每隔 keep_days 就会被删一次、重新下载一次,用户打的标签也随之丢失。这与文档描述不符。这里是正常配置下的问题,和初稿 F1 的"目录配错"不是一回事。
- **修复建议**:把 `_sweep_tmp_folder` 里的 `usage`/`keep_plays`/`last_played` 判断抽成公共函数,Spotify 分支用 `md5(path)`(即 FileItem id)查同一份 usage。
- **置信度**:Confirmed。

### CL-7 · Low · "存入曲库"对较长的中日韩标题必然失败(ENAMETOOLONG)
- **位置**:`bot/cache_store.py:318-321`(`[:120]` 按**字符**截断),`:340-348`。
- **证据**:`cleanup-worker/probe_misc.py`:90 个汉字的标题(270 字节)→ `OSError [Errno 36] File name too long`;`web_cache.py:78-82` 把它转成 500 `{'error':'io'}`。YouTube 标题最长 100 字符,CJK 标题超过约 83 个字就会超出 255 字节的 NAME_MAX。
- **影响**:本 fork 主要面向中文用户,长标题的歌无法存入曲库;没有数据损坏。
- **修复建议**:按字节截断:`name.encode('utf-8')[:200].decode('utf-8','ignore')`,并为 ` (n)` 和扩展名预留空间。
- **置信度**:Confirmed。

### DB-2 · Low(潜在) · `Condition` 的 OR 不加括号,和 `id != 'info' AND %s` 拼接后可能把 info 行查出来,`json.loads(None)` 让整个查询崩溃
- **位置**:`database.py:51-63`(`or_equal` 只拼 `" OR col=?"`),`:376-387`(`"WHERE id != 'info' AND %s"`),`:470`(`json.loads(result[3])`;info 行的 metadata 为 NULL);`delete_music`(`:484-490`)完全没有 info 保护,`query_random_music` 的子查询(`:458-461`)使用调用方传入的条件时也没有。
- **证据**:`probe_misc.py`:`Condition().or_equal('type','nope').or_equal('title','4')` 生成 `id != 'info' AND type=? OR title=?`,结果抛 `TypeError: the JSON object must be str, bytes or bytearray, not NoneType`。现有调用方 `bot/cleanup.py:284` 的 `type='url' OR type='file'` 因为 info 行 type 为 NULL 而侥幸没事。如果将来某个 `delete_music(<OR 条件>)` 删到了 info 行,下次启动就会走 0→1 迁移(`database.py:645-657`,只复制 id/type/title/metadata/tags,**丢掉 path/keywords**),再走 2→4,把 url 时长再乘一次 60。
- **影响**:当前不可利用;风险在于这种写法很容易让后来者踩坑(SQL 注入面:列名、ORDER BY、LIMIT/OFFSET 全部是代码常量或 `int()` 后的值,我核对了全部调用方,与初稿结论一致:无注入)。
- **修复建议**:`Condition.sql()` 返回 `f"({self._sql})"`;`query_*`/`delete_music` 统一加上 `id != 'info'`。
- **置信度**:Confirmed(潜在缺陷,当前无触发路径)。

---

## Info / 不单列
- **迁移不幂等**:`music_table_migrate_from_1_to_2`/`2_to_4`(`database.py:659-688`)里的 `insert_music(item)` 没有传 `conn`,每条记录单独提交,版本号最后才写。如果迁移中途崩溃,重跑时已转换过的 url 时长会再乘 60。另外,版本号大于代码版本时(降级运行),`while` 不执行,但随后的 UPDATE 会把版本**改回**旧值(`:537-538,566-567`)。只影响一次性升级。
- **settings v0 = DROP TABLE**:`db_version` 行缺失时,`settings_table_migrate_from_0_to_1` 会删掉整张设置表(`:617-622`)。我核对了全仓所有 `var.db.remove_option/remove_section/set` 调用,section 都是固定值,没有能删掉 `('bot','db_version')` 的路径,所以不可达。
- **连接模型**:每次调用新开连接、用完即关,没有共享,也不需要 `check_same_thread`;使用默认 5 s busy timeout,没有 WAL、没有重试;写操作都是短事务,没有发现长事务导致 "database is locked"。`playlist.save()`(`media/playlist.py:217-226`)先 `remove_section` 再逐条 `set`,每条单独提交,每 15 s 自动保存一次。如果进程在保存中途被 kill,队列尾部会丢失(Low)。
- **启动时信任 DB 内容**:`playlist.load()` 对 `json.loads` 的结果和 `current_index` 不做校验;条目记录被删(例如初稿 F10 的 `build_dir_cache` 删掉绝对路径记录,这一步在 `startup.py:210` 先于 `:236` 的 load 执行)后,索引会错位。repeat 模式下 `current_item()` 可能越界(之后被 `next()` 回绕修复)。没有发现会导致启动崩溃的确定路径。
- **`free_and_delete` 信任 DB 中的 url path**(`media/cache.py:95-97`):如果旧配置里 tmp_folder 曾等于 music_folder,url 记录的 path 就指向库内文件,ffmpeg 解码失败时会把它删掉。需要历史误配,Info。
- `util.clear_tmp_folder`(`util.py:671`)删除任意文件名,但全仓没有非测试调用者,是死代码。建议删除,以免将来被误接到 `/tmp/` 上。

## 剔除记录
- "cleaner 在 `cleanup_interval_days` 运行时改为 0 后空转":剔除,配置只在启动时读取(全仓没有 `var.config.set/read` 运行时调用)。
- "cleanup 把 ready 重置为 validated 与内存 cache 不同步,导致播放循环死循环":剔除。`URLItem.is_ready()`(`media/url.py:87-95`)每次都会检查文件是否存在并自愈;真正的卡死另有原因,见 RACE-1。
- "`_invalidate_db_records` 用整条旧记录回写导致丢更新":剔除。最坏情况是 DB 里写成 `validated` 而文件实际存在,下次 `validate()` 看到文件就改回 yes,没有可观察的危害。
- "Spotify 下载完成到加入队列之间被 `_prune_cache` 删掉":降级为不报。新文件 mtime 最新,按最旧优先的淘汰顺序会排在最后。

## 覆盖范围
- 逐行通读:`bot/cleanup.py`、`database.py`、`bot/cache_store.py`、`media/cache.py`、`media/file.py`、`media/spotify.py`(prune/download)、`media/url.py:40-160,236-400`、`bot/player.py:136-160,200-232,318-432,652-800,880-951`、`interface.py:496-505,588-720,805-825`、`commands/library.py:268-316`、`commands/streaming.py:125-260`、`media/playlist.py:58-120,205-250,321-420`、`web_cache.py`、`web_api.py:13-60,196-275`、`bot/startup.py:155-256`、`util.py:31-41,671-717`、`tests/test_cleanup.py`,以及旧 UI `web/js/main.mjs:385-412,630-720`。
- 没有看:`web_upload.py` 的删除点(属 Web 组)、`playlist_import.py`(初稿已覆盖)、`webui/` 除 CachePage 外的部分。
- 复现输出(`cleanup-worker/pytest_output.txt`):
```
FAILED repro_deep_cleanup.py::LegacyLibraryDelete::test_dir_filter_ignored_when_more_than_one_type_selected
FAILED repro_deep_cleanup.py::LegacyLibraryDelete::test_dir_like_wildcards_and_case_hit_sibling_folders
FAILED repro_deep_cleanup.py::DeleteItemIgnoresDeleteAllowed::test_library_file_survives_when_delete_disallowed
FAILED repro_deep_cleanup.py::StopDeleteResumeStall::test_resume_after_force_delete_triggers_redownload
FAILED repro_deep_cleanup.py::DropDatabase::test_bot_still_usable_after_dropdatabase
FAILED repro_deep_cleanup.py::ForeignHexFiles::test_cleanup_sweep_keeps_foreign_hex_named_archive
FAILED repro_deep_cleanup.py::ForeignHexFiles::test_size_eviction_keeps_foreign_hex_named_file
FAILED repro_deep_cleanup.py::OrphanPartials::test_month_old_orphan_part_file_is_reclaimed
FAILED repro_deep_cleanup.py::SpotifyIgnoresPlayHistory::test_recently_and_frequently_played_spotify_track_is_kept
9 failed in 0.83s
```
  各条断言信息:`deleting folder Rock with file+url filter wiped Jazz/ too`;`2 != 0 : deleting folder my_band also deleted myXband/ and MY_BAND/`;`delete_allowed=False but /post delete_item_from_library removed the file`;`player stuck in 'Wait for the next item to be ready': nothing re-downloads the deleted current item`;`music_db now points at .../settings.db`;两条 ForeignHexFiles 都是 `False is not true`(文件已被删);`30-day-old orphan .part survives both the sweep and eviction`;`played 5x yesterday, still deleted (record+tags dropped: None)`。DL-2 用例里的 `AttributeError: 'ListPlaylist' object has no attribute 'mode'` 来自删除**之后** `status()` 的渲染,是测试替身导致的,不影响结论。
