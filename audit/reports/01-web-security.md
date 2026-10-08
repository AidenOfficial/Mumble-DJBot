# Web 安全组审计报告(终版 · 已合并复核与红队)

仓库:/home/user/Mumble-DJBot 分支 claude/quirky-fermi-6nou7s(只读审计,工作树干净,无 git 写)。
组长:Fable。本报告已合并三份输入并逐条亲自核对:
- 组长初审(8 条)+ 复现脚本 audit/repro/web/(repro_auth.py / repro_ssrf2.py / repro_legacy.py / **repro_upload.py**)。
- Sonnet 复核:audit/reports/workers/websec-verify.md(+ websec-verify/)。
- Opus 红队新发现 4 条:audit/reports/workers/websec-redteam.md(+ websec-redteam/)。
本环境无 Agent/Task 工具,初审通读与复现由组长本人完成;复核/红队由协调人另行派发,其行号与复现均经组长重新核对。

严重度最终裁量权在组长;对复核建议的采纳/不采纳逐条在"严重度裁决"小节给出一句理由。

## 总评

整体:**无 Critical,但有 2 个 High 必须先修**。设计意图(Web 接口置于 Cloudflare Access 之后、8181 不对外暴露、
auth_method=none)本身合理,新 Vue 前端在 XSS/别名注入上做了正确防护(别名正则挡 `<>&"'`、文本绑定、无 v-html)。
问题集中在**鉴权层脆弱**与**旧 jQuery Flask 接口一组路径穿越/输入校验缺失**两条主线。

最该先修(三件):
1. **旧 Flask 接口的路径穿越主线(WEB-07 + WEB-10)**:旧 `/upload`、`/library` 删除分支完全没有路径规范化。
   WEB-07 可在容器内任意可写位置新建受控文件(root 容器下 → RCE);WEB-10 可 rmdir 曲库目录外的空目录。
   同根同治:旧接口统一复用 web_upload 的 `clean_filename` + `resolve_target_dir` 风格校验,或直接下线旧接口。
2. **flask_secret 默认公开(WEB-01)**:token 模式 + 未改默认密钥时,用公开密钥伪造会话 cookie 即完全绕过鉴权(已复现)。
3. **Web 取 URL 无 is_public_url(WEB-03)**:认证后 SSRF(内网探测);且聊天 `!radio`/`!live` 同样缺护栏(跨组)。

---

## 发现列表(按严重度:High → Medium → Low)

### WEB-07 [High / Confirmed · 组长初审(定性已更正)] 旧 `/upload` 文件名路径穿越 + 任意文件写(→ RCE)
- 位置:`interface.py:769`(`filename = file.filename`,无 basename/secure_filename)、
  `interface.py:784`(`if "audio" in file.mimetype or "video" in file.mimetype` — 仅凭客户端自报 Content-Type)、
  `interface.py:785-786`(`startswith(music_folder)` 只校验 `storagepath`=由 targetdir 推出的目录,**不校验** filename)、
  `interface.py:796`(`filepath = os.path.join(storagepath, filename)` 裸拼最终路径)。
- 问题:targetdir 的 `'../'` 检查(:776)与 abspath 校验都只作用于目录;filename 原样拼接,werkzeug 3.1.9 的
  multipart 解析不对 filename 做 basename(只处理 Windows 盘符)。`../../x` 形式的 filename 直接逃出 music_folder。
  类型门禁只看客户端 mimetype 字符串,伪造 `audio/*` 即过。
- 证据(audit/repro/web/repro_upload.py,全程隔离在 websec/sandbox/,已运行):
  ```
  1 one-dotdot  -> 200 | landed in music/: True
  2 two-dotdot  -> 200 | ESCAPED music_folder: True -> ../escaped.mp3
  3 .pth w/ audio mime -> 200 | written outside music: True
  4 .txt w/ html  mime -> 415 (类型门禁是唯一的检查)
  ```
  真实入口:`POST /upload` multipart,`filename='../../evil.pth'`、`Content-Type: audio/anything`、`targetdir='uploads/'`。
- 影响:任何通过 Web 鉴权(none 模式下即任何可达访问者)可在容器内任意可写位置**新建**(已存在则 409,不覆盖)
  文件名+内容完全可控的文件。`upload_enabled` 默认 True(ini:97);Docker 镜像无 `USER` → root;写入
  `venv/.../site-packages/*.pth` 即解释器启动代码执行,compose healthcheck 周期性拉起 `venv/bin/python` → 近即时 RCE。
  (红队/复核均未触碰真实 venv,仅在 sandbox 内演示 `.pth` 机制。)
- 修复:`filename = web_upload.clean_filename(file.filename)`,并对最终 `filepath` 再做
  `os.path.abspath(filepath).startswith(os.path.abspath(var.music_folder) + os.sep)`;最好直接删除旧 `/upload`
  (新 UI 已走分片上传,web_upload 的 clean_filename/resolve_target_dir 校验正确)。
- 置信度:Confirmed。
- 更正说明:组长初审曾把本条的"路径穿越"剔除,系复现脚本只用单个 `../`(落点仍在 music 内,且检查的是 music 上级目录)
  导致误判;复核(Sonnet)与红队均独立指出,已改用 `../../` 复现逃逸。本条由 Low 升为 High。

### WEB-01 [High / Confirmed · 组长初审] 默认 flask_secret 可伪造会话 cookie,完全绕过 token 鉴权
- 位置:`configuration.default.ini:90`(`flask_secret = ChangeThisPassword`,example.ini:234 同)、
  `bot/startup.py:41`(`web.secret_key = config.get('webinterface','flask_secret')`)、
  `interface.py:155-158`(token 分支:`if 'user' in session and 'token' not in request.args:` 直接放行,不回查 web_token 库)。
- 问题:会话 cookie 以 flask_secret 签名,默认值是写死在仓库里的公开串,全仓库无任何启动校验/告警。token 模式下
  session 里有 `user` 键即放行。攻击者用公开密钥自签 `{'user':'x'}` cookie 即可。
- 证据(audit/repro/web/repro_auth.py + 复核 websec-verify/v_web01_02.py,均已运行):
  ```
  A no-cred:                       200 NEED_TOKEN_PAGE        (无凭据被拒)
  B forged cookie (default secret):200 {JSON 正常返回}        (伪造即放行)
  B2 forged cookie vs 非默认密钥:  200 NEED_TOKEN_PAGE        (对照:改了密钥即被拒)
  B3 forged cookie POST /api/queue clear: 200                (破坏性接口同样放行)
  ```
- 影响:token 模式 + 未改默认密钥 + 可达时,无需任何凭据取得全部 Web 权限。注意 configuration.example.ini 的
  示例值正是 `auth_method = token`,即这是被文档推荐的模式之一,现实中并不罕见。
- 修复:启动时若 flask_secret 为空或等于占位串则拒绝启动(或自动生成随机密钥持久化到 db);token 模式强制非默认值。
- 置信度:Confirmed。

### WEB-02 [Medium / Confirmed · 组长初审] 封禁/限速基于可伪造的 X-Real-IP(绕过 + 定向封禁)
- 位置:`interface.py:61-63`(ReverseProxied 无条件 `environ['REMOTE_ADDR']=HTTP_X_REAL_IP`,无可信代理白名单)、
  `configuration.default.ini:91`(`is_web_proxified = True` 默认开,bot/core.py:45 读取)、
  `interface.py:131`(`if request.remote_addr in banned_ip: abort(403)`)、`:142`/`:177`(`bad_access_count[remote_addr]` 自增)。
- 问题:封禁表与失败计数都以可伪造的 remote_addr 为键。
- 证据(repro_auth.py + websec-verify/v_web01_02.py,已运行):
  ```
  C 轮换 IP 100 次坏 token -> banned: []  (无限暴破不被拦)
  D 伪造受害者 IP 发坏 token -> banned: ['203.0.113.7']; 受害者后续 403,其它 IP 200  (定向封禁 DoS)
  E password 模式同样可封禁受害者; F is_web_proxified=False 时封禁回到真实对端(对照正常)
  ```
- 影响:仅 password/token 模式生效。(a) 限速/封禁形同虚设;(b) 可定向封禁任意 IP 做 DoS/骚扰。
  附带:`bad_access_count` 无上限/无清理(缓慢内存增长);`!web`(commands/web.py:17-18,**非 admin 命令**)
  任何 Mumble 用户可执行并清空封禁表。
- 修复:仅当请求确来自固定可信反代(cloudflared/内网网关)时才采信 X-Real-IP,否则用真实 socket 对端;
  或引入 ProxyFix 配置可信跳数。封禁逻辑用真实对端 IP。
- 置信度:Confirmed。

### WEB-03 [Medium / Confirmed · 组长初审(表述已更正)] Web 取 URL 接口缺 is_public_url → 认证后 SSRF
- 位置:`interface.py:445`(/post add_url)、`:454`(/post add_radio)、`web_api.py:289-310`(/api/search/add,仅校验 http/https 前缀)、
  `web_users.py` `wrapper_from_entry`(radio/livestream/url 分支);全仓库仅 `commands/sources.py:123,149` 两处调用 is_public_url。
  radio 条目构造即外联:`media/radio.py` `get_radio_server_description` → `requests.head/get(base_url + '/stats?json=1' | '/status-json.xsl')`。
- 证据(repro_ssrf2.py + 端到端 websec-verify/v_web03.py,已运行):
  ```
  POST /post add_radio -> 200  outbound: HEAD/GET http://169.254.169.254/stats?json=1
  queue titles: ['INTERNAL-SERVICE-TITLE-LEAKED']   (对方 JSON 的 servertitle 被回显为队列标题)
  /api/search/add url builder received: http://127.0.0.1:8181/internal  (无主机过滤直达 yt-dlp)
  ```
- 影响:认证后(none 模式=任何可达访问者)驱动 bot 探测内网主机/端口。
- 更正(采纳复核):radio 探测路径**固定**(只拼 `/stats?json=1`、`/status-json.xsl`),攻击者控 host:port 但不能构造
  `/latest/meta-data/...`,故"读云元数据端点"对 radio 路径**不成立**——实际是盲 SSRF/内网探测,外加 servertitle 回显;
  url 类型走 yt-dlp 完整 URL(路径可控但不回显,盲),`file://` 被 yt-dlp 默认禁用。NAS 家用场景云元数据基本不适用。
- 跨组(与命令组共识):聊天 `!radio`/`!rbplay`/`!live`(commands/sources.py:165-182、271;commands/streaming.py)**同样缺** is_public_url,
  故本条不是"聊天已防、Web 遗漏"的单纯回归;修复须同时覆盖聊天侧与 Web 侧。
- 修复:所有取 URL 入口统一 `util.is_public_url(url)`;修复聊天侧 `!radio`/`!rbplay`/`!live`。
- 置信度:Confirmed。

### WEB-09 [Medium / Confirmed · 复核(额外观察)] `/post delete_item_from_library` 绕过 delete_allowed 安全开关
- 位置:`interface.py:498-504`(delete_item_from_library 分支):
  ```python
  _id = payload['delete_item_from_library']
  var.playlist.remove_by_id(_id)
  item = var.cache.get_item_by_id(_id)
  if os.path.isfile(item.uri()):
      os.remove(item.uri())        # 无 delete_allowed 检查
  var.cache.free_and_delete(_id)
  ```
- 问题:`/library action=delete`(interface.py:686)会检查 `config.getboolean('bot','delete_allowed')`,而 `/post`
  的 delete_item_from_library 分支**完全不检查**,直接删库文件 + 从磁盘 `os.remove`。两个入口的授权判定不一致。
- 证据(repro_legacy.py,已运行):`delete_allowed=False` 下 `POST /post {delete_item_from_library: <id>}` 仍删除了磁盘文件
  (`delete status 200 file still exists: False`)。
- 影响:运维以为关了 delete_allowed 即禁止 Web 删库,实际该开关可被另一入口绕过;任何已认证用户可删除库文件
  (需知道/枚举 item id,id 为内容 md5,可从 /playlist、/api/queue、/library 查询结果取得)。
- 修复:该分支加入与 `/library delete` 一致的 `if not config.getboolean('bot','delete_allowed'): abort(403)`。
- 置信度:Confirmed。

### WEB-10 [Medium / Confirmed · 红队 RT-01] 旧 `/library` 删除分支路径穿越 → rmdir 曲库外空目录
- 位置:`interface.py:698-699`:
  ```python
  if len(os.listdir(var.music_folder + payload['dir'])) == 0:
      os.rmdir(var.music_folder + payload['dir'])
  ```
  `build_library_query_condition`(interface.py:598-604)仅在 `type == 'file'` 时用 `dir` 做 path LIKE,**从不校验 `..`**;
  删除分支里 `dir` 是裸字符串拼到 music_folder 之后。
- 问题:`dir` 来自表单,无任何路径规范化。取 `type=url`(非 file)时 dir 不进查询条件,可自由设成 `../..../x`。
- 证据(websec-redteam/repro_rmdir_traversal.py,已运行):
  ```
  attacker dir param: ../OUTSIDE_victim_dir
  POST /library -> 302 ; victim dir STILL exists: False
  RESULT: VULNERABLE - directory outside music_folder was removed
  ```
  入口:`POST /library {action:delete, type:url, dir:'../OUTSIDE_victim_dir', tags:'', keywords:''}`;
  需 `delete_allowed=True`(默认 False)且查询至少命中 1 条库记录以进入删除分支。
- 影响:开了 delete_allowed 的部署,任何已认证用户可令 bot 对曲库外的**空目录** rmdir(DoS/误删);同一请求亦按条件批量
  os.remove 命中的库文件。路径穿越本身与 delete_allowed 无关,是独立的输入校验缺失(与 WEB-07 同根)。
- 修复:删除分支复用安全路径解析(resolve_target_dir 风格:abspath 落在 music_folder 内),或 `if '..' in dir.split('/'): abort(403)`。
- 置信度:Confirmed。

### WEB-11 [Medium / Confirmed · 红队 RT-02] 分片上传无空间预留/无挂起配额 → 认证后磁盘耗尽 DoS
- 位置:`web_upload.py:218-219`(`free = disk_usage(...).free; if size*1.05 > free:` — size 为攻击者声明值,且**不预扣**)、
  `web_upload.py:38-39`(CHUNK_SIZE 32M / STALE_SECONDS=24h)、全文件无"挂起上传计数/上限";
  附带 `upload_status`(:289)/`upload_cancel`(:294)未调 `_check_enabled()`(仅 init/chunk/finish 调了)。
- 问题:(a) 空间检查只比"单个声明 size vs 当前 free",不预留;N 个并发 init 看到同一份 free 全部放行。
  (b) 单身份/全局挂起上传数量无上限。(c) 半成品 `.part` 仅 >24h 才清理。
- 证据(websec-redteam/repro_upload_space.py,已运行,free 伪造为 100MB):
  ```
  init 0..7 -> 200 (each)；accepted uploads: 8 | declared total = 796,917,760 B | free claimed = 104,857,600 B
  => 合计声明是可用磁盘的 7.6 倍；STALE_SECONDS = 86400
  ```
- 影响:tmp_folder 常与曲库/缓存同盘;写满后缓存下载、边下边播、合并、sqlite 写入全失败 → 全功能 DoS。
  触发者:任何已认证用户(none 模式=任何访问者)。
- 修复:init 按 size 预扣运行时"已承诺字节"(完成/取消/过期归还),用 `free - committed` 判断;设单身份/全局挂起上限;
  STALE_SECONDS 降到数小时并在 init 顺手 prune;给 status/cancel 补 `_check_enabled()`。
- 置信度:Confirmed。

### WEB-12 [Medium / Likely · 红队 RT-03] Mumble 绑定码偏弱 + 限速键可轮换 → 劫持他人 Web 身份的个人歌单
- 位置:`web_users.py:253`(`code = f"{secrets.randbelow(1000000):06d}"`,仅 6 位十进制,空间 1e6)、
  `web_users.py:33`(`BIND_CODE_TTL = 600`)、`web_users.py:262-275`(consume 把任意 mumble_key 绑到码对应 identity);
  限速:`commands/personal.py:20-22`(MAX_FAILURES=5 / LOCKOUT_SECONDS=600,`_failures` 以 mumble_key 为键)、
  `:54-56`;未注册用户 key = `cert:<hash>` 或 `name:<名字>`,由攻击者自选。
- 问题:绑定码 6 位十进制、TTL 600s;唯一防爆破是按 mumble_key 的 5 次/10 分钟锁定,但未注册用户 key 可轮换
  (改名/换证书即得全新 key,锁定重置)→ 实际可无限尝试。消费侧"谁先报对码,谁的 Mumble 账号就绑到该 Web 身份"。
- 证据(websec-redteam/repro_bind.py,已运行):
  ```
  attacker consumed victim code -> bound identity: victim@example.com
  attacker can read victim playlists via chat: ['My Private List']
  key 'name:bot0' locked after 5 fails: True ; rotate to 'name:bot1' locked: False  (锁定绕过)
  ```
- 影响:攻击者把自己 Mumble 账号绑到受害者 Web 身份后,聊天 `!mylist`/`!fav` 读写受害者个人歌单(窃取/篡改/投毒),
  点歌计到受害者名下。不授予 Web 登录态(Web 身份仍来自 Access 邮箱/用户名),故 Medium。端到端在线爆破(~1e5+ 次 `!bind`)属 Likely。
- 修复:绑定码用更大空间(≥8–10 位或字母数字 token_hex);缩短 TTL;限速键改用不可轮换标识(优先注册 uid;未注册用户按全局/频道维度限速或拒绝绑定),并对全局 `!bind` 失败做总量限速。
- 置信度:Likely(接管机制与限速绕过 Confirmed;端到端爆破 Likely)。

### WEB-04 [Low / Confirmed · 组长初审(降级)] 旧版 jQuery 前端存储型/DOM XSS(path/url/title/tag 未转义)
- 位置:后端 `interface.py:350/353/357`(/playlist:`path=f' <a href="{item.url}">...'` 未转义)、`:727/730`(/library 结果 path=item.url);
  前端注入点 `web/js/main.mjs:125-130`(`.html(item.path/title/type)`)、`:594-599`、`:616`、`:567`(tag `.html()`)。
- 问题:URL 条目的 url(经 /post add_url)与 tag(经 /library edit_tags)无 HTML 转义,旧前端用 jQuery `.html()` 注入 → 存储型 XSS。
  复核补充向量:`item.title` 亦未转义,而 URL 条目标题来自 yt-dlp 抓取的远程元数据(发布一个标题含 HTML 的视频即可,不必有 Web 权限)。
- 证据(websec-verify/v_web04.py,真实 /playlist 路由,已运行):后端响应原样带回
  `... <img src=x onerror=alert(document.domain)> ...` 与 `title: <img src=x onerror=alert(2)>`。
- 降级理由(采纳复核):主 `Dockerfile` 只构建 webui/(Vue),不构建旧前端;`web/templates/index.<lang>.html` 与 static/js 不在 git,
  `_legacy_index`(interface.py:257-263)模板缺失时返回 404。故主镜像 `/legacy` 为 404、`/` 走 Vue dist,XSS 无落点;
  仅 `Dockerfile.local`(上游遗留)/手动构建旧界面的部署受影响。新 Vue 前端 grep v-html/innerHTML 无命中。由 Medium 降 Low。
- 修复:后端对拼进 HTML 的 url/path/title/tag 转义(或旧前端改 `.text()`);建议下线旧 UI 的 HTML 片段渲染。
- 置信度:Confirmed(缺陷属实,主部署不可达)。

### WEB-05 [Low / Confirmed · 组长初审(降级)] Web 访问令牌熵低且经 URL 传递
- 位置:`commands/web.py:25`(`secrets.token_urlsafe(5)`,7 字符/~40 bit)、`:32`(拼成 `/?token=`)、`interface.py:155-164`(从 request.args 取)。
- 问题:令牌 40 bit 且作为 GET 查询参数,进浏览器历史/Referer/访问日志(web_logfile 为空时也进 docker logs 的 werkzeug 行)。
- 证据(websec-verify/v_web05.py,已运行):`token sample len 7 bits 40`;werkzeug 访问日志行原样记录 `GET /?token=...`。
- 降级理由(采纳复核):在线暴破不现实(合法令牌数≈用户数,命中概率 N/2^40);真实风险是侧信道泄露后令牌**永不过期**
  (直到用户再 `!web`),且 `!web` 非 admin,任何能私聊 bot 的成员都能领取。仅 token 模式相关。由 Medium 降 Low。
  同时撤回初审"配合 WEB-02 使暴破门槛降低"的夸大表述。
- 修复:一次性票据(换 cookie 后失效)+ 302 去掉 URL 中 token;长度 ≥16 字节。
- 置信度:Confirmed。

### WEB-06 [Low/Info / Confirmed · 组长初审(降级)] Web 层无权限分级 + 默认 auth_method=none
- 位置:`configuration.default.ini:88`(`auth_method = none`);所有端点仅过 `requires_auth`,none 模式直接放行
  (interface.py:192-194);无按用户的授权判定。`interface.py:640-641` 的 `is_admin(user)` 用模块级全局 `user`,
  cloudflare/JWT 身份下该全局停在 `'Remote Control'`。
- 降级理由(采纳复核):默认 `enabled = False`(ini:89)且 `listening_addr = 127.0.0.1`(ini:92);docker-compose.yml 默认**不**发布端口
  (ports 注释 + 说明),WEBUI.md 明确"不要暴露 8181"——这是文档化的设计取舍。`is_admin(user)` 的影响被初审夸大:它仅用于
  `/library/info` 返回给前端的 upload_enabled/delete_allowed **显示标志**;真正执行侧只看配置开关(除 WEB-09 那个漏检的分支),
  不存在"管理员判定失效导致提权"。由 Medium 降 Low/Info(保留为设计说明 + 建议启动告警)。注:全局 `user` 亦有多线程串号竞态(非安全主因)。
- 置信度:Confirmed。

### WEB-08 [Low / Confirmed · 组长初审(事实已更正)] 状态变更端点无 CSRF 防护且接受表单编码
- 位置:`interface.py:410`(/post `request.form`)、web_api/web_users/web_cache/web_channels 的 `get_json(silent=True) or request.form`;
  无 CSRF token,无 Origin/Referer/CORS 校验。
- 事实更正(采纳复核):Flask 的 `SESSION_COOKIE_SAMESITE` 默认是 **None(不设置该属性)**,并非"默认 Lax";实测 Set-Cookie 为
  `session=...; HttpOnly; Path=/`,无 SameSite、无 Secure。Chrome/Edge 对无 SameSite 的 cookie 按 Lax 处理,Firefox/Safari 不一定,
  故并非"现代浏览器一律拦截"。
- none 模式补充:`auth_method = none`(文档默认部署)下不靠 cookie,跨站表单 POST 无需任何凭据即可打 `/post action=clear`、`/api/queue`
  (复核 v_web08.py 实测均 200);Flask 不校验 Host,配合 DNS 重绑定可从任意网页命中 127.0.0.1:8181 或已知内网地址。
- 证据(websec-verify/v_web08.py,已运行):none 模式跨站 form POST 200;`SESSION_COOKIE_SAMESITE=None, SECURE=False, HTTPONLY=True`;
  GET 路由中无状态变更端点。
- 影响:cookie(token)模式下实际可利用性被浏览器默认削弱(Low);none 模式 + 浏览器可达 bot 时上升到 Medium。
- 修复:写端点只收 `application/json`(触发预检)并校验 Origin/Host;显式 `SESSION_COOKIE_SAMESITE='Lax'/'Strict'` + Secure。
- 置信度:Confirmed。

### WEB-13 [Low / Confirmed · 红队 RT-04] 旧 `/post`/`/playlist`/`/download` 缺输入校验 → 未捕获 500
- 位置:`interface.py:322-323`(`int(request.args['range_from'/'range_to'])` 无 try)、`:462/465`、`:485/488`、`:492`
  (`var.playlist[int(...)]` / `float(payload['move_playhead'])` + current_item() 可为 None)、`:815-816`(download 的 `query_music(...)[0]`,id 不存在 → IndexError,在 try 之外)。
- 证据(websec-redteam/repro_500.py,已运行):`play_music=[1]`(list)、`delete_music="x"`、`move_playhead="x"`、`/playlist?range_from=abc` 均 500。
- 影响:认证后稳定打 500(轻量 DoS/噪声);**无信息泄露** —— 复核/红队均确认 Flask 3.1.3 下 `web.env='development'`(bot/startup.py:40)
  是废弃惰性属性,`web.run()` 未开 debug,`app.debug` 恒 False,500 返回通用页、无 traceback、无交互式调试器。
  附:`/post delete_music` 的 `len>=index`(:465)用长度比索引,允许越界/负索引删到非预期条目(状态破坏)。
- 修复:旧路由 int/float 解析统一 try/except → abort(400),并 `0 <= index < len` 边界校验(与 web_api 的 _queue_remove/_queue_move 一致);download 的 `[0]` 前判空。
- 置信度:Confirmed。

---

## 严重度裁决(组长最终 · 对复核建议逐条交代)
- WEB-07:Low → **High**。采纳复核/红队,定性更正(任意位置文件写,root 容器下达 RCE)。
- WEB-01:Critical → **High**。采纳复核。理由:确为零成本完全绕过,但需 token 模式 + 未改默认密钥 + 可达三者同时成立,
  且默认 enabled=False/127.0.0.1,降一档;仍列为最高优先级之一(example.ini 推荐 token,现实不罕见,接近 Critical)。
- WEB-02:High → **Medium**。采纳复核。理由:仅 password/token 模式;最坏为限速绕过 + 定向封禁 DoS,非直接接管;token 40bit 暴破不现实。
- WEB-03:High → **Medium**。采纳复核。理由:需认证;radio 为盲 SSRF + 路径固定(已更正"云元数据"表述);file:// 禁用;NAS 场景。
- WEB-04:Medium → **Low**。采纳复核。理由:主 Dockerfile 不构建旧前端,/legacy 404,主部署不可达。
- WEB-05:Medium → **Low**。采纳复核。理由:40bit 在线暴破不现实,主风险为泄露 + 永不过期;仅 token 模式。
- WEB-06:Medium → **Low/Info**。采纳复核。理由:enabled=False + 127.0.0.1 + 不发布端口的文档化取舍;is_admin 仅影响 UI 标志。
- WEB-08:维持 **Low**(none 模式注 Medium)。采纳复核的 SameSite=None 事实更正与 none 模式场景补充。
- WEB-09/10/11:**Medium**;WEB-12:**Medium**(端到端 Likely);WEB-13:**Low**。均采纳对应来源评级。
- 未采纳项:无。(复核对 WEB-01 曾提"偏向更低",组长按上述理由定格 High 而非更低,理由已在该条写明。)

## 剔除 / 降级记录(合并三方,一行一条)
- 旧 /upload 文件名路径穿越(初审剔除)→ **撤销剔除**,见 WEB-07(复现逃逸)。
- /download 任意文件读取 → 剔除。id 走参数化 `Condition.and_equal`,uri() 仅库内条目,zip 只枚举库内 uri;id 不存在时 `[0]` 抛 IndexError → 500(已并入 WEB-13,非读文件)。
- resolve_target_dir / 分片上传路径穿越 → 剔除。按 `..` 分段拒绝 + `path==root or startswith(root+os.sep)`,upload_id 受 `^[0-9a-f]{32}$` 约束,校验正确。
- SQL 注入(Condition)→ 剔除。值全 `?` 占位,列名固定;order_by/limit/offset 取内部常量/int。LIKE 里用户可注入 `%`/`_` 通配符,仅影响匹配范围。
- 别名注入 XSS → 剔除。`_ALIAS_RE=^[^<>&"'\x00-\x1f]+$`(web_users.py:35)+ 长度限制,不通过 400。
- JWT 校验 → 无缺陷。固定 `algorithms=['RS256']`,校验 aud 与 issuer,JWKS 来自配置 team 域;启用后无 JWT 一律 403。alg 混淆/none/aud 缺失均规避。
- web.env='development' 泄露 traceback / 开调试器(红队剔除)→ 剔除。Flask 3.1.3 下该属性为废弃 no-op,不影响 app.debug;web.run() 未传 debug。
- web_search `ytsearchN:` 前缀注入任意 extractor(红队剔除)→ 剔除。固定拼 `ytsearch{limit}:{query}`,limit 为受限 int,query 落冒号后被当搜索词;走库 API 非 CLI。
- 新上传 magic 校验被首分片绕过(红队降级)→ 不单报。magic 跑在合并后整文件;其弱点(octet-stream/magic 不可用时放行)等价 WEB-07 的"可写任意内容文件"。
- send_from_directory(/assets、/app/<path>)穿越(红队剔除)→ 剔除。Werkzeug safe_join 挡 `..`。
- alias 唯一索引 TOCTOU / touch 并发首插 IntegrityError(红队剔除)→ 剔除。唯一索引兜底,至多 500,无身份混淆;touch 异常被 try/except 吞。
- /download zip 内存耗尽(红队降级)→ 不单列。zipdir 写盘且按 hash 缓存复用,量级有限,归一般 DoS。

## 覆盖范围声明
- 逐行通读并核对行号:interface.py(全)、web_users.py(全)、web_api.py、web_upload.py、web_cache.py、web_channels.py、web_search.py、
  commands/web.py、commands/personal.py(绑定/歌单聊天侧)、commands/sources.py(URL 护栏对照)、util.py(zipdir/get_url_from_input/is_public_url/bilibili)、
  media/radio.py、media/url.py(yt-dlp 调用点)、bot/player.py(ffmpeg argv 构造)、database.py(Condition/查询)、bot/startup.py、configuration.default/example.ini [webinterface]。
- 前端:webui/src/api.ts(请求构造、无 token 落地、靠 Access cookie)、全量 grep v-html/innerHTML/window.open/location(新前端仅安全 :href);旧 web/js/main.mjs 的 `.html()` 注入点逐一核对。
- 复现(均 python + Flask test client):WEB-01/02/03/07(组长 websec/,repro_upload 隔离在 websec/sandbox)、WEB-04/05/06/08/09(复核 websec-verify/)、WEB-10/11/12/13(红队 websec-redteam/);组长已重跑关键脚本并复核红队/复核的行号。
- 未深入:yt-dlp/ffmpeg 进程参数(已核 argv 形式,URL 作独立参数,无 shell/选项注入;走库 API 非 CLI);playlist_import 各平台内部请求(入口校验已核:detect_source 限 host + http(s) + 长度;内部逻辑属导入/媒体组);spotdl(媒体组);无真实 Mumble,聊天侧仅代码级复现绑定流程。
- 环境清理提示:复核工人首次验证 WEB-07 时有测试文件误写到沙箱外(/three.txt、/tmp/two.mp3、/tmp/raw.mp3、/tmp/tmpcq5nuity/),其删除被安全检查拦截、未绕过,需人工清理;组长自己的 repro_upload.py 全程限定在 websec/sandbox 内。仓库内无任何文件被修改(git status 干净)。
