# 命令系统、配置与部署/供应链 审计报告

组长:Fable 5.1。分支 claude/quirky-fermi-6nou7s,只读审计。
说明:本会话没有 Agent 工具,初稿由组长亲自通读 + 脚本复现;随后协调者转来一名 Opus 工人的独立报告(`commands-worker.md`,脚本 `commands-worker/`),已逐条核对、跑脚本并合并,见文末「验收记录」。标注 **[worker]** 的内容来自该工人并经组长复核。验证脚本在 `audit/repro/commands/`(cfgcheck.py 配置三源比对、verify.py 复现脚本、pymumble_mumble.py 为 pymumble-upstream 分支拉取的只读副本)。

## 总评

**计数:2 High / 7 Medium / 13 Low / 1 组 Info。**

命令层整体沿用上游 botamusique 的设计:子进程全部用参数列表(无 shell=True,spotdl 用 `--` 隔离),无命令注入;admin 命令的权限检查在分发器统一做,partial-match 不能绕过 admin 标记。真正的问题集中在三处:
1. **任何频道成员一条消息即可冻结整个进程**:`!filematch` / `!listfile` 把用户输入直接当正则跑(ReDoS,实测 >25s);`_sre` 全程持 GIL,看门狗/播放/Web/pymumble 心跳线程全部停摆,不会自动重启。`!repeat N` 无上限。这是最该先修的。
2. **身份与封禁模型都是"字符串相等"**:admin 只比用户名(代码里明明已有 user_id/cert_hash 的稳定标识),admin 未在服务器注册或开启 `split_username_at_space` 时可被冒名,且身份延伸到 Web 端(升为 High,有条件);URL 封禁是归一化后的精确匹配,`youtu.be`/`m.youtube.com`/改参数顺序即可绕过。
3. **部署面的秘密泄漏**:`.dockerignore` 漏掉 `.env`(Cloudflare Tunnel token)、`cookies/`(B 站/YouTube 登录 cookie)和 `cache/`,`COPY . /botamusique` 会把它们烤进镜像层;聊天命令参数(含 `!password <明文>`)原样写进 INFO 日志。

供应链方面:pymumble 从本仓库 `pymumble-upstream` 分支装(仓库公开、同一信任域,只是未钉 commit);yt-dlp/spotdl 有意不锁版本;唯一需要指出的是 Deno 用 `curl | sh`(install.sh 里还是 `sudo sh`)无校验,以及 systemd timer 以 root 执行 botamusique 用户可写的 venv(site-packages `.pth` 自动执行)——bot 被攻破后可据此提权到 root,并留下 root 属主文件。`!update` 的上游 tarball 覆盖路径在本 fork 里因 `version='git'` 实际不可达(会抛 InvalidVersion),只剩"阻塞回调线程跑 pip"的问题。

---

## 发现列表

### CCD-01 [High] 用户正则直接 `re.search`:`!filematch` / `!listfile` 可 ReDoS 冻结 bot
- **位置**:`commands/sources.py:89`(`re.search(parameter, file)`)、`commands/sources.py:359`(`re.search(parameter, file['path'])`)
- **问题**:非 admin 用户提供的正则在 pymumble 回调线程上同步执行,对库中每个标题各跑一次,无长度/复杂度限制、无超时。
- **证据**:
  ```python
  # commands/sources.py:87-89
  for file_dict in file_dicts:
      file = file_dict['title']
      match = re.search(parameter, file)
  ```
  复现(verify.py):`re.search(r'(\w+\s?)*$', 'Bohemian_Rhapsody_Queen_Remaster_2011 (Official)')` 在 25s 内未返回(被 timeout 杀掉);`(a|a)+$` 对 27 字符标题 11.6s,指数增长。`_handle_message` 在 pymumble 的接收线程里同步调用 handler(`bot/core.py:312-320, 402`)。**[worker 补充,组长复核]** `_sre` 匹配全程不释放 GIL:组长实测一个 `time.sleep(0.01)` 轮询线程在 5.50s 的正则期间最长停顿 5.50s(与正则时长相等)。因此不只是回调线程:播放主循环(ffmpeg 喂音)、pymumble 心跳、Flask、以及 watchdog(`bot/startup.py:284-295`,同为 Python 线程)全部拿不到 GIL,看门狗不会 `os._exit` 重启;Docker healthcheck 只标 unhealthy 不重启(compose 注释自认需 autoheal)。前提仅是曲库里有至少一个 `type='file'` 条目。
- **影响**:频道内任意用户一条 `!fm (\w+\s?)*$`(或 worker 的 `!fm (.*.*)*!`)让整个 bot 进程冻结数分钟到数小时,且不会自愈;可无限重复。
- **修复建议**:`cmd_play_file_match`/`cmd_list_file` 改用 `re.escape(parameter)` 的子串匹配,或限制 `len(parameter) <= 64` 且在独立线程 + `signal`/`regex` 库超时中执行;最小补丁是把 `re.search(parameter, ...)` 改为 `parameter.lower() in file.lower()`。
- **置信度**:Confirmed

### CCD-02 [Medium] `!repeat N` 无上限,任意用户可阻塞线程并耗尽内存
- **位置**:`commands/playback.py:187-199`(`repeat = int(parameter)` 在 190,循环在 194-195)
- **问题**:`repeat = int(parameter)` 不设上限,`for _ in range(repeat): var.playlist.insert(...)` 逐条加锁插入。
- **证据**:
  ```python
  if parameter and parameter.isdigit():
      repeat = int(parameter)
  ...
  for _ in range(repeat):
      var.playlist.insert(var.playlist.current_index + 1, music)
  ```
  `max_track_playlist`(默认 20)只在 `media/url_from_playlist.py:52` 限制歌单导入,不约束队列长度。
- **影响**:`!repeat 999999999` 由任意频道成员发出:回调线程阻塞(同 CCD-01 的失联后果)、进程内存线性增长、`save_playlist` 落盘时写出巨型 JSON。
- **修复建议**:`repeat = min(int(parameter), 20)`(或复用 `max_track_playlist`),超限回 `bad_parameter`。
- **置信度**:Confirmed(代码路径无歧义,未实跑到 OOM)

### CCD-03 [High,有条件] admin 仅凭显示用户名判定,可被冒名,且身份延伸到 Web 端(原 Medium,验收后升级)
- **位置**:`bot/core.py:428-432`(`is_admin`)、`bot/core.py:328-331`(`user = ...name`,`split_username_at_space`)
- **问题**:`is_admin(user)` 把 `users[actor].name` 与配置里的名字做字符串比较;本仓库 `commands/personal.py:25-36` 已经实现了"注册用户用 user_id,否则 cert_hash"的稳定标识,但 admin 判定没有用。
- **证据**:
  ```python
  @staticmethod
  def is_admin(user):
      list_admin = var.config.get('bot', 'admin').rstrip().split(';')
      if user in list_admin:
  ```
  **[worker]** harness 复现(`commands-worker/repro_dispatch.py` A/B 段,组长已跑):admin=`Alice`,无 `user_id` 的访客 "Alice" 发 `!kill` → `bot.exit=True`;`split_username_at_space=True` 时访客 "Alice (guest)" 发 `!kill` → `bot.exit=True`。
- **影响**:只要管理员账号没有在 Mumble 服务器上注册(DEPLOY.md/DOCKER.md 都只让填"你的 Mumble 用户名",未提醒注册),任何人在管理员离线时用同名登录即获 admin(`!kill`、`!update`、`!dropdatabase`——见 CCD-10,会让 bot 瘫痪到重启、`!urlban`、`!webuseradd`),并绕过私聊/跨频道/URL ban 限制(`bot/core.py:347/358/394`)。开启 `split_username_at_space` 后甚至不需要 admin 离线:`Admin [AFK]` 会被截成 `Admin`。**[worker]** 身份还延伸到 Web:token 模式下冒名者 `!web` 拿到以 admin 名义绑定的 token(`commands/web.py:29-30` 按名字写 `web_token`/`user`),`interface.py:640-641` 随即按 `var.bot.is_admin(user)` 授予上传与删除权;password 模式下 `!password` 可覆盖该名字的 Web 密码(`commands/web.py:39-48`)。
  **裁决理由(升 High)**:两条路径均已复现;后果是完整 admin 面 + Web 面;唯一缓解条件"admin 已在服务器注册"在仓库任何文档里都没有要求,属于默认部署即暴露。
- **修复建议**:`is_admin` 改为接收 `text.actor`,用 `mumble_key()` 得到 `uid:`/`cert:` 键,配置支持 `admin = uid:3;cert:ab12...;Name`;至少在文档里要求管理员账号必须在服务器注册并带证书。
- **置信度**:Confirmed(代码路径 + harness);"Murmur 是否放行未注册者用该名"取决于服务器是否注册了该账号

### CCD-04 [Medium] URL 封禁是精确匹配,短链/镜像域/参数顺序即可绕过;`!yplay 关键词` 完全不经过封禁
- **位置**:`bot/core.py:358-360`(分发器检查)、`media/url.py:119`(validate 检查)、`util.py:314-328`(归一化只小写 scheme+host)
- **问题**:`var.db.has_option('url_ban', input_url)` 是 sqlite 等值查询;归一化不去 `www.`、不处理 `youtu.be`/`m.youtube.com`、不解析 `v=` 参数。
- **证据**(verify.py,封禁 `https://www.youtube.com/watch?v=dQw4w9WgXcQ` 后):
  ```
  banned? False https://youtube.com/watch?v=dQw4w9WgXcQ
  banned? False https://youtu.be/dQw4w9WgXcQ
  banned? False https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ
  banned? False https://m.youtube.com/watch?v=dQw4w9WgXcQ
  ```
  `!yplay <标题>`(`sources.py:329-338`)走 `youtube_search` 再拼 URL,分发器的封禁检查只看参数里的 URL,因此封禁视频仍可被搜索点播。
- **影响**:`!urlban` 对任何有心绕过的用户无效。
- **修复建议**:封禁键改为 `(extractor, video_id)`:validate 阶段用 yt-dlp 的 `info['extractor_key']+info['id']` 查 ban 表;分发器前置检查保留作快速路径。
- **置信度**:Confirmed

### CCD-05 [Medium] SSRF 防护只覆盖 `!url`/`!playlist`;`!radio`、`!live <url>`、`!rbplay` 可让 bot 请求内网地址
- **位置**:`commands/sources.py:165-181`(`cmd_play_radio` 无 `is_public_url`)→ `media/radio.py:15-54`(`requests.head/get(base_url + '/stats?json=1')`);`commands/streaming.py:69-72`(`!live` 直接入队)→ `media/livestream.py:108-112`(yt-dlp 抓取);`commands/sources.py:246-262`(`!rbplay` 用 radio-browser 公共库里任意人提交的 URL)
- **问题**:`util.is_public_url` 已存在且 `cmd_play_url`/`cmd_play_playlist` 都调用了(`sources.py:123, 149`),但另外三条入队路径没有。
- **证据**:
  ```python
  # commands/sources.py:174-176
  url = util.get_url_from_input(parameter)
  if url:
      music_wrapper = get_cached_wrapper_from_scrap(type='radio', url=url, user=user)
  # media/radio.py:22-29  (RadioItem.__init__ 同步调用)
  url_shoutcast = base_url + '/stats?json=1'
  response = requests.get(url_shoutcast, timeout=10)
  title_server = data['servertitle']; return title_server
  ```
  `!radio http://10.0.0.5:8080/x` 会让 bot 请求 `http://10.0.0.5:8080/stats?json=1` 和 `/status-json.xsl`,JSON 里的 `servertitle`/`server_name` 会回显到聊天;随后 ffmpeg 还会拉该 URL。
- **影响**:频道内任意用户探测 NAS/容器网络内的 HTTP 服务(盲 SSRF + 有限回显);Docker 部署里同网络的 cloudflared 等服务可被触达。
- **修复建议**:在 `cmd_play_radio`、`_enqueue_live`、`cmd_rb_play` 入队前加 `if not util.is_public_url(url): bad_url`;`get_radio_server_description` 内也加同样检查(radio 项从 DB 重载时不会再查,但新建时必查)。
- **置信度**:Confirmed(代码路径);内网实际可达性 Needs-verification

### CCD-06 [Medium] `.dockerignore` 漏掉 `.env`、`cookies/`、`cache/`:Tunnel token 与登录 cookie 会被烤进镜像
- **位置**:`.dockerignore:1-13`;`Dockerfile:18`(`COPY . /botamusique`);`docker-compose.yml:46`(`./cookies`)、`docker-compose.yml:76`(`.env` 存 `CLOUDFLARE_TUNNEL_TOKEN`);`.gitignore:121-126`(git 侧已忽略 `.env`、`cookies/`、`*cookies*.txt`,但 dockerignore 没同步)
- **问题**:DOCKER.md/WEBUI.md 要求用户在部署目录放 `.env`(tunnel token)和 `cookies/`(B 站大会员 / YouTube cookie),`docker compose build` 时 context 就是该目录,这三项都不在 `.dockerignore` 里。
- **证据**:
  ```
  .dockerignore: .git/ venv/ **/__pycache__/ *.pyc *.pyo configuration.ini db.ini *.db spotdl_cache/ music_folder/ data/ *.log **/node_modules/
  ```
  没有 `.env`、`cookies/`、`cache/`、`*cookies*.txt`;**[worker]** 也没有 `*.pem`(`.gitignore:112` 有),`[server] certificate` 指向的 Mumble 客户端证书私钥若放在项目目录同样会进镜像。
- **影响**:镜像 `mumble-music:latest` 的 python-builder 与最终层都包含 Tunnel token(可直接接管公网域名回源)和站点登录 cookie;镜像被导出/推送/备份时泄漏。`cache/` 还会让 build context 膨胀数 GB。
- **修复建议**:`.dockerignore` 追加 `.env`、`cookies/`、`*cookies*.txt`、`*.pem`、`cache/`、`tmp/`、`webui/node_modules/`、`web/`;长期看把 `COPY .` 改成显式列表。
- **置信度**:Confirmed

### CCD-07 [Medium] 聊天命令参数原样写入 INFO 日志:`!password <明文>`、`!bind <码>`、`!joinme <token>`
- **位置**:`bot/core.py:344`;`commands/web.py:39-50`(`cmd_user_password`,46 行哈希);`commands/admin.py:10-12`(`cmd_joinme` token);`commands/personal.py:60`(`cmd_bind`)
- **问题**:
  ```python
  self.log.info(f'bot: received command "{command}" with arguments "{argument}" from {user}')
  ```
  在任何 handler 之前无条件记录完整参数。
- **影响**:Web 密码明文落到 `logfile`、journald(`botamusique.service`)、`docker logs`(compose 配了 json-file 保留 3×10m);`!joinme` 泄漏频道访问 token。日志通常权限宽松、会被贴到 issue/聊天里排错。
- **修复建议**:维护一个 `SENSITIVE_COMMANDS = {change_user_password, bind, joinme}` 集合,这些命令日志里把 argument 记为 `<redacted>`;`cmd_user_password` 若来自频道消息(`not text.session`)应拒绝并提示私聊。
- **置信度**:Confirmed

### CCD-08 [Medium] `--tokens` / `BAM_TOKENS` 传入的是字符串,pymumble 按字符拆成 token:频道 token 配置通过 CLI/Docker 永远无效
- **位置**:`bot/core.py:107-111`;`entrypoint.sh:40-42`;pymumble `src/mumble/mumble.py:716-717`(pymumble-upstream 分支,已拉取副本到 `audit/repro/commands/pymumble_mumble.py`)
- **证据**:
  ```python
  # bot/core.py
  if args.tokens:
      tokens = args.tokens            # str, 未 split
  else:
      tokens = var.config.get("server", "tokens"); tokens = tokens.split(',')
  # pymumble
  if self.tokens:
      authenticate.tokens.extend(self.tokens)   # str → 逐字符
  ```
  `BAM_TOKENS=secret` 会发送 token 列表 `['s','e','c','r','e','t']`。
- **影响**:Docker/CLI 用户按 entrypoint 的 `BAM_TOKENS` 配置后 bot 进不了需要 token 的频道,且没有任何报错提示;只能写进 ini 才生效。
- **修复建议**:`bot/core.py:108` 改为 `tokens = args.tokens.split(',')`。
- **置信度**:Confirmed

### CCD-09 [Low] `!userban` 从未生效(大小写不一致),且列表显示的是 URL 封禁表
- **位置**:`commands/admin.py:15-24` vs `bot/core.py:352-353`
- **证据**:存储 `var.db.set("user_ban", parameter, None)` 原样保存;检查 `if user.lower() == i[0]`。verify.py:`!userban Bob` 后 Bob 发命令 → `banned? False`。`admin.py:21` 无参数时遍历的是 `"url_ban"`。
- **影响**:管理员以为封了用户,实际只有全小写用户名才匹配;列表功能展示错表。
- **修复建议**:`set("user_ban", parameter.lower(), None)`;`items("url_ban")` → `items("user_ban")`;`cmd_user_unban` 同样 lower。
- **置信度**:Confirmed

### CCD-10 [Low] `!dropdatabase` 后不重建表且把 music_db 指到 settings 库,bot 直到重启前全部命令报错
- **位置**:`commands/library.py:307-316`;`database.py:272-276, 492-496`
- **证据**:`var.db.drop_table()` 执行 `DROP TABLE botamusique` 后 `SettingsDatabase(path)` 不建表;`var.music_db = MusicDatabase(var.settings_db_path)` 用错路径。此后任何 `var.db.get/has_option`(如 `_handle_message` 的 `db.items("user_ban")`)抛 `OperationalError: no such table`。
- **影响**:admin 自伤;需要重启才能恢复(启动时 migration 重建)。
- **修复建议**:drop 后调用 `DatabaseMigration(var.db, var.music_db).migrate()`;路径改 `var.music_db_path`。
- **置信度**:Confirmed(代码路径)

### CCD-11 [Low] `save_music_library = False` 永远不生效
- **位置**:`bot/startup.py:164`
- **证据**:`if var.config.get("bot", "save_music_library"):` 对字符串 `"False"` 为真。verify.py:`bool(config.get)=True, getboolean=False`。
- **影响**:example.ini:156-157 与 `music_database_path` 注释承诺的"可关闭落盘"失效。
- **修复建议**:改 `getboolean`。
- **置信度**:Confirmed

### CCD-12 [Low] 配置三源不一致(default.ini / example.ini / 代码 / 文档)
- **位置**:`configuration.example.ini:196-200`(`youtube_query_cookie`,default.ini 无此项)→ `bot/startup.py:108-114` 会 `sys.exit`;`configuration.example.ini` 缺整个 `[spotify]` 节,而 `deploy/install.sh:159`、`deploy/DOCKER.md:88-90` 都要求填写;`configuration.default.ini:116` `source_address` 代码中无任何读取(cfgcheck.py);`example.ini:172-173` 说 `max_track_playlist` 是"队列最大曲目数",实际只限制歌单导入(`media/url_from_playlist.py:52`);`example.ini:152-153` 说 `delete_allowed` 是"允许管理员删除",实际无 admin 判断(详见 CCD-22);`example.ini:82` 示例值 `target_version = stable` 在本 fork 会让 `!update` 抛 `InvalidVersion`(见 CCD-14)。
- **证据**:cfgcheck.py 输出:`example.ini keys absent from default.ini: ('bot','youtube_query_cookie')`;`default.ini keys never read: source_address`。
- **影响**:照 example 取消注释 `youtube_query_cookie` 直接起不来;Spotify 配置项无官方样例;`delete_allowed` 语义与文档相反。
- **修复建议**:删除 example 的 `youtube_query_cookie` 段;example 增加 `[spotify]` 节;`cmd_delete_from_library` 加 `bot.is_admin(user)`;修正两条注释。
- **置信度**:Confirmed

### CCD-13 [Low] `!rbquery` 无参数时 KeyError(语言文件缺 `rb_query_empty`)
- **位置**:`commands/sources.py:194`;`lang/en_US.json`(只有 `rb_play_empty`,`constants.py:19-26` 回退到 en 后仍 KeyError)
- **影响**:用户得到 "Command failed with error: KeyError" 而非提示。
- **修复建议**:en_US.json/zh_CN.json 增加 `rb_query_empty`。
- **置信度**:Confirmed

### CCD-14 [Low] `!update` 路径:同步阻塞回调线程跑 pip;上游 tarball 覆盖路径在本 fork 不可达但保留了不安全 /tmp 用法
- **位置**:`util.py:144-171`;`update.sh:3-16`;`bot/core.py:26`(`version = 'git'`);`commands/admin.py:124-132`
- **证据**:`target == "git"` 时只执行 `pip install --upgrade yt-dlp`(`util.py:163`)并 `reload(youtube_dl)`,整个过程在 pymumble 回调线程里同步等待(数十秒,期间同 CCD-01 失联风险)。若把 `target_version` 改为 stable/testing(example.ini 示例值),`version.parse('git')` 在 `util.py:153` 抛 `InvalidVersion`,命令报错——因此 `update.sh` 从 `packages.azlux.fr` 拉**上游 azlux** tarball 并 `cp -r /tmp/botamusique/* .` 覆盖本 fork 的路径实际走不到;但脚本仍在仓库里,用固定共享路径 `/tmp/botamusique.tar.gz`(本机其他用户可预置符号链接),且一旦被触发会用上游单文件版代码覆盖 fork 的 bot/、commands/、webui/。`pip install -r requirements.txt` 在最终 Docker 镜像里还会因缺 `git` 失败(pymumble 是 git 依赖)。
- **影响**:当前实际影响是 admin 执行 `!update` 时 bot 短暂失联;残留脚本是未来误触的地雷。
- **修复建议**:`cmd_update` 改为后台线程执行并回报;删除 `update.sh`/stable/testing 分支,`target_version` 只保留 `git`;或 `!update` 直接改成 `git pull` + pip 并要求重启。
- **置信度**:Confirmed

### CCD-15 [Medium] systemd 定时更新以 root 执行 botamusique 用户可写的 venv:本地提权到 root + root 属主文件(原 Low,验收后升级)
- **位置**:`deploy/botamusique-ytdlp-update.service:35-37`;`deploy/botamusique.service:39`;`deploy/DEPLOY.md:135`(`chown -R botamusique`)
- **证据**:`User=root` + `ExecStart=/opt/botamusique/venv/bin/pip install --upgrade yt-dlp spotdl`。**[worker,组长复核]** 按 DEPLOY.md:135 `chown -R botamusique:botamusique /opt/botamusique` 后,`venv/bin/pip`(普通 Python 脚本)、`venv/lib/python3.x/site-packages/`(其中的 `.pth` 文件由 `site` 模块在解释器启动时自动执行)全部归 botamusique 可写;root 的定时任务每天 00:00 用 venv 解释器执行它们。此外 pip 以 root 写入的新 dist-info/包目录属主为 root(umask 022),之后 `!update`(`util.py:163`,以 botamusique 运行)在卸载旧版本时报 PermissionError。
- **影响**:bot 进程(持续处理不可信媒体,依赖 yt-dlp/ffmpeg/spotdl)一旦被攻破,攻击者往 site-packages 放一个 `.pth` 或改写 `venv/bin/pip`,次日 00:00 即以 root 执行——把 `User=botamusique` 的降权完全抵消。次要:`!update` 在 timer 跑过一次后即永久失败;timer 每天 0 点无条件 `systemctl restart` 会打断播放(Info)。
  **裁决理由(升 Medium)**:提权链每一环都由仓库自带的安装步骤构成(chown -R + User=root + venv 路径),不依赖额外假设;前提"bot 先被攻破"使其不到 High。
- **修复建议**:update.service 改 `User=botamusique` + `ExecStart=... pip ...`,重启用 `ExecStart=+/bin/systemctl restart botamusique`(`+` 前缀以 root 执行),或 `sudo -u botamusique` 包一层。
- **置信度**:Confirmed(按仓库给出的安装步骤;未在目标机实跑)

### CCD-16 [Low] Deno 用 `curl | sh` 无校验安装;install.sh 里以 `sudo sh` 执行远程脚本
- **位置**:`Dockerfile:32`;`deploy/install.sh:111`
- **证据**:`curl -fsSL https://deno.land/install.sh | sudo DENO_INSTALL=/usr/local sh`。
- **影响**:实际可利用性依赖 deno.land 被篡改或 TLS 被劫持(有 CA 时需先攻破 TLS),属供应链残余风险,不是即时漏洞;但 install.sh 的 `sudo` 把影响面升到 root。
- **修复建议**:固定 Deno 版本并从 GitHub release 下载 zip + 校验 sha256;Docker 里用 `denoland/deno:bin-<ver>` 镜像 `COPY --from`。
- **置信度**:Confirmed(代码事实);可利用性低

### CCD-17 [Low] 容器以 root 运行;挂载目录会被创建为 root 属主
- **位置**:`Dockerfile`(无 `USER` 指令,`ENTRYPOINT` 以 root 执行)、`docker-compose.yml:32-46`
- **影响**:bot 进程(含 yt-dlp/ffmpeg/spotdl/deno 子进程)全部 root;宿主 `./data ./cache ./spotdl_cache` 由容器创建后为 root:root,NAS 用户不能直接清理。结合 CCD-05 的 SSRF/CCD-01 的 DoS,没有降权层。`EXPOSE 8181` 仅声明,compose 默认不映射,`listening_addr` 由 WEBUI.md 指导为 0.0.0.0 仅在 compose 网络内——这部分设计是合理的。
- **修复建议**:Dockerfile 增加 `RUN useradd -r -u 1000 bot && chown -R bot /botamusique` + `USER bot`;compose 加 `user: "1000:1000"`。
- **置信度**:Confirmed

### CCD-18 [Low] 秘密出现在进程命令行:Mumble 密码与 Spotify client_secret
- **位置**:`entrypoint.sh:28-30`(`--password "$BAM_MUMBLE_PASSWORD"`);`media/spotify.py:227, 310`(`--client-secret`)
- **影响**:同主机其他用户 `ps`/`/proc/*/cmdline` 可读;Docker 内无其他用户,实际影响限于裸机 systemd 部署与 spotdl 运行期间。
- **修复建议**:spotdl 走 `_prepare_spotdl_credentials` 的配置文件路径已存在(`spotify.py:226`),应改为失败即报错而非回退到命令行;Mumble 密码改从配置文件读。
- **置信度**:Confirmed

### CCD-19 [Low] requirements / 运行时版本约束未声明
- **位置**:`requirements.txt`;`bot/player.py:2`(`import audioop`);pymumble `pyproject.toml` `requires-python = ">=3.12"`;`deploy/DEPLOY.md:215`
- **证据**:`audioop` 在 Python 3.13 被移除,requirements 没有 `audioop-lts`(测试 venv 是手工装的);pymumble 要求 ≥3.12,install.sh 用系统 python3,Ubuntu 22.04(3.10)会在 pip 阶段失败,25.04(3.13)会在启动时 ImportError。`pymumble @ git+...@pymumble-upstream` 钉的是分支不是 commit,构建不可重现(仓库为公开仓库,信任域相同,不构成第三方供应链风险)。
- **修复建议**:加 `audioop-lts; python_version >= "3.13"`;pymumble 改钉 commit SHA;install.sh 开头检查 `python3 -c 'import sys; assert sys.version_info >= (3,12)'`。
- **置信度**:Confirmed

### CCD-20 [Low] HTML 注入面:`get_url_from_input` 反转义后直接拼进 `<a href="{url}">`;yt-dlp/radio-browser 返回的标题未转义
- **位置**:`util.py:327`(`html.unescape(url)`);`lang/en_US.json:114`(`url_item`)、`:49`、`:82`;`commands/sources.py:213, 266, 322`(radio-browser 站名、YouTube 标题直接 f-string)
- **证据**(verify.py):`!url https://example.com/x?q=&lt;img&#9;src=&quot;http://attacker.tld/p&quot;&gt;` → url 变为 `https://example.com/x?q=<img\tsrc="http://attacker.tld/p">`,渲染结果 `<a href="https://example.com/x?q=<img	src="http://attacker.tld/p">">`。`&#9;` 实体绕过了 `\S*` 的空白限制。
- **影响**:评定为 Low 的原因:Mumble 服务器 `allowhtml=true` 时用户本就能直接发 HTML,`allowhtml=false` 时 bot 的消息同样被服务器剥离;bot 转发没有带来额外权限。但该 URL 会写入 playlist/DB 并由 Web UI 展示(属 Web 组范围,请其核对 webui 对 `url`/`title` 的渲染是否转义)。
- **修复建议**:`get_url_from_input` 返回前 `urllib.parse.quote(url, safe=':/?&=%#@+~.-_')`;所有 `tr(..., title=...)`/f-string 回显处用 `html.escape`。
- **置信度**:Confirmed(注入);客户端是否加载远程 `<img>` Needs-verification

### CCD-22 [Low] `!delete` 非 admin 可用且默认放行;全局 shortlist 让 A 用户删到 B 用户的检索结果 **[worker 增量,组长复核]**
- **位置**:`commands/library.py:257-304`(无 `is_admin`,只看 `delete_allowed`);`configuration.default.ini:44`(`delete_allowed = True`);`configuration.example.ini:152-154`(注释写 "allow admins to delete");`commands/_shared.py:23`(模块级全局 `song_shortlist`)
- **证据**:`if not var.config.getboolean("bot", "delete_allowed"): ...not_admin` 之后直接按 `_shared.song_shortlist[index-1]['id']` 调 `var.cache.free_and_delete`(`media/cache.py:90-101`:url 类型 `os.remove` 缓存文件,所有类型删 DB 行)。shortlist 是进程级单例,由最近一次 `!search`/`!listfile`/`!findtagged`/`!ysearch` 覆盖。
- **影响**:任意频道成员 `!search a` + `!delete 1 2 … 50` 批量删库记录;URL 条目的缓存文件与标签不可恢复(file 条目可 `!rescan` 找回)。A 发 `!delete 3` 时若 B 刚做过检索,删的是 B 的第 3 条。
- **修复建议**:`cmd_delete_from_library` 要求 `bot.is_admin(user)` 或把 `delete_allowed` 语义改为"允许非 admin"并默认 False;shortlist 按 `text.actor` 分桶。
- **置信度**:Confirmed

### CCD-23 [Low] `!maxvolume` 调低上限不会压低当前音量 **[组长原 Info,worker 独立发现,提为 Low]**
- **位置**:`commands/volume.py:35`
- **证据**:`if int(bot.volume_helper.plain_volume_set) > max_vol:` —— `plain_volume_set` 是 0~1 浮点,`int()` 后恒为 0(满音量为 1),条件几乎永不成立。
- **影响**:admin 把上限从 100% 调到 30% 后,当前 80% 音量继续播放,直到有人再 `!volume`。
- **修复建议**:去掉 `int()`。
- **置信度**:Confirmed

### CCD-21 [Info] 其他小项(不单独计分)
- `commands/__init__.py:144`:`register_command('rtrms', cmd_real_time_rms, True)` 第三个位置参数是 `no_partial_match`,不是 `admin`,调试命令对所有人开放;**[worker]** 开启后 `bot/player.py:823-828` 对每个收到的音频包 `print` 一行到 stdout,可刷满 docker 日志(compose 已限 3×10m)。
- **[worker]** `!joinme`(`commands/__init__.py:93` `access_outside_channel=True`、`commands/admin.py:10-12`)不需要 admin:任何频道的任何人(默认允许私聊)都能把 bot 拉走并传入任意 `token=`(`repro_dispatch.py` E 段复现)。上游设计,建议加开关或限 admin。
- **[worker]** `bot/core.py:403-407` 命令异常时把 traceback 最后一行原样回给用户,可能带出文件路径/内部信息。
- **[worker]** `!file` 无参数或参数含 `%`/`_` 时 `and_like('path', parameter + '%')`(`commands/sources.py:32-34`)未转义 LIKE 通配符,会把整个本地曲库入队并逐行刷屏频道(非注入,参数已绑定)。
- partial-match 结果(verify.py):`!o`→`oust`、`!c`→`clear`、`!de`→`delete`、`!k`→`kill`(admin 检查仍生效)。非 admin 本来就能发 `!oust`/`!clear`,所以不是提权,只是误触面;建议给 `stop_and_getout`、`clear`、`delete` 加 `no_partial_match=True`。
- `commands/web.py:17-18`:`!web` 会清空 Web 端 `banned_ip`/`bad_access_count`;能发 `!web` 的人本就能拿 token,影响有限。
- `configuration.default.ini:90` `flask_secret = ChangeThisPassword`、`bot/startup.py:41` 直接使用且不告警——交 Web 组评估影响。
- `bot/startup.py:134` 日志 10KB×3 轮转,故障时基本没有可用历史。
- `Dockerfile.local`、`.drone.yml`、`scripts/commit_new_translation.sh`、`scripts/update_translation_to_server.sh` 是上游 azlux 的遗留,构建/推送的是上游仓库(含 `https://azlux:$GITHUB_API@` 这种把 token 写进 remote URL 的做法),本 fork 不会触发,但容易误导。
- `docker-compose.yml:25` `BAM_DB: /botamusique/data/db.ini` 实际是 sqlite 文件,命名误导。
- `.gitignore` 的 `lib/`、`var/`、`*.spec` 等模板项当前没有误伤任何已跟踪文件(`git status --ignored` 只有 `.coverage`);`git ls-files` 未发现 configuration.ini/*.db/*.pem/cookies/.env 入库。

---

## 剔除/降级记录
- 工人报告验收结果见上节(全部采纳)。以下是组长自查后主动降级/剔除的项:
  - "命令参数注入 shell":所有 `subprocess` 调用均为参数列表(`util.py:156,159,163,502`、`bot/player.py:292`、`media/spotify.py:199-229,303-313`、`web_upload.py:132-137`),spotdl 用 `--` 隔离,ffmpeg 输入在 `-i` 之后;无发现,剔除。
  - "partial-match 让 `!k` 绕过 admin":分发器在匹配后用 `command_exc` 再查 `admin` 标记(`bot/core.py:390-392`),不可绕过;降为 Info(CCD-21)。
  - "example.ini 被拷成 configuration.ini 时 Web 默认暴露":example.ini 的 `[webinterface]` 全部注释,合并 default 后 `enabled = False`、`listening_addr = 127.0.0.1`;不成立,剔除。
  - "pymumble 从本仓库分支安装是供应链风险":仓库公开、与主代码同一信任域,降为 CCD-19 的"未钉 commit"。
  - "翻译文件占位符不匹配导致 `!ysearch` 崩溃"(es_ES/pt_BR 的 `{{índices}}`):复核为已转义的双花括号,误报,剔除。
  - "`!update` 会用上游 tarball 覆盖 fork":因 `version='git'` 触发 `InvalidVersion`,路径不可达,降为 CCD-14 Low。
  - "requirements 未锁版本":yt-dlp/spotdl 有意追新(entrypoint/timer 都在升级),无实际可利用点,只保留 CCD-19 中有确切后果的 audioop/python 版本项。
  - "`!bind` 可暴力猜 6 位码":`commands/personal.py:20-21, 53-56` 已有 5 次/600s 锁定(按 mumble_key),剔除。

## 验收记录(对 commands-worker.md 的逐条裁决)

工人报 2 High / 4 Medium / 10 Low / 1 Info。四个复现脚本以仓库根为参数全部跑通(A/B 冒名、C userban、D 前缀、E joinme、F dropdatabase、G ReDoS、H ban 绕过、I href 突破、J update InvalidVersion、`!repeat` 50/100 条 5s/10s、config_keys 全部命中 default.ini)。

| 工人项 | 裁决 | 落点 / 理由 |
|---|---|---|
| CMD-01 ReDoS 持 GIL | **采纳并入**,保持 High | 组长实测睡眠线程停顿 = 正则时长(5.50s),证实看门狗/播放/心跳全停;并入 CCD-01 |
| CMD-02 admin 冒名 High(有条件) | **采纳,CCD-03 由 Medium 升 High(有条件)** | 两条路径 harness 复现;延伸到 Web(interface.py:640-641、web.py:29-30/39-48 行号核实);缓解条件"admin 已注册"文档从未要求。工人引用的 `commands/personal.py:221-232` 有误,`mumble_key` 实际在 25-36,已按正确行号写入 |
| CMD-03 repeat | 重复 | = CCD-02,保持 Medium;工人 50/100 条耗时数据可线性外推 |
| CMD-04 SSRF | 重复 | = CCD-05 |
| CMD-05 dropdatabase | 重复 | = CCD-10;F 段 traceback 证实 `no such table`,维持 Low(admin 自伤) |
| CMD-06 root timer 提权 | **采纳,CCD-15 由 Low 升 Medium** | 链条:chown -R(DEPLOY.md:135)→ User=root 执行 venv/bin/pip → site-packages `.pth` 自动执行;每一环均为仓库自带步骤;前提需先攻破 bot,故不到 High |
| CMD-07/08/09/11/14/16 | 重复 | 分别 = CCD-09/04/20/14/11/12;严重度一致或组长更高(ban 绕过组长定 Medium,理由见 CCD-04:`!yplay 关键词` 使 ban 完全无效,不只是"换写法") |
| CMD-10 !delete + 全局 shortlist | **采纳**,新增 CCD-22 Low | 从 CCD-12 拆出;shortlist 跨用户误删为工人增量 |
| CMD-12 dockerignore | 重复 + 增量 | = CCD-06,采纳 `*.pem`、`tmp/` 补充 |
| CMD-13 供应链合集 | 重复 | 分散在 CCD-16/17/18/19;组长对"未锁版本"仍只保留有确切后果的项 |
| CMD-15 maxvolume | **采纳**,新增 CCD-23 Low | 组长原列 Info,与工人一致为可复现功能缺陷,提为 Low |
| CMD-17 Info 合集 | **采纳**并入 CCD-21 | joinme 跨频道+token、traceback 回显、`!file %`、rtrms 刷日志;行号全部核实 |

**配置一致性对照(第 5 点)**:工人 `config_keys.py` 与组长 `ccd/cfgcheck.py` 结论一致——代码引用的每个 `config.get*` 键在 default.ini 都存在,双方均无"default 缺失项"。组长的增量是反方向检查:default.ini 的 `source_address` 无任何代码读取、example.ini 独有 `youtube_query_cookie`(= 工人 CMD-16)、example 缺 `[spotify]` 节,均在 CCD-12。无冲突。

**未采纳**:无。工人 17 条全部成立,无剔除;两处行号修正(personal.py 行号、config_keys 的动态查询列表与组长一致)。

## 覆盖范围声明
- 通读:`commands/*.py` 全部 10 个文件、`bot/core.py`(命令分发、is_admin、send_msg、启动期 update 线程)、`bot/startup.py`、`util.py`(update/URL/bilibili/regex 相关段)、`constants.py`、`variables.py`、`mumbleBot.py`、`configuration.default.ini`、`configuration.example.ini`、`lang/en_US.json`(键与占位符脚本比对全部 9 个语言文件)、`database.py`(SettingsDatabase/Condition/drop_table)、`media/radio.py`、`media/livestream.py`(validate)、`media/url.py:95-140`、`media/cache.py:free_and_delete`、`media/playlist.py`(append/insert/extend)、`media/url_from_playlist.py:30-80`、`Dockerfile`、`Dockerfile.local`、`entrypoint.sh`、`docker-compose.yml`、`deploy/*.service/*.timer`、`deploy/install.sh`、`update.sh`、`.dockerignore`、`.gitignore`、`.gitattributes`、`.drone.yml`、`requirements.txt`、`webui/package.json`(+lock 存在性)、`scripts/*.sh`、deploy/*.md 与 README 的相关段落(grep 定点核对)、pymumble-upstream 分支的 `src/mumble/mumble.py` 与 `pyproject.toml`(只读拉取)。
- 未看/浅看:`interface.py`、`web_*.py`、`webui/src`(Web 组范围,只在 `banned_ip`、`flask_secret`、legacy 模板处交叉引用);`media/url.py` 下载逻辑、`bot/player.py` ffmpeg 细节(媒体/播放组范围,只核对了子进程参数构造);`scripts/smoke_test.py`、`scripts/translate_templates.py`、`scripts/sync_translation.py`(上游工具脚本,不在运行路径上);`tests/` 只看了 `test_commands_wiring.py` 以确认命令层无权限/封禁相关测试覆盖。
- 无法做:连接真实 Mumble 服务器验证 CCD-03 的同名登录行为与 CCD-20 的客户端 `<img>` 加载行为。
