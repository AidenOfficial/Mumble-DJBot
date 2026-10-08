# Web UI 发布指引(NAS + Cloudflare Tunnel/Access)

> 本文档只描述接入步骤,**不代表已执行部署**。所有命令在 NAS 上手动执行。

## 架构

```
浏览器 ──HTTPS──> Cloudflare edge ──Tunnel──> cloudflared(NAS)──HTTP──> bot:8181
                       │
                 Cloudflare Access(登录鉴权在这一层)
```

- 应用层不做登录:`[webinterface] auth_method = none` 保持现状,鉴权完全交给
  Cloudflare Access。**因此绝不能把 8181 端口直接暴露公网**——只有 cloudflared
  出站隧道能访问它。
- 新 UI 在 `/`,旧界面保留在 `/legacy`,JSON API 在 `/api/*`。所有写操作
  (controls/queue/search-add/上传)与旧接口走同一套 `requires_auth` 约定,
  auth_method 换成 password/token 时新 UI 无需改动。

## 配置项(configuration.ini)

```ini
[webinterface]
enabled = True
listening_addr = 127.0.0.1   ; 容器内用 0.0.0.0,由 compose 网络隔离
listening_port = 8181
is_web_proxified = True      ; 让 Flask 信任 X-Real-IP(经由 ReverseProxied)
```

## docker compose(已内置,tunnel profile)

cloudflared 服务**已经写在仓库根目录的 `docker-compose.yml` 里**,挂在
`tunnel` profile 下,平时不启动。启用步骤:

```bash
cd <部署目录>
echo 'CLOUDFLARE_TUNNEL_TOKEN=eyJh...' > .env    # token 来自 Cloudflare 控制台
docker compose --profile tunnel up -d            # bot + cloudflared 一起拉起
docker compose logs -f cloudflared               # 看到 "Registered tunnel connection" 即通
```

- 不加 `--profile tunnel` 时行为与从前完全一致(只起 bot)。
- 不要给 bot 服务开 `ports`:两个容器在同一 compose 默认网络里,
  cloudflared 用服务名直接回源,8181 不暴露给宿主机/公网。
- `.env` 已被 gitignore,token 不会入库。

## Cloudflare 侧步骤(Zero Trust 控制台)

1. **Tunnel**:Networks → Tunnels → Create tunnel(Cloudflared 类型),
   复制 token 写进 `.env`;Public hostname 的回源 Service 填
   `http://botamusique:8181`(compose 服务名)。
2. **Access 应用**:Access → Applications → Self-hosted,域名同上;
   策略按邮箱/组放行;Session 时长按需(如 24h)。
3. (可选)Access → Service Auth 为自动化脚本发 Service Token。

## 用户身份、别名与个人歌单

Cloudflare Access 回源时会带上登录者邮箱(`Cf-Access-Authenticated-User-Email`),
Web UI 据此识别"是谁":

- 右上角头像 → 设置**别名**(显示名)。在 Web 上点的歌,Mumble 里和统计页都显示别名;
  不设则用邮箱 @ 前面的部分。别名全站唯一(不分大小写),不能含 `< > & "`。
- **Playlists** 页:每个人自己的歌单。任意页面歌曲旁的 ♡ 可加入歌单,也可以把当前
  整个队列存成歌单;歌单可"立即播放 / 随机 / 追加到队列 / 单曲插播"。
- 没有经过 Access(例如局域网直连 8181)时是"Guest":能点歌,没有歌单。

**可选加固:校验 Access JWT。** 只读请求头的前提是 8181 不对外暴露(只能经 Tunnel 访问)。
想彻底防伪造,在 `configuration.ini` 加:

```ini
[webinterface]
access_team_domain = <你的团队名>.cloudflareaccess.com
access_aud = <Access 应用 Overview 页的 Application Audience (AUD) Tag>
```

开启后每个请求都校验 `Cf-Access-Jwt-Assertion`,没有合法 JWT 的一律 403
(局域网直连也会被拒,这是预期行为)。

## 频道与跟随(Settings 页)

- 实时显示服务器的频道树和每个频道里的人(bot 自己高亮,其他 bot 置灰)。
- **☆ Default**:设为默认频道,启动和 `!oust` 时回到这里;优先于 `[server] channel`。
- **Move here**:让 bot 立刻过去。
- 跟随模式:
  - *Stay put*:不动(默认)。
  - *Follow the crowd*:bot 所在频道没人了(只剩 bot / `when_nobody_in_channel_ignore` 里的 bot),
    就去最后离开的那个人去的频道;那人下线了就去人最多的频道。
  - *Follow someone*:始终跟着指定的人;对方离线时按 *Follow the crowd* 处理。
  - 可选"服务器上一个人都没有时回默认频道"。
- 有人换频道后等 2.5 秒再决定,来回切频道不会把 bot 带着乱跑。
- 开着跟随时,`when_nobody_in_channel = pause/stop` 不会在 bot 即将跟过去时误暂停/清空队列。
- bot 需要有进入目标频道的权限(Mumble ACL),没权限的频道服务器会拒绝移动。

## 没人时自动暂停(Settings 页)

- bot 所在频道连续没人(其他 bot 不算)达到设定分钟数就暂停,默认 5 分钟,0 = 关闭。
- 默认有人回到频道就自动继续;只恢复它自己暂停的,不会替人恢复手动暂停。
- 开着跟随模式时优先跟过去,不会暂停。
- 与 ini 里的 `when_nobody_in_channel`(立即暂停/停止)相互独立,一般保持那个为 `nothing` 即可。

## 跳过非音乐片段(SponsorBlock)

- YouTube / B 站视频播放时自动跳过社区标注的片段:MV 片头片尾的说话、赞助口播、求三连等。
- Now Playing 的进度条上用斜纹标出会被跳过的位置。
- 配置见 `configuration.example.ini` 的 `sponsorblock` / `sponsorblock_categories`。
- B 站多 P 视频的片段对应不到具体是哪一 P,这种情况不跳。

## 导入歌单(Playlists 页 → Import a playlist)

- 支持 YouTube 播放列表、网易云音乐歌单、Spotify 歌单/专辑,可以导入成新歌单或追加到当前歌单。
- 网易云在海外基本没有音源,Spotify 本身不提供音频,所以这两个只读歌名/歌手/时长,
  再去 YouTube 按"时长接近 + 官方频道优先 + 排除翻唱/现场/AMV"挑最像的一个。
  找不到的会列出来。导入几百首大约要一两分钟,页面上有进度。
- Spotify 需要 `[spotify] client_id / client_secret`(和 `!spotify` 命令用的是同一套)。

## 绑定 Mumble 账号

- 网页右上角头像 → **Link Mumble account**,生成 6 位绑定码(10 分钟有效),
  在 Mumble 里私聊 bot 发 `!bind 123456`。
- 绑定后在聊天里:
  - `!mylist`:列出我的歌单;
  - `!mylist 名字或序号 [shuffle]`:把歌单加入播放队列;
  - `!fav [歌单名]`:把当前这首加入歌单(默认 Favorites,没有就自动建);
  - `!unbind`:解除绑定。
- 还没设别名的话,绑定时自动用 Mumble 名当别名;统计页会把同一个人在聊天和网页上的点歌合并成一行。
- Mumble 账号的识别优先级:服务器注册用户 ID > 客户端证书指纹 > 用户名。
  没注册的用户换了客户端证书就需要重新绑定。
- 绑定码输错 5 次锁 10 分钟。

## 缓存与上传

- **Cache** 页:下载缓存占用、每首的大小/播放次数/最后使用时间。📌 固定 = 自动清理永不删除;
  💾 = 复制进本地曲库(`music_folder/saved/`);超过 `tmp_folder_max_size`(默认 4GB)时
  按"最久没用"淘汰,播放 ≥3 次的歌最后才淘汰。
- **上传**(Library 页 Upload):分片上传(每片 32MB),不受 Cloudflare 单请求 100MB 限制,
  断网会自动续传;单文件上限 `max_upload_file_size`(默认 4G)。视频默认只保留音轨
  (`upload_extract_audio`),上传完直接进曲库,不用 rescan。

## 验证清单(部署后手动)

- [ ] 域名打开即新 UI,`/legacy` 是旧界面,未登录时被 Access 拦截。
- [ ] 直连 NAS IP:8181 从公网不可达(仅 Tunnel 出站)。
- [ ] `/api/status` 轮询正常、控件/队列/搜索/统计各页可用。
- [ ] 上传大小上限 `max_upload_file_size` 符合预期;上传一个 >100MB 的视频能成功并只剩音轨。
- [ ] 右上角显示的是自己的 Access 邮箱;设置别名后点一首歌,Mumble 里显示别名。
