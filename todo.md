# todo.md — nattunnel 待办清单

- [x] 2026-11-03-13-21-03 隧道协议改造: tcp/udp → http/tcp/udp, tcp 支持 WebSocket 握手 (→ records/2026-11-03-13-21-03.md)
  - [x] 服务端: relay/main 角色判定与 data_types; http_bridge WS 握手桥接(自写 WSCodec, 未引入 wsproto)
  - [x] 服务端: schemas/models proto 取值 + seed 一次性 tcp→http 迁移 + 演示隧道
  - [x] 客户端: http/tcp 均字节流, 协议热更新判定
  - [x] 管理页: 协议下拉三项 + 语义说明(弹窗提示 + 隧道卡片说明)
  - [x] 工具/selftest/api_check: nattunnel-relay 子协议 + 新增 WS 握手桥接阶段(自测 E1-E10, D8, 协议取值)
  - [x] 实测(20 轮): 本地 http/tcp/udp + WS 握手桥接全链路 → tests/2026-11-03-13-21-03_cc/(20 轮全 PASS)
  - [x] 文档同步: README / tree.md / progress.md / need_user(方案A 归档)
- [x] 2026-11-03-19:33 重编译客户端 exe(客户端 http/tcp 字节流判定 + 文档表述变更; 同步 server/app/static 副本) — 旧 exe 对迁移后的 `http` 隧道会走 UDP 分支, 必须换新构建
- [ ] 后续需求(方案A 未含): `tcp+udp` 同端口双栈混合(客户端双 socket + 中继 T_DATA/U_DATA 分流) → 需新 record 再开工
- [ ] 部署到 www.keliit.top(子路径), 部署后改管理员密码(步骤 docs/deploy-keliit-top.md; 需服务器侧执行)
