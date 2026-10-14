# Web 安全组复核报告(websec-verify)

复核人:Sonnet 工人。仓库 /home/user/Mumble-DJBot,分支 claude/quirky-fermi-6nou7s,全程只读,无 git 写操作。
复现脚本(新写)在 audit/repro/web-verify/:v_common.py(公共桩,music 目录隔离在 sandbox/ 下)、
v_web01_02.py、v_web03.py、v_web04.py、v_web05.py、v_web07.py、v_web07b.py、v_web08.py。
原有 4 个脚本也已用 python 重跑(见各节)。

## 总表

| ID | 原严重度 | 结论 | 我的严重度 | 要点 |
|---|---|---|---|---|
| WEB-01 | Critical | Confirmed | High | 伪造 cookie 复现成功;但仅 token 模式 + 默认密钥才触发,项目自己的部署文档用的是 none + Access |
| WEB-02 | High | Confirmed | Medium | 绕过封禁 + 封禁受害者 IP 均复现;none 模式根本不计数,文档部署不受影响 |
| WEB-03 | High | Partially confirmed | Medium | 端到端复现外联;但 radio 探测路径固定、基本是盲 SSRF;且聊天 !radio/!live 同样没有护栏(报告说"聊天已防"不完整) |
| WEB-04 | Medium | Partially confirmed | Low | 后端未转义 + 旧前端 .html() 属实;但项目 Dockerfile 根本不构建旧前端,/legacy 返回 404 |
| WEB-05 | Medium | Partially confirmed | Low | 40 bit、令牌进 URL、werkzeug 访问日志里明文都属实;暴破不现实 |
| WEB-06 | Medium | Partially confirmed | Low / Info | 默认 enabled=False 且监听 127.0.0.1;is_admin(user) 只影响 UI 标志,不构成提权 |
| WEB-07 | Low | **Refuted(定性错误,实际更严重)** | **High** | 旧 /upload 的 filename 未做 basename,存在任意位置新建文件的路径穿越,已复现;报告的"已剔除:路径穿越"是错的 |
| WEB-08 | Low | Partially confirmed | Low(cookie 模式)/ Medium(none 模式) | Flask SameSite 默认是 None 而不是 Lax;none 模式下无 cookie,跨站表单 POST 直接 200 |

## 行号核对(报告引用 vs 当前代码)

| 报告引用 | 实际 |
|---|---|
| configuration.default.ini:90 flask_secret | 正确 |
| bot/startup.py:41 secret_key | 正确 |
| interface.py:155-158 token 分支 | 正确(155 `if auth_method == 'token'`,156 `'user' in session`) |
| interface.py:41-44 ReverseProxied | **应为 61-63**(`real_ip = ...` 在 61,`environ['REMOTE_ADDR']=` 在 63) |
| interface.py:126 banned_ip 检查 | **应为 131** |
| interface.py:137-153 / 172-183 计数 | 142 与 177 处自增,大体对得上 |
| interface.py:446-450 add_url / 452-457 add_radio | 实际 445-452 / 454-459(偏差 1-2 行) |
| interface.py:351-355 /playlist path | 实际 350 / 353 / 357 |
| interface.py:689-691 /library path=item.url | **应为 727 / 730** |
| web/js/main.mjs:130、594-599、616、567 | 正确 |
| interface.py:730 mimetype 判断 | **应为 784** |
| interface.py:150-164 token 读取 | 实际 155-164 |
| interface.py:189-191 none 模式放行 | 实际约 192-194 |
| interface.py:640-641 is_admin(user) | 正确 |
| commands/web.py:25、32 | 正确 |
| commands/sources.py:123、149 | 正确 |

---

## WEB-01 默认 flask_secret 伪造会话 cookie

**结论:Confirmed。**

**理由**
- 代码路径无歧义:token 模式下 `if 'user' in session and 'token' not in request.args` 直接放行(interface.py:156-158),
  不回查 `web_token` 库;session 用 `flask_secret` 签名,默认值 `ChangeThisPassword` 写死在 configuration.default.ini:90
  和 example.ini:234,全仓库没有任何启动时校验或告警(grep ChangeThisPassword 仅这两处)。
- 漏看的缓解:(1) 项目自己的部署(deploy/WEBUI.md)明确用 `auth_method = none` + Cloudflare Access,
  token 模式不在该路径上,所以对现有部署不构成直接风险;(2) 默认 `enabled = False`,监听 127.0.0.1。
  触发条件是"运维改成 token 模式却没改密钥",example.ini:233 其实写了 "absolutely necessary",但默认值就是公开串。
- 原 repro_auth.py 的 "A" 项返回 500 只是测试桩缺模板,说服力弱;我用 DictLoader 补了 need_token 模板重做对照:

**复现输出(v_web01_02.py)**
```
A no-cred: 200 b'NEED_TOKEN_PAGE'
B forged cookie (default secret): 200 b'{"current":null,"current_index":-1,"ducking":false,"empty":t'
B2 forged cookie vs non-default secret: 200 b'NEED_TOKEN_PAGE'     # 对照:换了密钥就被拒
B3 forged cookie POST /api/queue clear: 200 ...                    # 破坏性接口同样放行
```
原 repro_auth.py 重跑:`A no-cred /api/status: 500`(缺模板)、`B forged-cookie /api/status: 200`,与报告一致。

**严重度意见:Critical 偏高,建议 High。** 绕过本身是零成本完全绕过,但前置条件(token 模式 + 默认密钥 + 可达)较窄,
且不是项目文档化的部署形态。修复建议同报告:默认值为空/等于占位串时拒绝启动或自动生成并持久化随机密钥。

---

## WEB-02 封禁/限速基于可伪造 X-Real-IP

**结论:Confirmed。**

**理由**
- `ReverseProxied` 无条件采信 `HTTP_X_REAL_IP`(interface.py:61-63),`is_web_proxified` 默认 True(ini:91,bot/core.py:45 读取)。
- 封禁表与失败计数都以 `request.remote_addr` 为键(131、142、177),因此既能轮换 IP 绕过,也能冒用受害者 IP 触发封禁。
- 漏看的缓解/加重:
  - `auth_method = none`(项目文档部署)下根本没有计数逻辑(下面 G 项),所以文档部署不受影响;只影响 password/token 模式。
  - 经 Cloudflare Tunnel 时 cloudflared 转发的是客户端自带的 X-Real-IP(据我所知 Cloudflare 不会改写它,未实测),所以前置代理并不能阻止伪造。
  - 额外观察:`bad_access_count` 字典对每个伪造 IP 新增一项,无上限、无清理(缓慢内存增长);
    `commands/web.py:17-18` 的 `!web` 任何 Mumble 用户都可执行并清空封禁表(该命令不是 admin 命令)。

**复现输出(v_web01_02.py)**
```
C attacker rotating IP, 100 bad tokens -> banned: [] tracked ips: 100
D victim 203.0.113.7 banned after spoofed bad tokens: ['203.0.113.7']
  victim next request: 403
  other ip request: 200
E password mode victim banned: ['203.0.113.9']
F is_web_proxified=False: banned (real peer 127.0.0.1): ['127.0.0.1']   # 对照:关掉 proxified 封禁正常
G none mode banned/counts: [] {}
```
原 repro_legacy.py 重跑:`banned after 5 bad tokens w/ rotating X-Real-IP: []`,一致。

**严重度意见:High 偏高,建议 Medium。** 机制成立(两个方向都复现),但仅在 password/token 模式生效;
令牌 40 bit,在线暴破本身不现实;密码模式下暴破才是实际风险。

---

## WEB-03 Web 取 URL 接口缺 is_public_url → SSRF

**结论:Partially confirmed(漏洞成立,但影响与"回归"定性有偏差)。**

**理由**
- 已确认:`interface.py:445`(add_url)、`454`(add_radio)、`web_api.py:289-310`(search/add,只校验 http/https 前缀)、
  `web_users.py:478-499 wrapper_from_entry` 均未调用 `util.is_public_url`;全仓库只有 commands/sources.py:123、149 两处调用。
- **报告漏看/表述不准的点**
  1. `commands/sources.py:165-182 cmd_play_radio`、`cmd_rb_play`(271)、`commands/streaming.py` 的 `!live`/bilibili 都**没有** is_public_url。
     所以 radio 这条(也是唯一复现的一条)聊天侧同样可打,而且聊天用户群(任何 Mumble 用户)比 Web 用户更宽。
     "聊天路径已防,Web 路径遗漏,是明确的回归"只对 `!play`/`!playlist` 成立。
  2. radio 探测的路径是固定的:`media/radio.py:19-26` 只取 `base_url`(scheme+host:port),再拼 `/stats?json=1`、`/status-json.xsl`。
     攻击者控制 host:port,但**不能**构造 `/latest/meta-data/...`,报告里"读云元数据端点"对 radio 路径不成立;
     可做的是内网主机/端口探测(通过有无响应、耗时、以及 JSON 里 `servertitle` 键被回显为队列标题)。
  3. `url` 类型走 yt-dlp 的完整 URL(路径可控)但结果不回显,属盲 SSRF;`file://` 被 yt-dlp 默认禁用(我实测:"file:// URLs are disabled by default")。
  4. 部署背景是家用 NAS,云元数据基本不适用;内网扫描/打内网管理接口才是现实威胁。
- 注:`is_public_url` 本身有 DNS 重绑定/重定向绕过空间,但这是聊天侧同样存在的基线问题,不在本条范围。

**复现输出(v_web03.py,走真实 /post 路由 + 真实 item_builders,仅把 requests 打桩)**
```
POST /post add_radio -> 200 outbound: [('HEAD', 'http://169.254.169.254/stats?json=1'), ('GET', 'http://169.254.169.254/stats?json=1')]
queue titles: ['INTERNAL-SERVICE-TITLE-LEAKED']      # 对方 JSON 的 servertitle 被当作队列标题回显
url builder received: ['http://127.0.0.1:8181/internal']   # /api/search/add 无主机过滤直达 URL builder
is_public_url http://169.254.169.254/latest/meta-data/ False   # 对照:聊天护栏会拒绝
```
原 repro_ssrf2.py 重跑:`RadioItem construction made outbound request to: ['http://169.254.169.254/stats?json=1']`。
原 repro_ssrf.py 重跑仍然失败(`FakeCache` 没有 `get_item`,返回 500、hits 为空)——该脚本本身是坏的,
报告引用的是 repro_ssrf2.py,但它只是直接调 builder,没走 HTTP 路由;我的 v_web03.py 补上了端到端。

**严重度意见:High 偏高,建议 Medium。** 需认证(或 none 模式下可达),radio 路径盲且路径固定。
修复应一并覆盖聊天侧的 `!radio`/`!rbplay`/`!live`,而不只是 Web 入口。

---

## WEB-04 旧版 jQuery 前端存储型/DOM XSS

**结论:Partially confirmed(代码缺陷属实,但项目部署下不可达)。**

**理由**
- 后端未转义已复现;旧前端 `.html(item.path/title/tag)` 在 web/js/main.mjs:125-130、594-599、567、616 均属实。
  报告漏了一个更广的向量:`item.title` 同样未转义直接 `.html()`,而 URL 条目的标题来自 yt-dlp 抓到的远程元数据
  (攻击者发布一个标题带 HTML 的视频即可,不必有 Web 权限)。
- **漏看的关键缓解**:旧前端是 webpack 构建产物,仓库只提交了 `web/templates/*.template.html`,
  `index.<lang>.html` 与 `static/js` 都不在 git 里;项目的 `Dockerfile` 只构建 `webui/`(Vue),
  没有 `translate_templates.py`、没有 web/ 的 npm build,仅 `Dockerfile.local`(上游遗留)会构建旧界面。
  `interface.py:257-263 _legacy_index` 在模板缺失时直接返回 404 "Legacy interface not built"。
  所以在项目的 Docker 部署里 `/legacy` 是 404,`/` 走 Vue dist,XSS 没有落点。
  新前端 grep `v-html|innerHTML|insertAdjacentHTML|bypassSecurity` 无命中,确认。

**复现输出(v_web04.py,真实 /playlist 路由)**
```
200
path :  <a href="http://example.com/a"><img src=x onerror=alert(document.domain)>"><i>http://example.com/a"><img src=x onerror=alert(document.domain)></i></a>
title: <img src=x onerror=alert(2)>
```
(只证明后端响应带原始 HTML;没有浏览器环境,前端执行部分靠读 `.html()` 调用确认。)

**严重度意见:Medium 偏高,建议 Low**(仅 Dockerfile.local / 手动构建旧界面的部署受影响)。
最省事的修复是下线 `/legacy` 与 `/playlist`、`/library` 的 HTML 拼接。

---

## WEB-05 令牌熵低且经 URL 传递

**结论:Partially confirmed。**

**理由**
- 事实核对:`commands/web.py:25` `secrets.token_urlsafe(5)`(7 字符、40 bit),`:32` 拼成 `/?token=`,
  `interface.py:155-164` 从 `request.args` 取。werkzeug 访问日志会原样记录查询串;
  bot/startup.py 在 `web_logfile` 为空时用 StreamHandler(docker logs 里可见)。
- 暴破不现实:即使完全绕过封禁(WEB-02),合法令牌数 = 用户数(个位数),命中概率约 N/2^40,量级 10^11 次请求。
  报告里"配合 WEB-02 门槛进一步降低"夸大了。
- 实际风险是日志/历史/Referer 泄露后令牌**永不过期**(直到该用户再 `!web`),且 `!web` 非 admin 命令,
  任何能私聊 bot 的 Mumble 用户都能领取令牌(这是设计,但意味着 token 模式的"鉴权"边界就是 Mumble 服务器成员)。
- 仅 token 模式相关;项目文档部署(none)不涉及。

**复现输出(v_web05.py)**
```
token sample: b5ax6EU len 7 bits 40
werkzeug access log line: 127.0.0.1 - - [08/Oct/2026 09:47:53] "GET /?token=b5ax6EU HTTP/1.1" 200 -
```

**严重度意见:Medium 偏高,建议 Low。** 修复:换成一次性票据(换 cookie 后即失效)+ 302 去掉 URL 里的 token;长度至少 16 字节。

---

## WEB-06 无权限分级 + 默认 auth_method=none

**结论:Partially confirmed。**

**理由**
- 属实:ini:88 `auth_method = none`;所有端点只过 `requires_auth`,没有按用户的授权判定。
- 漏看的缓解:默认 `enabled = False`(ini:89)且 `listening_addr = 127.0.0.1`(ini:92);docker-compose.yml 默认**不**发布端口
  (ports 被注释并附说明),WEBUI.md 与 docker-compose.yml 注释都明确写了"不要暴露 8181"。这是文档化的设计取舍,不是疏忽。
- `is_admin(user)`(interface.py:640-641)的指控成立但影响被夸大:`user` 是模块级全局(也有多线程间串号的竞态),
  但它只用于 `/library/info` 返回给前端的 `upload_enabled/delete_allowed` 显示标志;
  真正的执行侧(`/upload` 762 行、`/library delete` 686 行,而 `/post delete_item_from_library` 496-506 行完全不查开关)只看配置开关,不看 admin,所以不存在"管理员判定失效导致提权/越权"。
- 复现:原 repro_legacy.py 重跑 `upload status 200`、`delete status 200 file still exists: False`(none 模式放行,delete_allowed=False 却被删掉)。
  注意 delete 那项:`/post delete_item_from_library` 在 `delete_allowed=False` 时仍删了文件——这不是 none 模式特有的问题,
  是该端点本身没检查 delete_allowed(与 `/library action=delete` 不一致),值得单独跟进(见文末"额外观察")。

**严重度意见:Medium 偏高,建议 Low/Info(设计取舍,需文档/启动告警)。** "无 RBAC"可作为设计说明保留。

---

## WEB-07 旧 /upload 仅凭客户端 mimetype(实际:还存在文件名路径穿越)

**结论:Refuted(对"路径穿越被挡住"的判断);mimetype 伪造本身 Confirmed。整体应升级为 High。**

**理由**
- interface.py:776 只检查了 `targetdir` 里的 `'../'`;`filename = file.filename`(769)被原样 `os.path.join(storagepath, filename)`(796),
  **没有 basename / secure_filename**,而且 `abspath(...).startswith(music_folder)` 校验(786)只作用于 `storagepath`,不作用于最终 `filepath`。
  werkzeug 3.1.9 不会对 multipart 的 filename 做 basename(只处理 Windows 盘符/UNC)。
- 报告里的 repro_legacy.py 用的是 `../outside.mp3` + 默认 targetdir `uploads/`,结果落在 `music/outside.mp3`(仍在 music 内),
  而它检查的是 `music` 的**上一级**目录 → 误判为"未逃逸"。改用 `../../x.mp3` 立刻逃逸。
- 影响:任何通过 Web 鉴权(或 none 模式下可达)的人,可在容器内任意可写位置**新建**文件(已存在则 409,不能覆盖),
  文件名与内容完全可控,mimetype 只要带 "audio"/"video" 字样(客户端自报)即可。
  Docker 镜像无 `USER` 指令 → root;venv 在 `/botamusique/venv`。上传到
  `../../venv/lib/python3.12/site-packages/x.pth`(相对 `music_folder/uploads/`)即得 `.pth` 代码执行,
  而 compose 的 healthcheck 每 30 秒就会启动一次 `venv/bin/python`,等于近乎即时的 RCE(我只在沙箱目录里演示了 `.pth` 机制,没有碰真实 venv)。
- 对比:新分片上传 web_upload.py:82-85 `clean_filename` 有 basename,`resolve_target_dir` 正确——只有旧 `/upload` 漏了,
  而该路由与是否构建旧前端无关,Docker 里照样存在。`upload_enabled` 默认 True(ini:97)。

**复现输出(v_web07.py / v_web07b.py,music 目录隔离在 sandbox/<tmp>/music/)**
```
werkzeug 3.1.9
'../one.mp3' -> 200
'../../two.mp3' -> 200
  on disk: two.mp3                # 在 music 的上一级 = 逃逸
  on disk: music/one.mp3
evil.php w/ audio mimetype -> 200       # 伪造 Content-Type 即可
evil.php w/ html mimetype -> 415
raw multipart ../../raw.mp3 -> 200 True # 原始 multipart 报文同样逃逸
upload ../../sp/x.pth -> 200 ['x.pth']
marker created by .pth on interpreter startup: True    # sandbox 内演示 .pth 机制
```
原 repro_legacy.py 重跑:`upload status 200 outside file written: False`(误导性结果,原因见上)。

**严重度意见:Low → High。** 认证后的任意位置文件创建,在项目 Docker 部署(root)下可达 RCE;none 模式 + 端口可达时等同未授权 RCE(可视为 Critical)。
修复:`filename = clean_filename(file.filename)`(复用 web_upload.clean_filename)并对最终 `filepath` 再做 `abspath().startswith(root + os.sep)`;
最好直接删除旧 `/upload`(新 UI 已用分片上传)。

---

## WEB-08 状态变更端点无 CSRF 防护且接受表单编码

**结论:Partially confirmed。**

**理由**
- 属实:`/post`(interface.py:410)与 web_api/web_users/web_cache/web_channels 的 `get_json(silent=True) or request.form`
  都接受 urlencoded 表单;没有 CSRF token,也没有任何 Origin/Referer/CORS 校验(grep 无)。
- **报告的缓解前提不准**:Flask 的 `SESSION_COOKIE_SAMESITE` 默认是 `None`(未设置属性),并不是"默认 Lax";
  Set-Cookie 实测为 `session=...; HttpOnly; Path=/`,无 SameSite、无 Secure。
  Chrome/Edge 对无 SameSite 的 cookie 按 Lax 处理,Firefox/Safari 不会,所以并非"现代浏览器一律被拦"。
- **报告漏看的主要场景**:`auth_method = none`(项目默认/文档部署)下根本不靠 cookie,跨站表单 POST 无需任何凭据;
  任何网页都能让访问者的浏览器向 `http://127.0.0.1:8181`(默认监听地址)或已知 NAS 内网地址发请求(Flask 也不校验 Host,可 DNS 重绑定)。
  走 Cloudflare Access 时靠的是 Access 的 cookie(其 SameSite 取值我没有验证,不下结论)。

**复现输出(v_web08.py)**
```
none-mode form POST /post action=clear from evil Origin -> 200
none-mode form POST /api/queue (form) from evil Origin -> 200
token login -> 200 Set-Cookie: session=eyJ0b2tlbiI6...; HttpOnly; Path=/
config SESSION_COOKIE_SAMESITE = None | SECURE = False | HTTPONLY = True
GET routes: ['/', '/legacy', '/assets/<path:path>', '/favicon.svg', '/app', '/app/<path:path>', '/playlist', '/library/info']   # GET 里没有状态变更
```

**严重度意见:Low 在 cookie 模式下合理;none 模式 + 浏览器能到达 bot 时建议 Medium。**
修复:写端点只接受 `application/json`(触发预检)并校验 `Origin`/`Host`;显式 `SESSION_COOKIE_SAMESITE='Lax'`。

---

## 「已剔除/降级」6 项复核

| 项 | 复核结论 |
|---|---|
| 旧 /upload 文件名路径穿越(剔除) | **剔除错误**,见 WEB-07,已复现逃逸。 |
| /download 任意文件读取(剔除) | 同意。`id` 走参数化 `Condition.and_equal`,`uri()` 来自库内条目;zip 也只枚举库内 uri。小问题:id 不存在时 `[0]` 抛 IndexError → 500(非安全问题)。 |
| resolve_target_dir / 分片上传穿越(剔除) | 同意。web_upload.py:90-98 按 `..` 分段拒绝,并校验 `path == root or startswith(root + os.sep)`;绝对路径 targetdir 会因 join 后不在 root 下被 403;`clean_filename` 有 basename。 |
| SQL 注入(剔除) | 同意。`Condition` 的值全部 `?` 占位,列名固定;order_by/limit/offset 为内部常量或 int。LIKE 里 `%`/`_` 可被用户注入通配符,但只影响匹配范围。 |
| 别名注入 XSS(剔除) | 同意。`_ALIAS_RE = ^[^<>&"'\x00-\x1f]+$`(web_users.py:35)+ 长度限制,不通过 400。 |
| JWT 校验(未发现缺陷) | 同意。固定 `algorithms=['RS256']`,校验 aud 与 issuer,JWKS 来自配置的 team 域;启用 JWT 校验后无 JWT 头一律 403。 |

我亲自重看的是:旧 /upload(推翻)、/download、resolve_target_dir、SQL、JWT/别名(各一遍)。

## 额外观察(报告未列,供组长考虑)

1. **聊天侧 `!radio`/`!rbplay`/`!live` 缺 is_public_url**(commands/sources.py:165-182、271;commands/streaming.py),见 WEB-03。
2. `/post delete_item_from_library`(interface.py:496-506,已读代码确认无任何开关检查)不检查 `delete_allowed`(repro_legacy.py 里 `delete_allowed=False` 时仍删了文件),与 `/library action=delete`(interface.py:686 检查开关)不一致。我只通过原脚本的输出看到,未单独深入验证调用链。
3. `!web`(commands/web.py:17-18)会清空全局 `banned_ip`/`bad_access_count`,且非 admin 命令。

## 环境清理说明(请人工处理)

在第一次验证 WEB-07 时,我的测试脚本(v_web07.py 的初版,music 目录误用了 /tmp 下的随机目录)把测试文件写到了沙箱之外。
我尝试删除时被安全检查拦截(`/three.txt` 命中保护规则),按要求没有绕过。以下为我创建的、内容仅为 "PWNED"/"X" 的残留文件,可手动删除:
- `/three.txt`
- `/tmp/two.mp3`
- `/tmp/raw.mp3`
- `/tmp/tmpcq5nuity/`(含 one.mp3 与 sub/evil.php)

仓库内没有任何文件被修改(`git status` 干净);此后所有复现都限定在 audit/repro/web-verify/sandbox/ 内。
