# tests/opinion.md — 测试结论累积

## 2026-11-03-13-21-03 批次: 隧道协议 http/tcp/udp + WebSocket 握手桥接 — 20 轮全 PASS

- 场景: 单 worker uvicorn + sqlite; 内网 TCP/UDP/WS echo 在测试进程内; 真实客户端代码建 lan 侧。
- 结果: 20/20 轮 PASS(每轮 selftest 32 项 + api_check 18 项); 明细见 tests/2026-11-03-13-21-03_cc/
- 关键结论:
  1. `tcp` 隧道可承载 WebSocket 握手(101)与数据帧/文本帧双向透明, 子协议协商结果原样回带;
  2. `http` 隧道 = 混合(浏览器直开 + WS 桥接 + 子协议裸流共存于同一入口/同一本地端口);
  3. 纯 HTTP 直转语义只属 `http` 隧道, `tcp`/`udp` 隧道对纯 HTTP 请求回 400;
  4. `udp` 隧道 WS 握手被拒(400), 声明子协议后仍走帧协议数据报;
  5. 存量 `proto=tcp` 在首启一次性迁移为 `http`(AppSetting 标记), 之后新建 `tcp` 按字节流语义工作。
- 修复: relay.get_redis 丢失; secrets.randbytes 缺失(改 token_bytes); selftest 期望值(E2/E8/E10 路径)。

## 2026-11-03-13-21-03 追加(exe 端到端, 第 21-26 轮)

- 新 exe(2026-11-03 19:56 重编译) X1-X7 全 PASS: `tcp` 字节流 + WS 桥接; `http` 混合(纯 HTTP 直转 + 热更新后 WS 桥接)。
- 旧 exe(2026-11-03 13:03 构建) X8-X10 证实不兼容: `http` 隧道落到 UDP 分支 → 公网 WS 桥接 504。
- 因此 exe 必须重新编译; 21-23 轮 FAIL 源于工具默认 exe 路径(已修), 24-26 轮全 PASS。
