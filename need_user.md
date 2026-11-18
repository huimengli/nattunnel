# need_user.md — 需用户确认的阻塞问题

<!-- 已归档于 ai_buckup/need_user.md_2026-11-03-18-58-00.md (Q1 已解决) -->

## Q2 2026-11-18 13:03 — Unity 入口形态: 新增 `proto=unity` 还是 `udp` 隧道 + 公网 UDP 端口?
- 用户原话: “现在项目的 UDP 不是标准的 UDP 端口映射, 我需要给 UNITY 用, 你看看有没解决办法? 或者新增一个 unity 选项?”
- status: done (Agent 已定案并实现, 20 轮全 PASS; 无需用户决策即可继续, 记录供确认)
- 定案: **不新增 `proto=unity`** —— Unity(`UnityTransport`) 用的就是标准 UDP, 与 `udp` 语义完全一致; 新增取值 = 同一语义两个名字(与 2026-11-03 的 http/tcp/udp 语义表冲突)。Unity 入口 = `udp` 隧道 + `udp_port`(公网 UDP 端口, 全服务器唯一) + `udp_allow`(源 IP 白名单) + 管理页 Unity 填法提示(`docs/unity-ngo.md`)。
- 需用户线下确认(不阻塞): ① 服务器放行**入站 UDP**(台架是 127.0.0.1 回环, nginx 不参与); ② Unity 真机 `UnityTransport` 填 `Server Address=公网IP` + `Server Port=udp_port` 连一次; ③ 重编译 exe(客户端首包竞态修复)。

(当前无待确认问题)
