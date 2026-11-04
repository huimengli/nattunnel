# 2026-11-04-20-17-27 批次(验证 3 轮) — 管理页“编辑隧道”保存走 PATCH

- 场景: 台架(`tools/admin_ui_logic_test.js`) 从 index.html 抽取 `openTunnelModal`/`saveTunnel` 真实函数体,
  注入桩 DOM/state/api/toast; `api` 桩按 MySQL CI 语义对已存在短链回 409; 基线钉在修复前 commit `d3ec4ae`。
- 期望: 工作区 A=PATCH 原大小写 ID + header_timeout_s 300; B=POST 保留小写 ID; C=改 ID 零请求; D=700 夹 600; E=9 位拒;
  基线必须复现 `POST` + 大写 ID + 409 toast(证明根因)。
- 结果: 3/3 轮全 PASS(每轮 6 项); 明细 record_1..3.md。
- 关键结论:
  1. 编辑/新建判定必须用弹窗打开时记录的 `editingTid`, 不能用“大写化后的输入查表” — 生成短链是大小写混合的;
  2. 改动前副产危害: 新建手填混合大小写 ID 被静默大写(`abHjkmn2` → `ABHJKMN2`);
     编辑态把 ID 改成别的值会**新建一条隧道**(`OTHERID1`), 修复后直接拒绝且零请求;
  3. 响应超时的 1..600 夹取(700 → 600)与 9 位 ID 拒绝在新旧版本都正确(与本次修复无关)。
- 无修复项: 三轮一次通过。
