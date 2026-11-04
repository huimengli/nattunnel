# 2026-11-04-19-06-38 批次(验证 3 轮) — 隧道配置项 header_timeout_s

- 场景: 单 worker uvicorn(127.0.0.1:8100) + sqlite(selftest_ht.db); 内网"慢服务"在吐响应头前 sleep 40 秒;
  真实客户端代码(进程内)建 lan 侧; 每轮跑 tools/header_timeout_test.py(阶段 F, 9 项) + tools/selftest.py(阶段 A-E, 32 项)。
- 期望: 默认 30 / 显式 45 / 上限 600 接受 / 601 → 422; 窗口 > 内网延迟 → 200; 窗口 < 延迟 → 504 且 detail "本地服务响应超时";
  改窗口后无需重启, 下一个请求生效。
- 结果: 3/3 轮全 PASS(每轮 header 9 项 + selftest 32 项); 明细 record_1..3.md。
- 关键结论:
  1. `header_timeout_s` 逐请求从库读取(http_bridge 在隧道查找处取值), 管理页 PATCH 后下一个请求立即生效, 不需重连/重启;
  2. 窗口起算点在**请求体全部入隧之后** → 大 body(46 图)的上传耗时不占用该窗口, 窗口只覆盖"本地服务吐响应头";
  3. 超时后 bridge 的 finally 向 lan 发 T_CLOSE, 内网请求被掐: 慢服务随后写响应会 ConnectionAborted(WinError 10053), 属预期(测试轮备注);
  4. WS 握手等 101 的窗口固定 20 秒(_WS_HANDSHAKE_TIMEOUT), 不随隧道配置放大;
  5. 存量库补列走 seed.py 幂等 ALTER(init_db 连跑两次无异常, duplicate 被吞)。
- 无修复项: 三轮一次通过。
