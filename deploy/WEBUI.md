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

## docker compose(示例,与 deploy/DOCKER.md 的服务合并)

```yaml
services:
  botamusique:
    build: .
    restart: unless-stopped
    volumes:
      - ./configuration.ini:/botamusique/configuration.ini
      - ./data:/botamusique/data
    # 不发布 8181 端口:只让同网络的 cloudflared 访问
    networks: [bot-net]

  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel run
    environment:
      - TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}   # 放 .env,不入库
    networks: [bot-net]

networks:
  bot-net: {}
```

## Cloudflare 侧步骤(Zero Trust 控制台)

1. **Tunnel**:Networks → Tunnels → Create tunnel,拿到 token 填入 `.env`;
   Public hostname 指到 `http://botamusique:8181`(compose 服务名)。
2. **Access 应用**:Access → Applications → Self-hosted,域名同上;
   策略按邮箱/组放行;Session 时长按需(如 24h)。
3. (可选)Access → Service Auth 为自动化脚本发 Service Token。

## 验证清单(部署后手动)

- [ ] 域名打开即新 UI,`/legacy` 是旧界面,未登录时被 Access 拦截。
- [ ] 直连 NAS IP:8181 从公网不可达(仅 Tunnel 出站)。
- [ ] `/api/status` 轮询正常、控件/队列/搜索/统计各页可用。
- [ ] 上传大小上限 `max_upload_file_size` 符合预期。
