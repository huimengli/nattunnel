# 第 4 轮 测试记录(Unity UDP 原生入口 20 轮回归)

- 时间: 2026-11-18 17:22:57
- 场景: 服务器全新启动 -> selftest 全阶段 A-G + api_check 巡检 -> 建 udp 隧道配公网 UDP 端口 -> LAN 离线数据报丢弃 -> 服务器重启(启动时自动绑定) -> 删除隧道解绑 -> 管理页静态检查
- 期望: 台架 9 项全过, 且 selftest / api_check 退出码 0(全 PASS)
- 实际: 台架 9/9 项通过
- 结果: 通过
- 备注: 服务器日志在 _tmp_rounds/(gitignore); 自测库 server/selftest.db(gitignore)

## 逐项

| 检查 | 结果 | 备注 |
| --- | --- | --- |
| R0 服务器全新启动 + /api/health | PASS | C:\Users\楼听\Desktop\git\nattunnel\_tmp_rounds\r4_server_a.log |
| S1 selftest 全阶段 A-G: 53 项通过 | PASS | rc=0 FAIL=[] |
| S2 api_check API 巡检: 26 项通过 | PASS | rc=0 FAIL=[] |
| P1 建 udp 隧道并绑定公网 UDP 端口 | PASS | tid=J7nU86SZ port=26204 eps=[{'tunnel_id': 'J7nU86SZ', 'port': 26204, 'peers': 0, 'target': '127.0.0.1:9400', 'allow': '127.0.0.1', 'public': '127.0.0.1:26204'}] |
| P3 LAN 离线时对入口发数据报: 丢弃且端点存活 | PASS | reply=None |
| P2 重启后启动时自动绑定 UDP 入口 | PASS | health=True eps=[{'tunnel_id': 'J7nU86SZ', 'port': 26204, 'peers': 0, 'target': '127.0.0.1:9400', 'allow': '127.0.0.1', 'public': '127.0.0.1:26204'}] C:\Users\楼听\Desktop\git\nattunnel\_tmp_rounds\r4_server_b.log |
| P2b 重启后入口仍在线(内网离线 -> 无回包, 端点存活) | PASS | reply=None |
| P4 删除隧道后 UDP 端点解绑 | PASS | [] |
| P5 管理页含 UDP 字段与 Unity 指引 | PASS | 缺失=[] |
