# 轮 2

- 时间: 2026-11-04T12:31:52.668Z
- 场景: 台架抽取 index.html 的 openTunnelModal/saveTunnel 真实函数体, 桩 DOM/state/api/toast; api 桩按 MySQL CI 语义对已存在短链回 409; 基线 = HEAD(改动前 d3ec4ae), 对照 = 工作区(修复后)
- 期望: 工作区 A=PATCH 原大小写 ID + header_timeout_s 300; B=POST 保留小写 ID; C=改 ID 零请求; D=700 夹 600; E=9 位拒; HEAD 复现 POST 大写 ID + 409 toast
- 实际:
```
== 工作区(修复后) ==
  A 编辑 smGvs9xQ(响应超时 300): calls=["PATCH api/tunnels/smGvs9xQ {\"name\":\"llm\",\"proto\":\"http\",\"local_port\":8080,\"local_target_host\":\"127.0.0.1\",\"bandwidth_kbps\":0,\"header_timeout_s\":300,\"enabled\":true}"] toast=["ok: 隧道已更新"]
  B 新建手填 abHjkmn2: calls=["POST api/tunnels {\"name\":\"新隧道\",\"proto\":\"http\",\"local_port\":9000,\"local_target_host\":\"127.0.0.1\",\"bandwidth_kbps\":0,\"header_timeout_s\":30,\"tunnel_id\":\"abHjkmn2\"}"] toast=["ok: 隧道已创建"]
  C 编辑态改 ID: calls=[] toast=["err: 隧道 ID 不可修改(改 ID 请新建隧道)"]
  D 响应超时 700: calls=["PATCH api/tunnels/smGvs9xQ {\"name\":\"llm\",\"proto\":\"http\",\"local_port\":8080,\"local_target_host\":\"127.0.0.1\",\"bandwidth_kbps\":0,\"header_timeout_s\":600,\"enabled\":true}"] toast=["ok: 隧道已更新"]
  E 9 位 ID: calls=[] toast=["err: 短链 ID 需为 8 位字母数字(不含 0O1lI)"]
== HEAD(改动前) ==
  A 编辑 smGvs9xQ(响应超时 300): calls=["POST api/tunnels {\"name\":\"llm\",\"proto\":\"http\",\"local_port\":8080,\"local_target_host\":\"127.0.0.1\",\"bandwidth_kbps\":0,\"header_timeout_s\":300,\"tunnel_id\":\"SMGVS9XQ\"}"] toast=["err: tunnel id already exists"]
  B 新建手填 abHjkmn2: calls=["POST api/tunnels {\"name\":\"新隧道\",\"proto\":\"http\",\"local_port\":9000,\"local_target_host\":\"127.0.0.1\",\"bandwidth_kbps\":0,\"header_timeout_s\":30,\"tunnel_id\":\"ABHJKMN2\"}"] toast=["ok: 隧道已创建"]
  C 编辑态改 ID: calls=["POST api/tunnels {\"name\":\"llm\",\"proto\":\"http\",\"local_port\":8080,\"local_target_host\":\"127.0.0.1\",\"bandwidth_kbps\":0,\"header_timeout_s\":300,\"tunnel_id\":\"OTHERID1\"}"] toast=["ok: 隧道已创建"]
  D 响应超时 700: calls=["POST api/tunnels {\"name\":\"llm\",\"proto\":\"http\",\"local_port\":8080,\"local_target_host\":\"127.0.0.1\",\"bandwidth_kbps\":0,\"header_timeout_s\":600,\"tunnel_id\":\"SMGVS9XQ\"}"] toast=["err: tunnel id already exists"]
  E 9 位 ID: calls=[] toast=["err: 短链 ID 需为 8 位字母数字(不含 0O1lI)"]
  [PASS] A PATCH 原大小写 ID + header_timeout_s 300 + toast 隧道已更新
  [PASS] B POST 保留小写 ID abHjkmn2
  [PASS] C 编辑态改 ID 被拒(零请求)
  [PASS] D 700 夹到 600 后 PATCH
  [PASS] E 9 位 ID 被拒(零请求)
  [PASS] HEAD 复现: POST 大写 ID -> toast tunnel id already exists
台架: PASS 6 / FAIL 0
```
- 结果: PASS
- 备注: 台架只验证前端请求构造(方法/URL/ID 大小写/夹取); 服务端 409 由桩模拟(真实语义见 routers/tunnels.py:98)
