# 命令系统 / 配置 / 部署供应链 — 工人报告(commands-worker)

审计对象:/home/user/Mumble-DJBot @ 64109cf(分支 claude/quirky-fermi-6nou7s),只读。
复现脚本目录:`audit/repro/commands/`
- `repro_dispatch.py`:stub 掉 `mumble` 模块,真实调用 `MumbleBot._handle_message` + `register_all_commands`(A–F)
- `repro_misc.py`:ReDoS 计时、URL ban 绕过、href 突破、`util.update()`(G–J)
- `repro_repeat.py`:真实 `media.playlist` 上跑 `cmd_repeat`
- `config_keys.py`:AST 提取所有 `config.get*` 引用,对比 default.ini / example.ini

运行:`cd <repo-root> && python -W ignore <script> /home/user/Mumble-DJBot`

## 总评

命令层没有 shell 注入:所有子进程都是 argv 列表(`bot/player.py:292`、`util.py:156/159/163/502/597`、`media/spotify.py:81/199`、`web_upload.py:137`),spotdl 已用 `--` 分隔,ffmpeg 的 `-i <uri>` 后面紧跟的值不会被当作选项。配置解析也很健康:代码里引用的每个 (section, key) 在 default.ini 里都有,而且 startup 先读 default.ini 再读用户 ini,旧 ini 不会因为缺 key 崩溃。

真正的问题在**身份与资源边界**上:
1. **频道里任何人都能一条消息冻结整个进程**:`!fm` / `!listfile` 把用户正则直接交给 `re.search`。CPython 的 `re` 匹配期间持有 GIL,音频、Web、看门狗线程全部停摆(CMD-01)。
2. **admin 与 Web 身份 = 裸 Mumble 用户名**:不查 `user_id` / 证书。未注册的同名访客,或开启 `split_username_at_space` 后用 "Admin (x)" 这样的名字,就能拿到 admin;`!web` / `!password` 也能把这个身份带进 Web(CMD-02)。
3. `!repeat N` 没有上限(CMD-03);SSRF 防护只覆盖了 `!url` / `!playlist`(CMD-04);`!dropdatabase` 执行后 bot 再也不响应任何命令(CMD-05);systemd 的 root 更新单元可被用于提权(CMD-06)。

---

## 发现列表

### CMD-01 [High] `!filematch` / `!listfile` 用户正则 ReDoS,持 GIL 冻结整个进程
- **位置**:`commands/sources.py:89`(`re.search(parameter, file)`)、`commands/sources.py:359`(`re.search(parameter, file['path'])`)
- **问题**:聊天参数原样作为正则执行,没有长度、复杂度或超时限制。
- **证据**:
  ```python
  # commands/sources.py:87-89
  for file_dict in file_dicts:
      file = file_dict['title']
      match = re.search(parameter, file)
  ```
  `repro_misc.py` G 段:模式 `(.*.*)*!`(聊天中输入 `!fm (.*.*)*!`)对 10/11/12/13/14/16 字符标题的耗时分别为 0.02 / 0.07 / 0.24 / 1.0 / 3.4 / 36 s,每多一个字符约 ×3。普通歌名有 30 字符以上,实际上永远跑不完。
  另测(内联脚本):正则跑在一个线程里时,另一线程最长停顿 3.70 s(正则本身 3.65 s)。也就是说 `_sre` 全程不释放 GIL。
- **影响**:与 bot 同频道的任何非 admin 用户(`fm` / `listfile` 不是 admin 命令)发一条消息,就能冻结整个 bot 进程:音频输出、pymumble 心跳、Flask Web、看门狗都停。看门狗 `bot/startup.py:284-295` 本身是 Python 线程,同样拿不到 GIL,不会触发重启。Docker healthcheck 只会标 unhealthy,不会自动重启(compose 注释也承认需要 autoheal)。前提是曲库里至少有一个 `type='file'` 的条目(本地 music_folder 或 Web 上传)。
- **修复**:不要把聊天输入当正则。改成 `re.escape(parameter)` 或子串匹配 `parameter.lower() in file.lower()`;确实要支持正则,就换 `regex` 包并设 `timeout=`,或限制长度并禁止嵌套量词。两处(89、359)一起改。
- **置信度**:Confirmed

### CMD-02 [High,有条件] admin 判定与 Web 身份只比对 Mumble 用户名,可被冒名
- **位置**:`bot/core.py:328-333`(取名字、`split_username_at_space` 截断)、`bot/core.py:427-433`(`is_admin`)、`commands/web.py:29-30`(`!web` token 绑定到名字)、`commands/web.py:39-48`(`!password` 按名字写 Web 密码)、`interface.py:640-641`(Web 端 `var.bot.is_admin(user)`)
- **问题**:`is_admin` 只做 `user in config['bot']['admin'].split(';')`,不检查 pymumble 用户对象的 `user_id`(只有注册用户才有)或 `cert_hash`。同仓库 `commands/personal.py:221-232` 的 `mumble_key()` 已经用了正确做法,admin 路径没用。
- **证据**:
  ```python
  # bot/core.py:328-333
  user = self.mumble.users[text.actor].name
  if var.config.getboolean('commands', 'split_username_at_space'):
      user = user.split()[0]
  # bot/core.py:428-430
  list_admin = var.config.get('bot', 'admin').rstrip().split(';')
  if user in list_admin:
  ```
  `repro_dispatch.py`:
  - A 段:admin=`Alice`,一个没有 `user_id` 的访客 "Alice" 发 `!kill`,结果 `bot.exit = True`。
  - B 段:`split_username_at_space=True`,访客 "Alice (guest)" 发 `!kill`,结果 `bot.exit = True`。
- **影响**:
  - Murmur 只阻止**在线**重名和**已注册**名被未注册者占用。如果 admin 名没注册(小服务器很常见,example.ini 也没要求注册),admin 离线时任何访客改用该名就是 admin:`!kill`、`!update`(跑 pip)、`!dropdatabase`(见 CMD-05,会让 bot 瘫痪)、`!webuseradd`、URL 白名单/ban 管理,还会绕过私聊限制、频道限制和 URL ban(`bot/core.py:347/358/394`)。
  - 开启 `split_username_at_space` 时,即使 admin 已注册且在线,也能用 "Admin xxx" 冒充。
  - 身份会延伸到 Web:token 模式下冒名者发 `!web`,拿到以 admin 名义的 Web 会话,`interface.py:640-641` 随即授予上传和删除权限;password 模式下 `!password` 可以覆盖该名字用户的 Web 密码(`check_auth` 只要求名字在 `web_access` 列表里)。
  - 同一根因也让 `!userban` 形同虚设:被 ban 的人改个名就行。
- **修复**:admin 配置改成按 Mumble 注册 ID(`user.user_id`)或证书哈希匹配,例如 `admin_ids = 12;34`;`is_admin` 改为接收 user 对象,要求 `getattr(u, 'user_id', None)` 存在且在列表中。名字匹配至少要额外要求 `user_id` 存在(已注册)。`split_username_at_space` 截断后的名字不应再用于 admin 判定。`!web` / `!password` 的身份键改用 `personal.mumble_key()`。
- **置信度**:Confirmed(代码路径加 harness 复现);"Murmur 是否允许未注册者用该名"取决于服务器配置

### CMD-03 [Medium] `!repeat N` 无上限:阻塞回调线程并撑爆内存
- **位置**:`commands/playback.py:187-199`;每次插入都会走 `media/playlist.py:76-89` 和 `async_validate`(`media/playlist.py:252-257`,校验线程空闲时 `time.sleep(0.1)`)
- **证据**:
  ```python
  if parameter and parameter.isdigit():
      repeat = int(parameter)
  ...
  for _ in range(repeat):
      var.playlist.insert(var.playlist.current_index + 1, music)
  ```
  `repro_repeat.py`(真实 playlist 类):`!repeat 50` 耗时 5.04 s,`!repeat 100` 耗时 10.08 s,约 0.1 s/次。`!repeat 1000000` 约 28 小时,另外每次 `list.insert` 是 O(n),还有 `pending_items` 增长和日志刷屏。
- **影响**:任何同频道用户都能让命令处理线程长时间阻塞。PROGRESS.md:165 记录"pymumble 回调持锁调用",因此连接循环大概率也会被拖住并掉线。播放队列和内存会被无限填充。
- **修复**:`repeat = min(int(parameter), 20)`;一次性构造列表后用 `extend` 或单次加锁插入,只调用一次 `async_validate`。
- **置信度**:Confirmed(阻塞);掉线为 Likely

### CMD-04 [Medium] SSRF 防护只加在 `!url` / `!playlist`,`!radio` / `!live` / `!rbplay` 可直达内网
- **位置**:`commands/sources.py:165-187`(`cmd_play_radio`,无 `is_public_url`)、`commands/streaming.py:69-72`(`!live <url>`)、`commands/sources.py:262-271`(`!rbplay`,radio-browser 返回的 URL 谁都能提交);抓取点 `media/radio.py:15-60`(`requests.head/get` 访问 `base/stats?json=1` 和 `base/status-json.xsl`)、`media/radio.py:65-87`(`requests.get(url)`)、`bot/player.py:269`(ffmpeg `-i uri`)
- **证据**:`util.is_public_url` 只在 `commands/sources.py:123` 和 `:149` 被调用(全仓 grep)。`!radio http://169.254.169.254/latest/meta-data/` 在 `RadioItem.__init__` 时就会同步发出 HEAD/GET,播放时 ffmpeg 再去 GET。
  另外 `!url` 的检查只验证首个 URL 解析出的 IP,yt-dlp 和 ffmpeg 之后会跟随 302 并重新解析 DNS,所以公网 302 跳到 127.0.0.1 或 DNS rebinding 都能绕过。
- **影响**:频道用户可以让 bot 对内网/本机发起 GET(盲 SSRF 与端口探测,"添加成功"和报错的差异可作为探测信号)。其中 `servertitle` / `server_name` 等 JSON 字段会作为电台标题回显到频道,形成有限的回读。这让 `!url` 上已有的 SSRF 修复失去意义。Web 端 `web_api.py:304`、`interface.py:446/456` 同样没有该检查,交给 Web 组。
- **修复**:`cmd_play_radio`、`_enqueue_live`、`cmd_rb_play` 入队前都调用 `util.is_public_url`;更稳妥的做法是在 `RadioItem` / `LiveStreamItem` / `URLItem` 构造或校验时统一检查。radio 的 `requests` 改为 `allow_redirects=False` 并逐跳校验(参照 `_resolve_bilibili_redirect`)。
- **置信度**:Confirmed(代码路径);回读程度取决于目标服务

### CMD-05 [Medium] `!dropdatabase` 执行后 bot 不再响应任何命令,且 music_db 被指到 settings 库
- **位置**:`commands/library.py:309-312`;受影响点 `bot/core.py:352`
- **证据**:
  ```python
  var.db.drop_table()
  var.db = SettingsDatabase(var.settings_db_path)     # __init__ 不建表(database.py:200-201)
  var.music_db.drop_table()
  var.music_db = MusicDatabase(var.settings_db_path)  # 用错路径:应为 var.music_db_path
  ```
  `repro_dispatch.py` F 段:admin `!dropdatabase` 后收到 "Database dropped",之后 `!version` 抛出 `sqlite3.OperationalError: no such table: botamusique`(`bot/core.py:352` 的 `var.db.items("user_ban")`)。异常被 `message_received` 吞掉只写日志,用户收不到任何回复;`var.music_db.db_path == settings path` 为 True。
- **影响**:admin 执行这条"官方"命令后,bot 对所有用户的所有命令都沉默,直到重启;曲库写入也会失败或写进错误的文件。结合 CMD-02,冒名者可以用它做拒绝服务。
- **修复**:重建对象后调用 `DatabaseMigration(var.db, var.music_db).migrate()` 重新建表;第 312 行改用 `var.music_db_path`(并沿用 `save_music_library` 的 `:memory:` 分支)。
- **置信度**:Confirmed

### CMD-06 [Medium] 以 root 运行的更新单元执行由 bot 用户可写的 venv,可本地提权
- **位置**:`deploy/botamusique-ytdlp-update.service:35-37`(`User=root`,`ExecStart=/opt/botamusique/venv/bin/pip install ...`);`deploy/botamusique.service:16`(安装说明 `chown -R botamusique:botamusique /opt/botamusique`)、`:39`(`User=botamusique`)
- **问题**:`venv/bin/pip` 是普通 Python 脚本,`venv/lib/.../site-packages`(含 `.pth` 自动执行)和 `venv/bin/python3` 符号链接都归 botamusique 用户所有。root 的定时任务每天执行它们一次。
- **影响**:bot 进程一旦被攻破(它持续处理不可信媒体和网页,依赖 yt-dlp/ffmpeg/spotdl),攻击者只要往 site-packages 写一个 `.pth` 或改 `venv/bin/pip`,次日 00:00 就能拿到 root。附带问题:root 执行 pip 后,新文件归 root 所有,之后以 botamusique 身份运行的 `!update`(`util.py:163`)会因权限失败。
- **修复**:更新单元改成 `User=botamusique`,重启那一行用特权前缀 `ExecStart=+/bin/systemctl restart botamusique`,或交给 polkit 规则 / 主单元的 `Restart=` 处理;不要让 root 执行非 root 可写的路径。
- **置信度**:Confirmed(按仓库给出的安装步骤)

### CMD-07 [Low] `!userban` 大小写失配,ban 实际无效;`!userban` 列表显示的是 URL ban
- **位置**:`commands/admin.py:15-24`、`bot/core.py:352-356`
- **证据**:存储用 `var.db.set("user_ban", parameter, None)` 原样保存,比较用 `if user.lower() == i[0]`。`repro_dispatch.py` C 段:`!userban Bob` 存进去的是 `('Bob', None)`,Bob 发 `!rtrms` 照常执行(`_display_rms` 被切换)。另外不带参数时第 21 行遍历的是 `var.db.items("url_ban")`。
- **影响**:只要名字含大写字母,ban 就不生效;admin 看到的 ban 列表也是错的。
- **修复**:存储时 `parameter.strip().lower()`,`cmd_user_unban` 同样 lower;第 21 行改为 `"user_ban"`。根本上应按 user_id 封禁(见 CMD-02)。
- **置信度**:Confirmed

### CMD-08 [Low] URL ban 是精确字符串匹配,换种写法就能绕过
- **位置**:`bot/core.py:358-363`、`media/url.py:119-120`、`util.py:323-327`(只对 scheme 和 host 做小写化)
- **证据**:`repro_misc.py` H 段,ban 了 `https://www.youtube.com/watch?v=dQw4w9WgXcQ` 后,`youtu.be/ID`、`m.youtube.com`、`http://`、去掉 `www.`、`&t=1`、参数换序、`:443` 全部不命中;只有改 host 大小写会被拦下。
- **影响**:admin 的 URL ban 形同虚设,普通用户换个写法就能播放被禁内容。
- **修复**:ban 和比较都基于 yt-dlp 的 `extractor_key + id`(`media/url.py` 校验时 `info['extractor_key']`、`info['id']` 已经可得),在 `validate()` 拿到 info 之后检查;聊天入口的预检可以保留,只作为快速失败。
- **置信度**:Confirmed

### CMD-09 [Low] 聊天回显 HTML 不转义;`html.unescape` 后的 URL 能突破 `href="..."`
- **位置**:`util.py:327`(`return html.unescape(url)`)+ `lang/en_US.json:114`(`url_item`)、`:82`(`radio_item`)、`media/url.py:491-497`、`media/radio.py:153-159`(`title=get_radio_title(self.url)`,即攻击者电台的 ICY `StreamTitle`)、`commands/sources.py:213/266`(radio-browser 站名、homepage)、`commands/sources.py:40/67/95`、`commands/library.py:29/141/183`(标题、路径、标签)
- **证据**:`repro_misc.py` I 段。标准客户端输入 `!radio http://x.test/a"><b>BIG</b>`,线上实际传输 `&quot;&gt;&lt;b&gt;...`;`get_url_from_input` 反转义后渲染成
  `<a href="http://x.test/a"><b>BIG</b>"><b>t</b></a> ...`。`now_playing` 在 `announce_current_music=True`(默认)时会广播到整个频道。攻击者自建的 Icecast 也可以把任意 HTML 放进 StreamTitle,每次播报都会推送。
- **影响**:频道级的富文本伪造:伪造"系统/管理员"消息、带误导文字的钓鱼链接、超大 data: 图片刷屏。Mumble 客户端(Qt LogDocument)一般只加载 `data:` 图片,不会外联,`javascript:` 链接也不会执行,所以 IP 泄露和脚本执行的可能性低(该客户端行为为 Needs-verification)。
- **修复**:对所有插入模板的外部数据(title、url、name、tags、path、station 字段)统一做 `html.escape(..., quote=True)`。在 `tr()` 中对 kwargs 默认转义,确需原样 HTML 的少数参数(如 `list=`、`result_table=`)显式标记为 raw。`get_url_from_input` 反转义后应拒绝含 `"<>` 的 URL。
- **置信度**:Confirmed(注入串);客户端渲染后果为 Needs-verification

### CMD-10 [Low] `!delete` 不是 admin 命令,且默认放行所有人;共享 shortlist 会误删他人的检索结果
- **位置**:`commands/library.py:257-302`、`configuration.default.ini:44`(`delete_allowed = True`)、`configuration.example.ini:152`(注释写的是 "allow admins to delete")、`commands/_shared.py`(全局 `song_shortlist`)
- **影响**:任何同频道用户都可以 `!search a` 然后 `!delete 1 2 … 50`,批量删除曲库条目;对 url 类型条目还会 `os.remove` 已缓存的文件(`media/cache.py:95-97`)。file 条目可以由 admin `!rescan` 恢复,但 URL 条目及其标签无法恢复。shortlist 是全局的,A 用户的 `!delete 3` 删的可能是 B 用户刚搜出来的第 3 条。
- **修复**:`!delete` 要求 `bot.is_admin(user)` 或 `delete_allowed`(语义改为"允许非 admin"),默认改为 False,并修正文档;shortlist 按 `text.actor` 分开存。
- **置信度**:Confirmed

### CMD-11 [Low] `!update`:pip 同步跑在回调线程里;`reload(yt_dlp)` 只重载顶层模块;stable/testing 分支是会覆盖 fork 的死代码
- **位置**:`util.py:144-171`、`commands/admin.py:124-132`、`update.sh:3-17`、`bot/core.py:26`(`version = 'git'`)、`configuration.example.ini:79-82`
- **证据**:
  - 默认 `target_version=git` 时,`!update` 会在消息回调里同步执行 `pip install --upgrade yt-dlp`,耗时数十秒。这期间所有命令阻塞,回调持锁时可能掉线。
  - `reload(youtube_dl)` 只重新执行 `yt_dlp/__init__.py`,子模块(extractor 等)仍是旧版本,新旧混用直到重启,但回复却写着 "reloaded"。
  - 按 example.ini 设成 `stable`/`testing` 时,`version.parse('git')` 会抛异常(`repro_misc.py` J 段:`InvalidVersion: Invalid version: 'git'`),所以 `update.sh` 实际不可达。
  - 一旦可达,`update.sh` 会下载**上游 azlux** 的 tarball(没有签名和哈希,固定 `/tmp` 路径),`cp -r` 覆盖本 fork,再 `pip install -r`,并且版本号也是向 `packages.azlux.fr` 查询的。
- **影响**:目前只能由 admin(或 CMD-02 的冒名者)触发,后果是长时间阻塞和 yt-dlp 半升级状态。stable/testing 路径是潜在的"用上游代码覆盖 fork、无完整性校验"隐患。
- **修复**:pip 升级放进后台线程,完成后提示需要重启,去掉 `reload`;删除 `update.sh` 以及 `util.update` 的 stable/testing 分支和 example.ini 中相关注释(fork 没有发布渠道)。
- **置信度**:Confirmed(死代码与阻塞);混版本行为为 Likely

### CMD-12 [Low] `.dockerignore` 漏掉 `.env`、`cookies/`、`*.pem`、`cache/`,`COPY .` 会把它们烤进镜像
- **位置**:`Dockerfile:18`(`COPY . /botamusique`)、`.dockerignore`(只排除了 configuration.ini、*.db、db.ini、data/、music_folder/、spotdl_cache/ 等);对照 `.gitignore` 中的 `.env`、`cookies/`、`*cookies*.txt`、`*.pem`;`docker-compose.yml:76-77` 说 token 放在同目录 `.env`;`deploy/DOCKER.md:181-184` 让用户把 B 站 cookies 放进 `<项目目录>/cookies/`
- **影响**:在 NAS 上 `docker compose build` 时,Cloudflare Tunnel token、B 站/YouTube 登录 cookie、Mumble 客户端证书私钥会写进镜像层;`cache/`(可能有数 GB 的下载缓存)也会被打进镜像,导致构建慢、体积暴涨。镜像一旦被 push 或导出,凭据随之泄露。
- **修复**:`.dockerignore` 增加 `.env`、`cookies/`、`*cookies*.txt`、`*.pem`、`cache/`、`tmp/`。
- **置信度**:Confirmed(按 Docker ignore 语义阅读;未实际 build)

### CMD-13 [Low] 供应链暴露:每次启动以 root 拉取未固定版本的 yt-dlp/spotdl;容器以 root 运行;`curl | sh` 安装 deno;pymumble 跟踪可变分支
- **位置**:`docker-compose.yml:23`(`BAM_UPDATE_ON_START: "1"` 默认开启)、`entrypoint.sh:10-14`、`Dockerfile`(无 `USER`)、`Dockerfile:32` 与 `deploy/install.sh:111`(`curl -fsSL https://deno.land/install.sh | [sudo] sh`,不固定版本、不校验)、`requirements.txt:1-19`(全部不固定版本)、`requirements.txt:17`(`@pymumble-upstream` 分支)
- **影响**:yt-dlp、spotdl 或其任一传递依赖的恶意版本,会在下一次容器重启时以 root 身份执行。此时容器挂载了宿主的 `configuration.ini`(含密钥)、`data/`、`music_folder/`。构建结果不可复现。属于常见做法,但叠加了"默认开启 + root + 每日重启"三个因素。
- **修复**:Dockerfile 增加非 root `USER`,并对挂载目录 chown;`BAM_UPDATE_ON_START` 默认关闭,或只升级 yt-dlp 并用 `--require-hashes`/约束文件;deno 改为下载固定版本的 release zip 并校验 sha256;pymumble 固定到 commit 哈希。另外 `entrypoint.sh:29` 把 Mumble 密码放进 argv(`--password`),同容器内的进程可以通过 `/proc/*/cmdline` 看到,建议改用环境变量或配置文件传入。
- **置信度**:Likely(风险性质)

### CMD-14 [Low] `save_music_library = False` 不生效:字符串 `"False"` 为真
- **位置**:`bot/startup.py:164`、`bot/core.py:239`、`bot/player.py:617`(都写成 `if ... var.config.get("bot", "save_music_library")`)
- **影响**:用户关闭曲库持久化后仍会写 `music.db`(包括 Docker 里 `/botamusique/data`),也没有走 `:memory:` 分支。
- **修复**:三处都改为 `getboolean`。
- **置信度**:Confirmed

### CMD-15 [Low] `!maxvolume` 调低上限后不会压低当前音量
- **位置**:`commands/volume.py:35`(`if int(bot.volume_helper.plain_volume_set) > max_vol:`)
- **影响**:`plain_volume_set` 是 0~1 的浮点数,`int()` 后恒为 0(满音量时为 1),所以把上限调低到 30% 时,当前 80% 的音量保持不变。
- **修复**:去掉 `int()`,直接比较浮点数。
- **置信度**:Confirmed(阅读)

### CMD-16 [Low] 文档漂移:按 example.ini 取消注释 `youtube_query_cookie` 会让 bot 启动即退出
- **位置**:`configuration.example.ini:196-200`;`bot/startup.py:108-114`(出现 default.ini 中没有的 key 就 `sys.exit()`)
- **证据**:`config_keys.py` 的输出:代码引用但 default.ini 缺失的 key:**无**;example.ini 有、default.ini 没有的 key:仅 `[bot] youtube_query_cookie`(代码也不再使用);default.ini 有但从未被引用:`[youtube_dl] source_address`。
- **修复**:从 example.ini 删掉 `youtube_query_cookie` 这一段。另外 example.ini 缺少 `[spotify]` 段,以及 `ducking_delay`、`normalize_volume*`、`watchdog_timeout`、`redirect_ffmpeg_log` 的说明,建议补上。
- **置信度**:Confirmed

### CMD-17 [Info] 部分匹配与零散权限问题
- 部分匹配在 admin 检查**之前**解析(`bot/core.py:367-392`),不会造成越权,因为解析结果仍要过 admin 检查。但 admin 打错字就可能执行危险命令:`!k`→kill、`!ma`→maxvolume、`!webusera/d/l` 都会唯一匹配到 admin 命令(`repro_dispatch.py` D 段)。建议 `kill` 设置 `no_partial_match=True`。歧义提示(`which_command`)会把 admin 命令名也列给普通用户。
- 调试命令 `rtrms` 注册时没有 `admin=True`(`commands/__init__.py:144`,第三个位置参数其实是 `no_partial_match`)。任何人都能开启 `bot/player.py:823-827`,让每个收到的音频包都向 stdout 打一行,刷满 docker 日志。
- `!joinme`(`commands/admin.py:10-12`)设置了 `access_outside_channel=True` 且不需要 admin:任何频道的任何人(默认还允许私聊)都能把 bot 拉走,并用 `token=` 传入任意 access token(`repro_dispatch.py` E 段)。这是上游的设计,如需要可加开关。
- token 模式下任何人发 `!web` 都会清空 `interface.banned_ip` 和 `bad_access_count`(`commands/web.py:16-18`),Web 端的防爆破计数随之失效。token 有 40 bit,爆破不现实,影响低。
- `!file` 不带参数或参数为 `%` 时,`and_like('path', parameter + '%')` 没有转义 LIKE 通配符(`commands/sources.py:32-44`),会把整个本地曲库塞进队列并逐行刷屏到频道。
- 命令异常时把 traceback 最后一行原样回给用户(`bot/core.py:403-407`),可能带出路径或内部信息。

---

## 剔除 / 降级记录
- "ffmpeg/yt-dlp/spotdl 参数以 `-` 开头被当作选项":剔除。ffmpeg 的 uri 紧跟在 `-i` 后,会被当作值;spotdl 的 `list_spotify_tracks` 和 `download_tracks` 都用了 `--`(`media/spotify.py` 中 `'--', url` 及第 311 行注释);yt-dlp 走 Python API 而不是 CLI。全仓没有 `shell=True` 或 `os.system`。
- "旧 ini 缺 key 导致启动崩溃":剔除。`config_keys.py` 证明所有引用都在 default.ini 中,而且 startup 先读 default(`bot/startup.py:102-103`)。
- "`update.sh` 被上游或 DNS 控制后可 RCE":降级到 CMD-11(Low)。该分支在本 fork 默认的 `version='git'` 下因 `InvalidVersion` 不可达,`auto_check_update` 默认也是 False。
- "通过 `<img src=http://…>` 让所有客户端外联":未独立成条,并入 CMD-09。Mumble 客户端只加载 `data:` 资源,无法离线验证,标为 Needs-verification。
- "私聊与频道消息差异导致越权":未发现。私聊同样要过频道检查(`bot/core.py:394-397`,看的是发送者所在频道);只有 admin 可以绕过,而 admin 的问题归入 CMD-02。
- entrypoint 复制 example.ini 后的默认值:example.ini 只有 `[server] host/port` 两个有效键,所以 `webinterface enabled=False`、`auth_method=none`(来自 default.ini)、`admin` 为空(`['']`,任何非空名都不匹配)。默认配置不存在开放的 Web 界面,不构成发现。
- install.sh 的幂等性:venv 和 configuration.ini 都有存在性判断,`set -euo pipefail` 配合 `|| echo` 合理,`sudo` 只用于 apt 和 deno。除了已并入 CMD-13 的 `curl | sudo sh`,没有其他问题。

## 覆盖范围
- 全文读过:`commands/*.py`(10 个文件)、`bot/core.py`、`util.py`、`constants.py`、`variables.py`、`update.sh`、`deploy/install.sh`、`deploy/*.service`、`deploy/*.timer`、`entrypoint.sh`、`Dockerfile`、`Dockerfile.local`、`docker-compose.yml`、`.dockerignore`、`.gitignore`、`requirements.txt`、`configuration.default.ini`,以及 example.ini 的相关段落。
- 为追踪数据流读过部分:`database.py`(SettingsDatabase、Condition)、`media/{radio,url,livestream,playlist,cache,spotify}.py` 中的相关函数、`bot/player.py:240-300` 与 `:823`、`bot/startup.py:90-170` 与 `:280-295`、`interface.py:90-200` 与 `:640`、`lang/en_US.json` 的模板。
- 未覆盖:pymumble 2.x 源码(未安装,本地也没有 `pymumble-upstream` 分支),回调线程是否持锁只能依据 PROGRESS.md:165 和代码注释;Mumble 客户端 HTML 渲染策略无法实测;`Dockerfile.local` 是遗留文件(clone 的是上游 azlux 仓库,只用于本地),只做了阅读;Web 端 SSRF/XSS 交给 Web 组(本文只交叉引用 `web_api.py:304`、`interface.py:446/456`)。
