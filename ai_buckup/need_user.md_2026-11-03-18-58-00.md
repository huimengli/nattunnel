# need_user.md — 需用户确认的阻塞问题

## Q1 混合隧道语义(2026-11-03, 关联 records/2026-11-03-13-21-03.md)

status: done

用户问: "能不能支持混合隧道? 比如说 http+tcp 混合的隧道?"

现状(本次改造后的实现):
- 隧道 = 一个前端端口(单一 local_port + local_target_host), 公网入口 /tunnel/<短链> 按连接类型分流;
- proto=http 的隧道实际上已经是混合隧道: 纯 HTTP 直转(浏览器开短链) + WS 握手桥接(页面里的 WebSocket)
  + 子协议 nattunnel-relay 的帧协议裸流(等价 tcp 访问);
- proto=tcp: WS 握手桥接 + 子协议裸流; 纯 HTTP 直转请求拒绝(400);
- proto=udp: 仅帧协议数据报(WS 握手拒绝)。

需要用户拍板的三点:
1. 是否把混合做成显式协议(如 mixed / http+tcp), 还是把 http 的混合语义写进文档即可;
2. 是否允许 tcp 隧道也接受纯 HTTP 直转(即 tcp 也混合);
3. 是否需要 tcp+udp 同端口双栈混合(客户端对同一端口同时开 TCP 与 UDP 通道, 帧类型 T_DATA/U_DATA 分流)
   —— 该模式需要改客户端与中继, 工作量明显更大; http+udp 混合无意义。

答复后按结论实施并更新 records/2026-11-03-13-21-03.md。

答复(2026-11-03, 用户): "按照方案A走吧" —— 不新增 mixed 协议; `http` 隧道即混合隧道(浏览器 HTTP 直开 +
WS 握手桥接 + 子协议 nattunnel-relay 裸流), `tcp` 为字节流(WS 握手 + 裸流, 拒绝纯 HTTP), `udp` 数据报;
语义写入 README 与管理页说明。`tcp+udp` 同端口双栈混合列为后续需求(todo.md)。