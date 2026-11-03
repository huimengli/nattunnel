# 测试批次结论 — tests/2026-11-03-13-21-03_cc/

- 批次时间: 2026-11-03 17:36 – 18:54(本地 Asia/Shanghai), 20 轮(明确测试, 每轮 selftest + api_check 全新进程) + 开发期 3 轮验证
- 需求: 隧道协议由 tcp/udp 改为 http/tcp/udp; 原 tcp(浏览器 HTTP 直开)改名 http; 新 tcp 为原生 TCP 字节流且必须支持 WebSocket 握手(101 + 数据帧)到达内网 WS 服务 (records/2026-11-03-13-21-03.md)
- 环境: Windows + Python 3.12.0(仓库 .venv) + 单 worker uvicorn 0.54 / FastAPI 0.162 / websockets 17.4 / sqlite(selftest.db), 无 Redis(降级内存)
- 期望: 每轮 selftest 全阶段(00,01,A,B,C,D,E) ALL PASS + api_check API CHECK ALL PASS
- 实际: 20 轮全部 PASS(每轮 selftest 32 项全过, api_check 18 项全过)
- 结论: 新语义成立

## 关键断言覆盖(每轮)

- A 阶段: 子协议 nattunnel-relay 字节流中继 + T_CONFIG 热更新(改 local_port 后转发到新端口);
- B 阶段: UDP 数据报双向 echo;
- C 阶段: 绑定令牌定位隧道(无隧道 ID 的令牌);
- D 阶段: http 隧道纯 HTTP 逐请求直转(method/path/query/body 透传) + HTML `<base>` 注入与 Content-Length 同步 +
  **tcp 隧道纯 HTTP → 400(直转语义只属 http)** + LAN 离线 503 + exe 双下载通道;
- E 阶段(WS 握手桥接): 无子协议握手 → 101(子协议 None); 数据帧/文本帧双向 echo;
  内网要求子协议时协商结果 `chat` 原样回带(热改 local_port 到第二台 WS 服务);
  http 隧道同样桥接; 同一 http 隧道的纯 HTTP 直转仍可达(WS 服务对非 WS 请求回 400/426);
  udp 隧道 WS 握手被拒(400); udp 隧道声明子协议仍走帧协议裸流(HELLO);
- api_check: 协议取值 http/tcp/udp 合法、`proto=ws` → 422、`PATCH proto http→tcp` 生效。

## 开发期暴露并修复的缺陷(3 轮验证阶段)

1. **relay.py 重写时丢失 `get_redis()`** → main.py ImportError 启动失败; 已恢复(惰性 Redis 客户端)。
2. **`secrets.randbytes` 在本机解释器缺失**(Python 3.12 定制安装无该符号) → WS 掩码生成抛
   AttributeError, E3 连接被切; 改用 `secrets.token_bytes(4)`。
3. **selftest 期望值错位**: E2 的内网 WS 服务要求子协议时 websockets 对"无子协议握手"回
   400 missing subprotocol(桥接行为本身正确); 拆为 A(不要求子协议)/B(要求 chat) 两台内网服务;
   E8 期望 400/426(WS 服务对非 WS 请求的合法应答); E10 裸流必须连裸路径 `/tunnel/<ID>`
   (带 `/ws` 子路径不匹配 FastAPI 路由, uvicorn 以 403 拒绝握手)。

## 观察与后续

- 桥接拒绝(udp WS → 400)时 uvicorn 打印 `ASGI callable returned without completing handshake`:
  这是用 `websocket.http.response` 预拒绝的正常表征(客户端得到 HTTP 响应), 不影响功能。
- 浏览器 `new WebSocket()` 无法自定义头 → 公网侧"无 nattunnel-relay 子协议的握手"= 桥接;
  裸流工具必须显式声明子协议(README 与工具已写明)。
- 桥接层剥 `Sec-WebSocket-Extensions`(不做 permessage-deflate), `Sec-WebSocket-Key` 原样转发
  => uvicorn 计算的 `Sec-WebSocket-Accept` 与内网服务一致, 浏览器校验通过。
- 方案A(用户 2026-11-03 拍板): 不新增 mixed 协议; `http` 即混合(HTTP 直开 + WS 桥接 + 子协议裸流);
  `tcp+udp` 同端口双栈混合未实现(需改客户端流模型与中继), 列为后续需求。
