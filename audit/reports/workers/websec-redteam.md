# Web 攻击面 — 红队工人独立挖掘报告

仓库:/home/user/Mumble-DJBot 分支 claude/quirky-fermi-6nou7s(只读,无 git 写)。
工人:Web 安全组红队(Opus)。目标是组长 web-security.md 已知项**之外**的新发现。
复现脚本:audit/repro/web-redteam/{repro_upload_space,repro_bind,repro_rmdir_traversal,repro_500}.py
(均用 python + Flask test client + tests/test_web_api 的 FakeBot/FakeCache/FakePlaylist 实跑通)。

遵守已知项去重:flask_secret 伪造 session、X-Real-IP 封禁绕过、Web 取 URL 的 SSRF(add_url/
add_radio/search add/playlists items url & wrapper_from_entry)、旧前端 item.url 存储型 XSS、
web token 40bit+query、默认 auth_method=none 且无权限分级、旧 /upload 仅信 mimetype、无 CSRF —
**本报告不重复**这些。

## 总评

新前端(webui/src)在 XSS 面确实干净(grep 无 v-html / innerHTML / window.open / location 赋值,
POST 带 Content-Type: application/json 天然触发预检),这一块无补充。新发现集中在**后端的两类遗漏**:
(1) 旧 jQuery 路由(/post、/library、/playlist、/download)普遍缺输入校验,其中 `/library` 删除分支
有一个真实的**路径穿越**(可 rmdir 曲库外的空目录);(2) 分片上传缺配额/并发治理,存在认证后
磁盘耗尽 DoS;另有 Mumble 绑定码偏弱 + 限速键可被攻击者轮换,可劫持他人 Web 身份的个人歌单。

最该先修的 3 件:**RT-01(/library 删除路径穿越)**、**RT-02(上传无配额→磁盘耗尽)**、
**RT-03(绑定码劫持)**。

---

## 发现列表

### RT-01 [Medium / Confirmed] 旧 `/library` 删除分支路径穿越 → 删除曲库目录外的任意空目录
- 位置:`interface.py:698-699`
  ```python
  if len(os.listdir(var.music_folder + payload['dir'])) == 0:
      os.rmdir(var.music_folder + payload['dir'])
  ```
  且 `build_library_query_condition`(`interface.py:598-604`)只在 `type == 'file'` 时才用 `dir`
  做 path LIKE,**从不校验 `..`**;删除分支里的 `dir` 是裸字符串拼接到 `music_folder` 后。
- 问题:`dir` 来自请求表单,未做任何路径规范化/穿越检查。取 `type=url`(或任何非 file 类型)时
  `dir` 完全不进查询条件,可自由设成 `../../..../something`,而 rmdir 仍对其求值。
- 证据(repro_rmdir_traversal.py,已运行):
  ```
  music_folder     : /tmp/.../music/
  victim dir exists: True -> /tmp/.../OUTSIDE_victim_dir
  attacker dir param: ../OUTSIDE_victim_dir
  POST /library -> 302
  victim dir STILL exists: False
  RESULT: VULNERABLE - directory outside music_folder was removed
  ```
  真实入口:`POST /library {action:delete, type:url, dir:'../OUTSIDE_victim_dir', tags:'', keywords:''}`。
  需满足:`delete_allowed=True`(默认 False)、查询条件至少命中 1 条库记录(达到删除分支)。
- 影响:开了 `delete_allowed` 的部署里,任何已认证 Web 用户(none 模式下即任何访问者)可用穿越路径
  令 bot 对曲库目录外的**空目录**执行 rmdir(DoS / 误删);同一请求还会按条件批量删除并 `os.remove`
  命中的库文件。注意路径穿越本身与 delete_allowed 无关,是独立的输入校验缺失。
- 修复:删除分支改为复用已校验的安全路径解析(如 `web_upload.resolve_target_dir(payload['dir'])`,
  校验 `abspath` 落在 `music_folder` 内),或显式 `if '..' in dir.split('/'): abort(403)`;
  rmdir/listdir 前统一 `os.path.abspath` + `startswith(music_folder)` 校验。
- 置信度:Confirmed(已复现曲库外目录被删)。

### RT-02 [Medium / Confirmed] 分片上传无空间预留、无并发/挂起配额 → 认证后磁盘耗尽 DoS
- 位置:`web_upload.py:213-229`(init 的空间检查)、`web_upload.py:38-39`(CHUNK_SIZE=32M /
  STALE_SECONDS=24h)、`web_upload.py:75-79`(max_upload_file_size,默认 4G);全文件无任何
  "挂起上传计数/上限"(grep 仅 `prune_stale` 用到 listdir)。
  ```python
  free = shutil.disk_usage(staging_dir()).free
  if size * 1.05 > free:          # size 是攻击者声明值,且不预留
      return ..., 507
  upload_id = uuid.uuid4().hex     # 每次 init 独立判断,互不相让
  ```
- 问题:(a) 空间检查只比"单个声明 size vs 当前 free",**不做预留**;N 个并发 init 看到同一份 free
  全部放行。(b) 对单身份/全局的**挂起上传数量无上限**。(c) 半成品 `.part` 仅在 >24h 后才被
  `prune_stale` 清理。三者叠加 → 认证后可把 `<tmp_folder>/.uploads` 撑爆。
- 证据(repro_upload_space.py,已运行;把 free 伪造成 100MB):
  ```
  init 0..7 -> 200 (each)
  accepted uploads: 8 | declared total bytes = 796917760 | real free claimed = 104857600
  => combined declared size is 7.6x the free disk
  STALE_SECONDS = 86400 (=24 h)
  ```
  即 8 个各声明 95MB 的上传在仅 100MB 可用时全部被接受;随后分别灌数据即可超额写满磁盘。
- 影响:tmp_folder 与曲库/缓存通常同盘;磁盘写满后,bot 的缓存下载、边下边播、合并上传、
  sqlite 写入都会失败,等于全功能 DoS。触发者:任何已认证 Web 用户(none 模式=任何访问者)。
- 修复:init 时按 `size` **预扣**一个运行时"已承诺字节"计数器(完成/取消/过期时归还),用
  `free - committed` 做判断;对单身份与全局的挂起上传数设上限(如各 ≤3 / ≤20);把 STALE_SECONDS
  降到数小时,并在 init 时顺手 prune。附带:`upload_status`/`upload_cancel` 未调用 `_check_enabled()`
  (小问题,可一并加)。
- 置信度:Confirmed(复现超额接受)。

### RT-03 [Medium / Likely] Mumble 绑定码偏弱 + 限速键可被攻击者轮换 → 劫持他人 Web 身份的个人歌单
- 位置:`web_users.py:253`(`code = f"{secrets.randbelow(1000000):06d}"` 仅 6 位)、
  `web_users.py:33`(`BIND_CODE_TTL = 600`)、`web_users.py:262-275`(consume 将任意 mumble_key
  绑到码对应 identity)、`web_users.py:277-286`(identity_for_mumble);
  限速:`commands/personal.py:21-22,53-56`(`MAX_FAILURES=5 / LOCKOUT_SECONDS=600`,`_failures`
  字典以 `mumble_key` 为键)、`commands/personal.py:25-36`(未注册用户 key = `cert:<hash>` 或
  `name:<名字>`,完全由攻击者选择)。
- 问题:绑定码只有 6 位十进制(空间 1e6),TTL 600s。消费侧(聊天 `!bind`)把"谁先报对码,谁的
  Mumble 账号就绑到该 Web 身份"。唯一防爆破是按 `mumble_key` 的 5 次/10 分钟锁定,但未注册用户的
  key 由攻击者自选,**改名/换证书即得全新 key,锁定重置**,故实际可无限尝试。
- 证据(repro_bind.py,已运行):
  ```
  1) victim bind code: 626058 (6 digits; TTL=600s)
  2) attacker consumed victim code -> bound identity: victim@example.com
  3) attacker mumble_key now maps to web identity: victim@example.com
     attacker can read victim playlists via chat: ['My Private List']
  4) after 5 fails key 'name:bot0' locked? True
     attacker rotates to a new name/cert -> key 'name:bot1' locked? False (lockout bypassed)
  ```
  机制(绑定即接管歌单)与限速绕过均已复现;完整在线爆破(命中某活跃码需约 1e5+ 次 `!bind`,
  取决于 Mumble 文本消息吞吐)属 Likely。
- 影响:攻击者把自己的 Mumble 账号绑到受害者 Web 身份后,聊天侧 `!mylist`/`!fav` 直接读写受害者的
  个人歌单(窃取/篡改/投毒),其聊天点歌也计到受害者名下。不授予 Web 登录态(Web 身份仍来自
  Cloudflare 邮箱/用户名),故定 Medium。
- 修复:绑定码用 `secrets.token_hex`/更大数字空间(如 ≥8–10 位或字母数字);缩短 TTL;限速键改用
  **不可轮换**的标识(优先 `uid:` 注册用户;对未注册用户按全局/频道维度限速或直接拒绝绑定),
  并对全局 `!bind` 失败做总量限速。
- 置信度:Likely(接管机制与限速绕过 Confirmed;端到端爆破 Likely)。

### RT-04 [Low / Confirmed] 旧 `/post`、`/playlist`、`/download` 缺输入校验 → 未捕获 500(类型混淆 / 坏 int)
- 位置:`interface.py:323-324`(`int(request.args['range_from'/'range_to'])` 无 try)、
  `interface.py:462,485`(`var.playlist[int(payload['delete_music'/'play_music'])]`)、
  `interface.py:492`(`float(payload['move_playhead'])` + `current_item()` 可为 None)、
  `interface.py:815-816`(`query_music(... id ...)[0]`,id 不存在 → IndexError,在 try 之外)。
- 证据(repro_500.py,已运行):
  ```
  A /post play_music=[1] (list) -> 500
  B /post delete_music="x"      -> 500
  C /post move_playhead="x"     -> 500
  D /playlist?range_from=abc    -> 500
  ```
  JSON 传 list / 非数字 str 给期望 int/float 的参数,或 `/playlist` 的 range 传非数字,均抛未捕获异常。
- 影响:认证后可稳定打出 500(轻量 DoS / 噪声);**无信息泄露**——实测 Flask 3.1.3 下
  `interface.web.env='development'`(`bot/startup.py:40`)是惰性属性,`web.run()` 未开 debug,
  `app.debug` 恒 False,500 返回通用页、不含 traceback(已验证,见剔除记录)。`/post delete_music`
  的 `len>=index`(`interface.py:465`)用长度比索引,还允许越界/负索引删到非预期条目(状态破坏)。
- 修复:旧路由的 int/float 解析统一包 try/except → abort(400),并做 `0 <= index < len` 边界校验
  (与新 API web_api.py 的 `_queue_remove`/`_queue_move` 一致);`/download` 的 `[0]` 前判空。
- 置信度:Confirmed(A–D 均复现 500)。

---

## 剔除 / 降级记录(本工人查了但不报或降级的)
- **web.env='development' 泄露 traceback / 开调试器**:剔除。Flask 3.1.3 下 `app.env` 为废弃惰性属性,
  不影响 `app.debug`(实测设后 debug 仍 False);`web.run()` 未传 debug=True,500 不回 traceback、
  无交互式调试器。即便旧 Flask,启用 debug 靠 FLASK_ENV 环境变量而非该属性,设属性本身是 no-op。
- **web_search `ytsearchN:` 前缀注入任意 extractor**:剔除。`search_youtube` 固定拼 `f'ytsearch{limit}:{query}'`,
  `limit` 为 `min(12,max(1,int()))` 的受限整数,`query` 落在冒号**之后**被当搜索词,无法改写 extractor
  或注入 URL;且走库 API(extract_flat/skip_download),非 CLI,无选项注入。
- **新上传合并后 magic 校验被首分片绕过**:降级/不单报。magic 在**合并后的整文件**上跑
  (`web_upload.py:270-280`),非首分片;首分片无法决定最终 mime。其弱点(octet-stream 与 magic
  不可用时放行)等价于已知 WEB-07 的"可写入任意内容文件",不另立新条。
- **send_from_directory(/assets,/app/<path>) 路径穿越**:剔除。Werkzeug safe_join 挡 `..`,无逃逸。
- **alias 唯一索引 TOCTOU 重名**:剔除。`web_user(alias COLLATE NOCASE)` 唯一索引兜底,竞态下第二条
  触发 IntegrityError(至多变成 500,不产生重名/身份混淆)。
- **touch 并发首插 IntegrityError**:剔除。`resolve_identity` 对 touch 异常 try/except 吞掉,仅影响
  一次 last_seen 记录,无安全后果。
- **/download zip 内存耗尽**:降级。`util.zipdir` 写盘(非内存)且按文件列表 hash 缓存复用;可造成
  CPU/磁盘占用与 tmp 堆积,但量级有限,归入一般 DoS,不单列。

## 覆盖范围声明
- 逐行通读:interface.py(全)、web_api.py、web_users.py、web_upload.py、web_search.py、web_cache.py、
  web_channels.py、commands/personal.py(绑定/歌单聊天侧)、util.zipdir、bot/startup.py(web 启动)、
  configuration.default.ini [webinterface]。
- 前端:grep 全量 webui/src 的 v-html/innerHTML/window.open/location 赋值(均无);读 api.ts 请求构造。
- 复现:RT-01/02/04 Confirmed(Flask test client 实跑),RT-03 机制 + 限速绕过 Confirmed、端到端爆破 Likely。
- 与组长 web-security.md 去重:上述已知 8 条不再复述;本报告 4 条均为其未列出的新点
  (/library 删除路径穿越、上传磁盘耗尽、绑定码劫持、旧路由 500)。
- 未深入:yt-dlp/ffmpeg 进程参数(组长已核 argv 形式,无 shell/选项注入);playlist_import 各平台内部
  请求(属导入/媒体组);pymumble 无法联真机,聊天侧仅就绑定流程做了代码级复现。
