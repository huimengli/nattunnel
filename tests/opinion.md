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
## 2026-11-04-19-06-38 批次(验证 3 轮): 隧道配置项 header_timeout_s(默认 30, 上限 600) — 3 轮全 PASS

- 场景: sqlite + 内网慢服务(响应头延迟 40 秒) + 进程内真实客户端; 每轮 header_timeout_test(9 项) + selftest(32 项)。
- 结果: 3/3 轮 PASS → tests/2026-11-04-19-06-38_cc/(record_1..3 + opinion.md)
- 关键结论: 窗口逐请求读库(改后下一个请求生效, 无需重启); 窗口起算点在请求体入隧之后(大 body 上传耗时不占窗口); 超时后 T_CLOSE 掐本地请求(慢服务随后写响应 ConnectionAborted 属预期); WS 握手窗口固定 20 秒不随配置放大; 存量库幂等 ALTER(init_db x2 无异常)。
## 2026-11-04-20-17-27 批次(验证 3 轮): 管理页“编辑隧道”保存走 PATCH(大小写短链) — 3 轮全 PASS

- 场景: Node 台架抽取 index.html 的 openTunnelModal/saveTunnel 真实函数体 + 桩 DOM/api(api 桩按 MySQL CI 语义对已存在短链回 409); 基线钉在修复前 commit d3ec4ae。
- 结果: 3/3 轮 PASS(每轮 6 项) → tests/2026-11-04-20-17-27_cc/(record_1..3 + opinion.md)
- 关键结论: 编辑/新建判定须用弹窗打开时记录的 editingTid(生成短链大小写混合, 大写化后查不到 → 走 POST → 409 tunnel id already exists);
  改动前还会静默大写手填 ID(abHjkmn2 → ABHJKMN2), 编辑态改 ID 会新建隧道; 修复后: PATCH 原 ID, 改 ID 被拒(零请求), 700 夹到 600, 9 位 ID 拒。
