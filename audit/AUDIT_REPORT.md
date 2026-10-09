# Mumble-DJBot 代码审计总报告

- 审计对象:`AidenOfficial/Mumble-DJBot`,基线提交 `64109cf`(分支 `claude/quirky-fermi-6nou7s`)。
- 方式:只读审计,不修改业务代码。5 个专项组,每组由一名组长负责验收,另派 Opus/Sonnet 工人独立深挖或复核,组长逐条核对行号并复跑复现后合并。
- 基线:仓库自带 253 个测试全部通过。全部 9 个 High 均有可运行的复现,复现脚本见 [`repro/`](repro/README.md)。
- 分报告(含每条发现的完整证据、修复建议与剔除记录):
  1. [Web 安全](reports/01-web-security.md)
  2. [播放核心与并发](reports/02-playback-core.md)
  3. [数据层、文件清理与导入](reports/03-data-cleanup-import.md)
  4. [命令系统、配置与部署](reports/04-commands-config-deploy.md)
  5. [测试质量与前端工程](reports/05-tests-frontend.md)

## 一、结论

**整体评价:功能扎实,但默认配置下有三条能被普通用户触发的严重路径。**

1. **旧 Flask 接口是最大的风险源。** `/upload` 与 `/library` 两个上游遗留接口完全没有路径规范化。上传可写任意位置文件,容器以 root 运行时可直达代码执行;删除接口的过滤条件会静默失效,一次请求可删光整个曲库。`upload_enabled` 与 `delete_allowed` 默认都是 True,而 `auth_method` 默认是 none。
2. **播放状态机会永久卡死。** "等待就绪"分支只轮询、从不补发下载。`!repeat`、重复点同一链接、切换播放模式、暂停时连按跳过等日常操作都能让机器人无声停播,看门狗不会介入,`!skip` 也救不回来。
3. **任意频道成员一条消息即可冻结整个进程。** `!filematch` 与 `!listfile` 把用户输入直接当正则执行。Python 的正则引擎全程持有 GIL,看门狗线程同样被冻住,进程不会自愈。

做得好的部分:SQL 全部参数化,没有注入面;所有子进程都用参数列表,spotdl 用 `--` 隔离,没有命令注入;Cloudflare Access JWT 校验正确地固定了算法并校验 aud 与 iss;新 Vue 前端没有 XSS 面,提交的 dist 与源码重建逐字节一致;ffmpeg 子进程生命周期管理健全。

### 计数

| 组 | High | Medium | Low | Info |
|---|---|---|---|---|
| Web 安全 | 2 | 6 | 5 | 0 |
| 播放核心 | 3 | 8 | 8 | 3 |
| 数据/清理/导入 | 2 | 6 | 11 | 若干 |
| 命令/配置/部署 | 2 | 7 | 13 | 1 组 |
| 测试/前端 | 0 | 5 | 13 | 4 |
| **合计(未去重)** | **9** | **32** | **50** | — |

跨组去重后约为 9 High、29 Medium、47 Low,去重明细见第五节。

## 二、修复优先级

按"一处修改消解的风险面"排序。前 6 项建议在下一个版本内完成。

| 序 | 修复 | 消解的发现 | 改动量 |
|---|---|---|---|
| 1 | **下线旧 `/upload`、`/library` 删除分支与 `/post delete_item_from_library`**。新 UI 已有分片上传;`LibraryPage` 仍依赖 `/library` 查询与 `/library/info`,先把这两条迁到 `/api` 再删。短期至少对文件名做 `clean_filename`,对最终路径做 `realpath` 落在 `music_folder` 内的校验,并在所有删除入口检查 `delete_allowed` | WEB-07、DL-1、WEB-10、WEB-09/DL-2、WEB-04、WEB-13 | 中 |
| 2 | **启动时拒绝默认或空的 `flask_secret`**,或首次启动自动生成随机密钥并持久化 | WEB-01 | 小 |
| 3 | **等待分支补发下载**:当前曲未就绪且无人在下载时调用 `async_download`(`bot/player.py:797`) | PC-13、RACE-1,并让 PC-01 可自愈 | 小 |
| 4 | **`URLItem.validate()` 在 `downloading` 或 `ready == 'preparing'` 时直接返回**(`media/url.py:101`) | PC-01 | 小 |
| 5 | **新增 `playlist.remove_current()` 统一回退指针**,`remove_by_id` 改倒序删除并在锁内收集 | PC-02、PC-03、PC-14、PC-15 | 小 |
| 6 | **`!filematch`/`!listfile` 改为子串匹配**(`re.escape` 或 `in`);`!repeat` 加上限 | CCD-01、CCD-02 | 小 |
| 7 | admin 判定改用注册 uid 或证书哈希,不再比对显示名 | CCD-03 | 中 |
| 8 | 所有取 URL 的入口(Web 与聊天的 `!radio`/`!live`/`!rbplay`)统一调用 `is_public_url` | WEB-03、CCD-05 | 小 |
| 9 | 清理模块:Spotify 分支只删"确属本程序"的文件,目录比较统一用 `realpath`,`_prune_cache` 增加与曲库重叠的保护 | F1、F2、F3、CL-6 | 中 |
| 10 | `.dockerignore` 补 `.env`、`cookies/`、`*.pem`、`cache/`;敏感命令参数不写入日志 | CCD-06、CCD-07 | 小 |
| 11 | `ReverseProxied` 只信任固定可信代理的 `X-Real-IP` | WEB-02 | 小 |
| 12 | 上传按已承诺字节预扣空间并限制挂起数;前端取消时调用 `DELETE /api/upload/<id>` | WEB-11、前端 F2 | 中 |
| 13 | 修复测试顺序依赖,并把第 3~5 项的复现用例转成回归测试 | T1、T2 | 小 |

## 三、High 发现

### H1 · 旧 `/upload` 文件名路径穿越,可写任意文件(WEB-07)
- 位置:`interface.py:769`、`interface.py:796`。
- 文件名原样拼进最终路径。目录参数的校验挡不住文件名里的 `../../`,Werkzeug 也不清洗 multipart 文件名。类型门禁只看客户端自报的 Content-Type。
- 影响:任何通过 Web 鉴权的用户可在容器内任意可写位置新建内容可控的文件。镜像以 root 运行,写入 `site-packages/*.pth` 即可在下次启动解释器时执行代码,而 compose 的 healthcheck 会周期性启动解释器。
- 复现:`repro/web/repro_upload.py`。

### H2 · 默认 `flask_secret` 可伪造会话,完全绕过 token 鉴权(WEB-01)
- 位置:`configuration.default.ini:90`、`bot/startup.py:41`、`interface.py:155`。
- 会话 cookie 用仓库里写死的公开字符串签名。token 模式下只要会话里有 `user` 键就放行,不回查令牌库。
- 前提:token 模式、未改默认密钥、Web 可达。example.ini 的示例值正是 token 模式。
- 复现:`repro/web-verify/v_web01_02.py`。

### H3 · 旧 `/library` 删除的范围远超用户选择,可删整库(DL-1,同主题 WEB-10、WEB-09)
- 位置:`interface.py:598-604`、`interface.py:685-699`、`interface.py:496-505`。
- 类型同时选中 file 与 url 时,目录条件被静默丢弃,删掉全部本地文件。目录 LIKE 不转义 `_` 且不区分大小写,删 `my_band/` 会连带删 `myXband/`。末尾的 `os.rmdir(music_folder + dir)` 可用 `../` 删曲库外的空目录。另一入口 `delete_item_from_library` 根本不检查 `delete_allowed`。
- **跨组更正**:Web 组报告称 `delete_allowed` 默认 False,实际 `configuration.default.ini:44` 为 True,因此这一组漏洞默认可达。
- 复现:`repro/data/repro_deep_cleanup.py::LegacyLibraryDelete`、`repro/web-redteam/repro_rmdir_traversal.py`。

### H4 · Spotify 清理分支删除 `download_folder` 内任意过期文件(F1)
- 位置:`bot/cleanup.py:234-262`。
- tmp 分支只删 32 位十六进制名或数据库登记过的路径,并保护固定与常听条目。Spotify 分支对目录里所有普通文件只看修改时间。
- 前提:管理员把 `download_folder` 设成 `/tmp/`、`./` 这类非专用目录。默认值 `spotdl_cache/` 是专用目录,不受影响。
- 影响:删别的进程的临时文件、配置文件与源码;正常配置下也会删掉常听的 Spotify 缓存(CL-6)。
- 复现:`repro/data/repro_cleanup_data.py`。

### H5 · 等待态从不补发下载,主循环永久卡住(PC-13,并入 RACE-1)
- 位置:`bot/player.py:772-798`。
- 已复现的触发路径:暂停时连按跳过超出预取窗口后恢复;清空后重加正在下载的同一 URL;Cache 页强制删除当前曲缓存后恢复(RACE-1);以及 H6 的结局。
- 影响:播放无声停住,Web 显示"等待准备",看门狗不介入,`!skip` 无效。
- 复现:`repro/playback/repro_playback_audit.py`、`repro/data/repro_deep_cleanup.py::StopDeleteResumeStall`。

### H6 · 下载中的条目被二次校验时文件被删(PC-01)
- 位置:`media/url.py:98-116`。
- 边下边播默认开启(`configuration.default.ini:71`)。`validate()` 看到"文件存在且有 `.incomplete` 标记"就当成崩溃残留删除,不区分下载是否仍在进行。`!repeat`、重复点同一链接、切换播放模式都会触发二次校验,随后落入 H5。
- 复现:`repro/playback/repro_playback_core.py`。

### H7 · 删除当前曲后主循环再 `next()`,吞掉下一首(PC-02)
- 位置:`bot/player.py:740-755`、`bot/player.py:784-786`。
- 任何解码失败或下载失败都会额外跳过下一首。默认的 one-shot 模式下是直接删除用户点的歌。下载失败是线上最常见的故障。
- 复现:`repro/playback/repro_playback_core.py::FfmpegFailureSkipsFollowingItem`。

### H8 · 用户正则直接执行,可冻结整个进程(CCD-01)
- 位置:`commands/sources.py:89`、`commands/sources.py:359`。
- 正则引擎匹配期间不释放 GIL。组长实测一个 10 毫秒轮询的线程在 5.5 秒的匹配期间停顿了 5.5 秒,看门狗、播放、心跳与 Web 全部停摆。
- 前提:曲库里至少有一个本地文件条目。
- 复现:`repro/commands/verify.py`。

### H9 · admin 仅凭显示名判定,可被冒名(CCD-03,有条件)
- 位置:`bot/core.py:428-432`、`bot/core.py:328-331`。
- 管理员账号未在 Mumble 服务器注册时,任何人可在其离线时用同名登录。开启 `split_username_at_space` 后,`Alice (guest)` 即使管理员在线也能冒充。冒名身份还会经 `!web`、`!password` 带进 Web 端。
- 仓库内已有按注册 uid 或证书哈希生成稳定标识的 `mumble_key()`(`commands/personal.py:25-36`),但 admin 判定没有用它。部署文档从未要求注册管理员账号。
- 复现:`repro/commands/repro_dispatch.py`。

## 四、Medium 发现(去重后)

**Web 与鉴权**
- WEB-02:封禁与限速以可伪造的 `X-Real-IP` 为键,可无限暴破或定向封禁他人。
- WEB-03 + CCD-05:Web 与聊天的取 URL 入口缺 `is_public_url`,可让机器人探测内网(盲 SSRF,电台路径会回显标题)。
- WEB-11:分片上传按声明大小检查空间且不预扣,并发初始化可占满磁盘。前端 F2 取消上传后不通知服务端,残留文件 24 小时不清理,两者叠加。
- WEB-12:Mumble 绑定码只有 6 位数字,限速键可通过改名轮换,可劫持他人的个人歌单。

**播放核心**
- PC-03:`remove_by_id` 遇重复条目时删错歌,留下的孤儿条目会杀掉刚开播的歌。
- PC-04:`interrupt()` 在 ffmpeg 未运行时什么都不做,等待期跳过无效,启动窗口内暂停会丢失。
- PC-05:直播与电台在音频线程做同步网络请求,每次开播必断音。
- PC-14:single 模式末曲失败后每 0.1 秒抛一次 IndexError。
- PC-15:等待中删除当前曲会播错歌。
- PC-16:`resume()` 的三条早退路径会多跳一首。
- PC-17:控制方法与主循环无互斥,状态发布顺序错误。
- PC-18:断线后主循环卡在等缓冲的内层循环,只能等 120 秒看门狗重启。

**数据与清理**
- F2:`_prune_cache` 没有曲库重叠保护,`download_folder` 放进曲库会删曲库文件。
- F3:目录重叠保护用 `abspath` 比较,符号链接可绕过。
- F4:歌单导入没有并发与频率限制。
- DB-1(= CCD-10):`!dropdatabase` 后不重建表,还把曲库库指到设置库,重启前所有功能报错。结合 H9 可被冒名者触发,故取 Medium。

**命令与部署**
- CCD-02:`!repeat N` 无上限。
- CCD-04:URL 封禁是精确匹配,短链、移动版域名、参数换序或 `!yplay 关键词` 都能绕过。
- CCD-06:`.dockerignore` 漏掉 `.env`(Cloudflare Tunnel token)、`cookies/`、`*.pem`,会被打进镜像。
- CCD-07:命令参数原样写入 INFO 日志,包括 `!password` 的明文。
- CCD-08:`--tokens` 与 `BAM_TOKENS` 被 pymumble 逐字符拆分,永远无效。
- CCD-15:systemd 定时任务以 root 执行 bot 用户可写的 venv,机器人被攻破后可提权到 root。

**测试与前端**
- T1、T2:测试存在顺序依赖。单独运行 `tests/test_play_history.py` 或 `tests/test_livestream.py` 各失败 1 条,反序运行全量也失败。
- 前端 F1:状态轮询没有超时,网络半开时页面静默冻结。
- 前端 F6:队列操作只提交下标,多人同时操作时会删或播错曲目。

Low 与 Info 共约 50 条,见各分报告。

## 五、跨组去重与裁决

| 主题 | 涉及条目 | 裁决 |
|---|---|---|
| `delete_item_from_library` 绕过开关 | WEB-09、DL-2 | 同一缺陷,并入 H3 主题 |
| `/library` 删除接口 | DL-1、WEB-10、WEB-09 | 合并为 H3。更正 `delete_allowed` 默认值为 True |
| SSRF | WEB-03、CCD-05 | 合并;修复须同时覆盖 Web 与聊天入口 |
| `!dropdatabase` | DB-1(Medium)、CCD-10(Low) | 取 Medium,理由见上 |
| 停止后强删缓存再恢复卡死 | RACE-1、PC-13 | 同一根因,并入 H5 |
| 上传磁盘耗尽 | WEB-11、前端 F2 | 根因不同(服务端无配额、客户端不清理),保留两条并列 |
| `save_music_library` 无效 | CCD-11、数据 F8 | 同一缺陷 |
| `/download` 不存在 id 返回 500 | 数据 F9、WEB-13 | 同一缺陷 |
| 绑定码暴破 | WEB-12 成立;数据组与命令组曾剔除 | 以 Web 红队为准:锁定以可轮换的 `mumble_key` 为键,能被绕过 |
| `is_admin(user)` 使用模块全局 `user` | WEB-06、前端 B5 | 成立。影响限于 `/library/info` 返回的显示标志,另有多线程串号 |

## 六、审计过程中被推翻的初判

验收环节推翻了 3 条组长初判,说明独立复核是必要的:

- Web 组长初审把旧 `/upload` 的文件名穿越剔除了,原因是复现只用了一层 `../`。复核工人用两层 `../` 证实可逃逸,升为 High(H1)。
- 数据组长初审把"删除与播放的竞态"整体剔除了。工人复现了"停止后在 Cache 页强删再恢复"会永久卡住,改列为 RACE-1。
- 播放组长初审把"恢复时遇到未就绪曲目就跳下一首"视为上游行为剔除。工人证明边下边播场景是本 fork 新增且现实存在,改列入 PC-16。

## 七、局限

- 容器内无法连接真实 Mumble 服务器,`pymumble` 也未安装。冒名登录是否被服务器放行、客户端是否加载远程 `<img>` 等结论只验证到代码层。
- 未在浏览器里实际运行前端,前端结论来自读代码、对照后端与 Flask test client。
- 未在 Windows 上验证 `nopart` 边下边播的行为。
- 运行环境是 Python 3.13,需要额外安装 `audioop-lts`;`requirements.txt` 未声明该依赖(CCD-19)。
