# record_6.md — 第 6 轮 (20 轮批次: 隧道协议 http/tcp/udp + WebSocket 握手桥接)

- 时间: 2026-11-03T09:51:57.626Z (本地 2026/11/3 17:51:57)
- 场景: 单 worker uvicorn:8100 + sqlite(selftest.db); 内网 echo(TCP/UDP/WS)在测试进程内; 真实客户端代码建 lan 侧
- 期望: tools/selftest.py 全阶段(00,01,A,B,C,D,E) ALL PASS; tools/api_check.py API CHECK ALL PASS
- 实际: selftest exit=0; 32 passed, 0 failed; 输出摘要: ALL PASS
        api_check exit=0; API CHECK ALL PASS
- 结果: PASS
- 备注: 无异常
