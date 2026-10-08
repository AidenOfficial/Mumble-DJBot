# 前端工程组(工人)报告:webui/ 审计 + 构建验证 + 旧前端状态

范围:webui/src 全部 21 个文件(api.ts、upload.ts、main.ts、App.vue、composables/*、components/*.vue 共 12 个)逐行读过;
后端对照读了 web_upload.py、web_api.py(queue/status/search)、interface.py(静态路由/legacy/library)、web_search.py(头部)。
未运行浏览器(环境无法起真实 bot),所有结论靠读代码 + 对照后端 + 小脚本(Flask send_from_directory 头部、Node md4)。

## 总评
前端整体写得克制:无 v-html / innerHTML / eval / window.open / location 赋值;唯一动态 href 见 FE-13(现实不可利用);
所有轮询 timer(QueueList/SettingsPage/UserChip/PrepPanel)在卸载时清理;模板插值全是文本,XSS 面干净。
没有 High 及以上。真正值得修的是 3 件:
1. 队列操作按"数组下标"提交且服务端不校验(FE-01),多人同时点歌/切歌时会删/播错曲目;
2. 上传取消/失败不通知服务端清理 staging(FE-02),默认 4G 上限 + 默认开启,反复取消可吃满磁盘;
3. useStatus 轮询无超时、无乱序保护(FE-04/05),卡死后 UI 静默过期。
构建:`npm ci && npm run build` 成功,重建产物与已提交 dist 逐字节一致(见 B 节)。仓库已还原,`git status` 干净。

---
## A. 发现(按严重度)

### FE-01  Medium  队列操作用下标定位,陈旧列表会作用到别的曲目
- 位置:webui/src/components/QueueList.vue:36(move)、140(play)、146(top)、154(remove)、149(AddToPlaylist queue index);
  服务端 web_api.py:243-273(`api_queue_edit` 只取 index/to)、web_api.py:162-178(`_queue_remove` 仅范围校验)。
- 问题:前端把"我点的是第 i 行"当成 `{action, index: i}` 发出,服务端只校验 0<=index<len,不校验该下标上还是不是用户看到的那首歌。
- 证据:
  ```
  QueueList.vue:140  @click="act({ action: 'play', index: i })"
  QueueList.vue:154  @click="act({ action: 'remove', index: i })"
  web_api.py:249-250 index = int(payload.get('index', -1)) ... elif action == 'remove': _queue_remove(index)
  ```
  列表只在 (a) /api/status 的 `version` 变化(≤3s 轮询,QueueList.vue:54)、(b) 10s 定时器(:58)、(c) 自己 act 之后刷新。
  路径:A 打开页面看到 [X,Y,Z] → B 在 Mumble 里 `!rm 1`/或 Web 端删除 X(或某首播完被移出)→ A 在 ≤3s(加请求往返)内点 Z 行的 ✕,实际 index=2 已是越界(400,被吞)或
  列表缩短后落在另一首上 → 删/播了错误的曲目。拖拽同理:`dragFrom` 在 dragstart 时按旧顺序记录(:15),拖动过程中 reload 替换 `items`(:47)后 drop 用的仍是旧 from 下标。
- 影响:共享 DJ 机器人的典型多人场景下,误删/误跳他人点的歌;无任何提示(act 的 catch 静默 reload,:68-69)。
- 修复:请求里带上 `item_id`(`item.id`)或 `version`(列表来自的 `q.version`),服务端比对 `playlist[index].id`(或 `playlist.version`)不一致时返回 409,前端 409 时刷新列表并提示"队列已变化"。
  最小 patch:前端 postQueue 加 `id`,`_queue_remove/_queue_move` 和 play/top 入口前加 `if body_id and var.playlist[index].id != body_id: abort(409)`。
- 置信度:Likely(代码路径无歧义;哪些事件会 bump `playlist.version` 未在本组逐一核实,多人编辑必 bump)。

### FE-02  Medium  上传取消/失败后 staging 文件残留,客户端从不调用 DELETE;取消提示也不对
- 位置:webui/src/upload.ts:54(abort 检查)、74-76(catch/重试)、80-88(finish/轮询);LibraryPage.vue:137-140、218(取消按钮);
  服务端 web_upload.py:292-301(`DELETE /api/upload/<id>` 存在)、web_upload.py:110-119(`prune_stale` 仅在下一次 init 时运行)。
- 问题:`grep -n DELETE webui/src/upload.ts` 无结果——客户端没有任何路径清理服务端已收到的分片。
- 证据:点 ✕ → `ctrl.abort()` → upload.ts:57 的 fetch 抛 AbortError → :74 `if (opts.signal?.aborted || e instanceof UploadError) throw e` 直接把 DOMException 往外抛 →
  LibraryPage.vue:139 `err instanceof UploadError ? ... : 'Upload failed.'` 显示"Upload failed."(而不是 UPLOAD_ERRORS.aborted 的 "Cancelled.",该分支只在 abort 恰好落在两片之间的 :54 才触发)。
  服务端 `.part` 与 `.json` 留在 `tmp_folder/.uploads`,默认 `max_upload_file_size = 4G`、`upload_enabled = True`(configuration.default.ini:95,97),
  只有下一次有人 init 且文件 mtime>24h 才会被 prune。5 次网络重试失败(:75)同样遗弃已上传的分片。
- 影响:任何已登录用户反复"选 4G 文件→传到一半取消"可使 bot 的 tmp_folder 所在磁盘被占满(init 只检查当时剩余空间 `size*1.05 > free`,web_upload.py:219),随后 yt-dlp 下载/缓存失败。浏览器直接关页也同样遗留(见 FE-11)。
- 修复:uploadFile 里用 try/finally:除 `done` 外,失败/取消路径 `fetch(DELETE /api/upload/${id}, {keepalive:true})`;abort 时统一抛 `new UploadError('aborted')`;
  服务端另可在 `prune_stale` 之外加定时清理(或 init 时同时清理 status != done 且 >1h 无更新的 .part)。
- 置信度:Confirmed。

### FE-03  Low  上传"处理中"阶段没有重试/超时:瞬时网络抖动 = 误报失败;进程崩溃则无限轮询
- 位置:upload.ts:81-88;web_upload.py:156(`meta = _load(upload_id)` 在 try 之外)、web_upload.py:132-148(ffmpeg 超时 3600s)。
- 证据:
  ```
  upload.ts:81  const fin = await fetch(`${BASE}/api/upload/${id}/finish`, { method: 'POST', signal })
  upload.ts:84  for (;;) {
  upload.ts:85    const st = await (await fetch(`${BASE}/api/upload/${id}`, { signal })).json()
  upload.ts:86    if (st.status === 'done' || st.status === 'error') return st
  upload.ts:87    await sleep(1000) }
  ```
  chunk 阶段有 5 次退避重试,finish 与轮询阶段完全没有:finish 响应丢失 → 抛 TypeError → UI "Upload failed.",但服务端 finalize 线程照常入库(重传会产生 `name (1)` 重复);
  轮询中任一次 fetch/JSON 失败同样整条上传被判失败。反向:finalize 线程在 `_load` 处因 meta 被并发 DELETE 抛 NotFound、或 bot 重启,meta 永远停在 `processing`,客户端以 1 次/秒无限轮询(无上限、无 UI 可取消——取消按钮只在 phase==='uploading' 时显示,LibraryPage.vue:217)。
- 影响:体验与重复文件;不影响安全。
- 修复:轮询包 try/catch 容忍 N 次失败;加总超时(如 视频 30 分钟);finish 失败时先 GET 状态再判定;处理中阶段也显示取消。
- 置信度:Confirmed(代码路径),触发需网络抖动/异常。

### FE-04  Low  轮询响应与控制响应乱序时,旧状态覆盖新状态
- 位置:webui/src/composables/useStatus.ts:16-22(applyStatus 无条件覆盖)、24-27(poll)、62-67(control);同类:SearchPage.vue:72、LibraryPage.vue:150、QueueList.vue:66 都 applyStatus(响应)。
- 证据:poll 在 t0 发出,用户在 t0+ε 点 Pause → control 响应先到并 applyStatus(play=false) → t0 的 poll 响应(服务端在 Pause 之前生成)后到,`status.value = s` 把 play 改回 true,UI 显示"仍在播放"最长 3s(1s 当 prep/download)。
  服务端已提供 `server_time`(web_api.py:66),前端从未使用。
- 影响:按钮/进度条短暂回弹,偶发"点了没反应"的错觉;可自愈,不会丢操作。经 Cloudflare Tunnel 延迟较大时概率明显。
- 修复:`applyStatus` 里 `if (status.value && s.server_time < status.value.server_time) return`;或给每次请求打递增序号,只接受不早于已应用序号的响应。
- 置信度:Likely。

### FE-05  Low  状态轮询无超时、无 visibilitychange;fetch 挂起则轮询永久停止
- 位置:useStatus.ts:49-52(`await poll(); setTimeout(pollLoop, ...)`)、api.ts:63-67(getJson 无 AbortSignal/超时)。
- 证据:pollLoop 先 await poll() 再排下一次;若 fetch 因网络切换/半开连接永远不 settle(浏览器无默认超时,可持续数分钟),下一次 setTimeout 永远不会被安排,`error` 也不会置位——页面静默过期,直到刷新。
  另外隐藏标签页仍以 1~3s 轮询(浏览器节流后在后台 Chrome 可降至 1/分钟),切回前台不会立刻 refresh;多人长期挂着页面会对 /api/status 形成持续负载。
- 修复:`AbortSignal.timeout(8000)` 加到 getJson;pollLoop 用 try/finally 保证重新排程;监听 `visibilitychange`:hidden 时降频(如 15s),visible 时立刻 `poll()`。
- 置信度:Likely(挂起场景需要网络半开)。

### FE-06  Low  搜索:清空输入后旧结果会"复活";Enter 与防抖双发
- 位置:SearchPage.vue:34-40(q<2 提前 return 且不递增 seq)、41(`++seq`)、47(`mySeq !== seq`)、29-32 + 100(`@input` 防抖 + `@keydown.enter`)。
- 证据:输入 "ab" → 400ms 后发出请求(seq=1,yt-dlp+bilibili 通常数秒);请求在途时用户删光输入 → 400ms 后 search() 走 q.length<2 分支,仅清空 results,**没有 `++seq`** 也没有 `loading=false`;
  之后 seq=1 的响应到达,`mySeq === seq` 成立,`results.value = data.results; searched = true` —— 搜索框为空却显示旧结果,且 loading 转圈直到响应到达。
  Enter(:100)直接调用 search() 不取消待触发的防抖定时器,"打字后立刻回车"会向后端发两次同样的重型搜索。输入慢于 400ms 的节奏会堆积并发 yt-dlp 搜索(前端无 AbortController,后端也不取消)。
- 修复:q<2 分支里先 `++seq; loading.value = false`;Enter 时 `clearTimeout(debounce)`;新搜索前 abort 上一次 fetch。
- 置信度:Confirmed(代码路径)。

### FE-07  Low  未捕获的 Promise 拒绝:删除/解绑失败无提示
- 位置:UserChip.vue:85-89(`unlink` 的 `await unbindMumble()` 无 try)、PlaylistsPage.vue:77-84(`remove` 的 `await deletePlaylist`)、PlaylistsPage.vue:169-173(`dropItem` 的 `await removeFromPlaylist`)。
- 证据:
  ```
  PlaylistsPage.vue:80  await deletePlaylist(detail.value.id)      // 无 try/catch
  PlaylistsPage.vue:171 detail.value = await removeFromPlaylist(...) // 同上
  ```
  服务端 4xx/5xx(例如另一端已删除该歌单→404、登录失效)→ `unhandledrejection`,界面毫无反馈,按钮看起来"没反应"。
- 修复:仿同文件 `play()`/`rename()` 用 try/catch + `say('...failed')`。
- 置信度:Confirmed。

### FE-08  Low  /api/me 首次失败后永不重试,页面永久显示 Guest 且个人歌单功能整体消失
- 位置:useMe.ts:9-15(catch 吞错)、29-32(`started` 只启动一次);UserChip.vue:199-205("You're browsing as a guest" 文案)、AddToPlaylist.vue:56(`v-if="me?.can_have_playlists"`)、PlaylistsPage.vue:197。
- 证据:页面加载瞬间网络抖动/后端重启 → `reloadMe` 失败,`me` 永远是 null;`started=true` 不会再触发;状态轮询恢复后 UI 其余部分正常,但所有 ♡ 按钮消失、头像显示"Guest",且提示用户"No Cloudflare Access identity reached the bot"(误导)。直到手动刷新。
- 修复:`reloadMe` 失败时 setTimeout 退避重试;或在 useStatus 轮询恢复(error 从有到无)时顺带 `reloadMe()`;`me === null` 时区分"加载中/失败"而不是 Guest。
- 置信度:Confirmed。

### FE-09  Low  无防重复点击:Skip、创建歌单、导入
- 位置:Controls.vue:52(skip 无 in-flight 保护,`control()` useStatus.ts:62 也无);AddToPlaylist.vue:35-46 与 PlaylistsPage.vue:51-62(`newName` 在 await 之后才清空);PlaylistsPage.vue:116-120、252(`importRunning` 要等第一次 fetchImportJob 之后才为 true)。
- 证据:双击 ⏭ → 两个 `POST /api/controls {action:'skip'}` → 连跳两首(`_skip` 非幂等,web_api.py:123);创建歌单表单在请求往返期间回车两次 → 创建两个同名歌单(服务端是否有唯一约束未核实,本组未验证);Import 在 startImport 返回前可再次提交 → 两个导入任务。
  AddToPlaylist.addTo(:23) 对同一歌单快速双击会先显示"Added ✓"再被第二次的"Already in"覆盖(服务端去重,无副作用,仅文案误导)。
- 修复:control 内加 `inflight` 标志或对 skip 做 300ms 节流;创建/导入按钮在请求期间 `:disabled`。
- 置信度:Confirmed(skip),其余 Likely。

### FE-10  Low  曲库查询:无 ok 检查、无乱序保护,错误被显示成"空曲库"
- 位置:LibraryPage.vue:33-34(`/library/info` 不检查 rv.ok)、47-69(query)、277-279。
- 证据:`const rv = await fetch('../library', ...); const data = await rv.json()` —— 500/401 返回 HTML → `.json()` 抛 SyntaxError → catch 里清空 items → 页面显示"Nothing in the library matches."(把故障伪装成空结果)。
  `page.value = toPage` 在请求前就写入(:49),快速翻页/切类型时较慢的旧响应后到会覆盖较新响应,页码标签与内容不一致(SearchPage 有 seq 守卫,这里没有)。
- 修复:检查 rv.ok 并展示错误;加 seq 守卫;`page.value` 在成功后再赋值。
- 置信度:Confirmed。

### FE-11  Low  切换页签会丢失上传进度 UI 与取消入口,上传继续在后台跑
- 位置:LibraryPage.vue:99(`uploads` 是组件局部 ref)、110-141;App.vue 的 `v-else-if` 切换(App.vue:513-523 模板区)会销毁 LibraryPage。
- 证据:上传中点到 Search 页再回来:`uploads` 已重置为空,`onUpload` 的 async 循环仍在跑(没有 onBeforeUnmount abort),用户既看不到进度也无法取消;完成后 `query(page.value)` 在已卸载组件上执行。关闭标签页则留下 FE-02 的残留。
- 修复:把上传队列放到模块级单例(像 useStatus),或 `<KeepAlive>` 包裹视图;卸载/`beforeunload` 时 abort 并 DELETE。
- 置信度:Confirmed。

### FE-12  Low  队列拖拽:乐观重排在 busy 时被丢弃;key 含下标导致整段重挂载;触屏不可用
- 位置:QueueList.vue:34-36 + 62-63、95(`:key="\`${item.id}-${i}\`"`)、106(`draggable="true"`)。
- 证据:`onDrop` 先 `items.splice` 乐观重排(:34-35)再 `await act(...)`;`act` 在 `busy.value` 为真时直接 return(:63)——界面显示已移动但请求从未发出,直到 in-flight 操作结束后的 `reload()` 才把顺序"弹回去",用户的拖动被静默丢失。
  key 含 `i`:任何一次前移/删除都让其后所有行 key 改变 → 整段 `<li>` 重新挂载(缩略图重新请求、已打开的 ♡ 下拉被销毁、正在进行的拖拽状态丢失),没有错位但有闪烁;因为同一首歌可重复入队,无稳定 id 可用。
  HTML5 drag&drop 在多数手机浏览器上不触发,窄屏布局(App.vue 的 md: 断点)下队列重排实际只能靠 ⤴;无键盘替代。
- 修复:busy 时排队或禁用拖拽;服务端 /api/queue 给每行返回稳定 `uid`(或带 version)作 key;为触屏加上下移按钮。
- 置信度:Confirmed。

### FE-13  Info  CachePage 的 `:href="e.url"` 无前端 scheme 校验
- 位置:CachePage.vue:198。
- 证据:`<a v-if="e.url" :href="e.url" target="_blank" rel="noopener noreferrer">`。`e.url` 来自 music_db 的 url 条目(bot/cache_store.py:162);当前所有入口都经 `util.get_url_from_input`(util.py:314-329,只放行 http/https)或 web_api.py:301 的 `startswith(('http://','https://'))`,
  因此现实中 javascript:/data: 进不了库。若将来新增导入入口(如 web_users 歌单 `source:'url'` 分支,web_users.py:719 附近本组未核实是否校验)忽略 scheme,这里会成为存储型 XSS 落点。
- 修复:渲染前 `/^https?:\/\//i.test(e.url)`,否则不渲染 `<a>`。
- 置信度:Confirmed 无当前利用面(防御性)。

### FE-14  Info  其他小项(合并)
- 无运行时响应校验:`getJson<T>` 只做类型断言(api.ts:63-67),`StatsPage.vue:58-59`(`stats.value?.top_tracks.map`)、`LibraryPage.vue:254`(`item.tags.slice`)等对字段缺失会 TypeError;前后端部署版本不一致(只更新其中一端)时才会触发。
- 连接错误只在 Now Playing 页显示(NowPlaying.vue:160-162),其它页签断连时无提示;control 失败文案"Can't reach the bot: /api/controls -> 400"把 4xx 说成"连不上"。
- Cloudflare Access 会话过期时,fetch 跟随 302 到跨域登录页 → CORS 失败 → 永远 "Can't reach the bot: Failed to fetch",无"请重新登录/刷新"提示(api.ts:63-77)。
- App.vue:28/31/41:`localStorage` 未包 try/catch(Safari 隐私/禁用存储会抛 SecurityError,仅控制台报错,主题仍可切换);主题在 onMounted 后才应用 → 偏好为 light 而系统为 dark 时首屏闪烁。
- 刷新后总是回到 "Now Playing":`view` 只是内存状态(App.vue:439),没有 hash/路由持久化(因此"深链接刷新 404"问题不存在,但也没有深链接)。
- 前端 rAF 循环(useStatus.ts:40)播放中每帧触发 NowPlaying 重渲染,手机耗电偏高(低优先)。
- SearchPage `added[key]==='done'` 永久禁用按钮(:145),同一搜索结果页内无法再次点同一首。
- 上传进度按 32MB 分片粒度更新(upload.ts:72,fetch 无上传进度事件),小于 32MB 的单曲进度条 0→100% 直接跳变(LibraryPage.vue:228)。
- 后端 finalize 的 `error=str(e)[:200]`(web_upload.py:187)会原样显示在 UI(LibraryPage.vue:135),可能带出服务器本地路径(仅已认证用户,Info)。
- (跨组提示)interface.py:640-641 `library_info` 里 `var.bot.is_admin(user)` 使用的是模块全局 `user`(interface.py:78,在 requires_auth 内被 `global user` 逐请求改写,:139/:157/:163),多线程下会串用户;属后端组范围,仅提示。

---
## B. 构建验证
- 命令:`cd webui && npm ci && npm run build`(Node v22.22.0)。
- `npm ci`:70 packages,2s,**无 deprecation/engine 警告**;摘要 "5 vulnerabilities (1 moderate, 4 high)"。
- `npm run build`(vue-tsc -b && vite build,vite v8.1.5):类型检查通过,44 modules,产物 index.html 0.51kB、assets/index-CFiRT3aR.css 22.66kB、assets/index-BjzSu08G.js 149.22kB。
- 与提交的 dist 对比:`git status --porcelain webui/dist` 为空、`git diff --stat webui/dist` 为空;重建 index.html 引用 `index-BjzSu08G.js` / `index-CFiRT3aR.css`,与 HEAD 版本完全一致 => 提交的 dist 与源码同步,构建可复现。
- 还原:已执行 `git checkout -- webui/dist && git clean -fd webui/dist`,`git status --porcelain` 输出为空(node_modules 被 .gitignore:132 忽略)。
- `npm audit --omit=dev`(均无实际利用面,Info):
  - `@vue/server-renderer <3.5.42`(high,SSR 属性名 XSS)——仅通过 `vue` 间接依赖,**产物是纯客户端包,bundle 中无 server-renderer/renderToString**(grep 为空),无利用面;顺手升 vue>=3.5.42 即可。
  - `nanoid <3.3.18`(high,size=0 死循环)、`postcss <=8.5.22`(moderate,sourceMappingURL 读 .map)、`source-map-js`(high,source map DoS)——均是 vite/tailwind 的**构建期**依赖,不进入产物/运行时,仅在 CI 处理不可信 CSS/sourcemap 时相关。无需紧急处理,`npm audit fix` 可解。
- vite.config.ts 与 Flask 路由匹配性:`base: './'`(vite.config.ts:9)。index.html 用 `./assets/...`、`./favicon.svg`;
  interface.py 同时在 `/`(:266-271)与 `/app/`(:298-304)返回同一 index.html:在 `/` 下解析为 `/assets/<f>`(:280-283 `webui_assets`)与 `/favicon.svg`(:286-288),在 `/app/` 下解析为 `/app/assets/<f>`(`webui_app` 通配 path)——两种挂载点都能加载,匹配。`/app`→302 `./app/`(:292-295)正确(相对 Location 解析为 /app/)。
  API 基址 `..`(api.ts:61)相对 `/` 或 `/app/` 都落到 `/api/...`,反代前缀下也成立。
- SPA 刷新:无前端路由,`/app/<任意>` 走 send_from_directory 缺文件即 404,`/` 与 `/app/` 刷新均正常(见 FE-14,刷新会回到首页签)。
- 静态资源缓存头(用 Flask 3.1.3 send_from_directory 实测):`Cache-Control: no-cache` + `ETag` + `Last-Modified`,带 If-None-Match 返回 304。即每次都要重新验证,hash 文件名的 /assets 没有 `immutable` 长缓存——安全无碍,仅多一次往返(Info)。`/api/thumbnail` 为 `private, max-age=86400`(web_api.py:343)。`/assets/*` 受 requires_auth 保护,`/favicon.svg` 不受。

## C. 旧前端 web/ 与 /legacy、/playlist、/post
- `/post`、`/library`、`/library/info`:**仍在被新 UI 使用**——LibraryPage.vue:33(`../library/info`)、:59(`../library`)、:74 与 :147(`../post` add_item_next/add_item_bottom)。**不是死代码**,删旧路由前需先迁移到 /api/*。
- `/playlist`(GET,interface.py:307)、`/upload`(:757)、`/download`(:809):新 UI 与 webui/src 均不引用(新上传走 /api/upload/*,新队列走 /api/queue),仅旧 jQuery 前端使用;`/legacy`(:274-277)是旧界面入口。tests/ 里无对这些路由的引用(grep 仅命中 playlist_import 的 URL 字符串)。
- `/legacy` 在生产镜像里实际是 404:主 Dockerfile 只构建 webui(Dockerfile:2-9,35),不构建 web/;web/templates 仓库内只有 index.template.html/need_token.template.html,`index.<lang>.html` 是构建 + translate_templates.py 生成物,`_legacy_index()`(interface.py:257-264)缺文件时返回 "Legacy interface not built" 404。只有 Dockerfile.local(node:14-bullseye-slim,:23)与 .drone.yml(上游遗留)仍会构建旧前端。另 `_legacy_index` 用 `open(...).read()` 不关文件句柄(CPython 立即回收,无实际泄漏)。
- web/ 的 npm 构建在现代环境跑不起来:web/package-lock.json 锁 webpack 5.6.0(:6253)、sass 1.29.0、babel-loader 8、css-loader 5、eslint 7(均为 2020 年依赖);webpack 5.6 用 md4 哈希,在 OpenSSL3(Node ≥17)抛 ERR_OSSL_EVP_UNSUPPORTED——本机 Node 22 实测 `crypto.createHash('md4')` 即抛该错误。只有 Dockerfile.local 钉死 Node 14 才能通过。
- 结论(Info):旧 UI 入口 `/legacy` 与 `/playlist`、`/upload`、`/download` 在默认部署里基本是死代码(/legacy 404);`/post`、`/library*` 仍是新 UI 的活依赖。建议:在 PROGRESS/README 标注;迁移 LibraryPage 到 /api 后再整体删除 web/ 与 .drone.yml 中的旧构建步骤。

## 覆盖声明
读过:webui/src 全部文件(含全部模板)、vite.config.ts、package.json、dist/index.html、web/package.json(+lock 关键版本)、Dockerfile/Dockerfile.local 相关段、interface.py 静态/legacy/library 路由、web_upload.py 全文、web_api.py status/queue/search 段、web_search.py 概览。
未读/未做:未起浏览器实测交互;未审 web/js 旧前端源码(只确认其用途与构建可行性);web_users.py 的歌单/导入端点未做深审(属后端组);style.css 与 tailwind 样式未审(无安全面)。
