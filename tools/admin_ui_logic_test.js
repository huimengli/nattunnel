#!/usr/bin/env node
/* 台架: 从 server/app/static/index.html 抽取 openTunnelModal / saveTunnel 真实函数体,
 * 注入桩 DOM/state/api/toast, 复现并验证"编辑隧道保存"走 PATCH 还是 POST。
 * 用法: node tools/admin_ui_logic_test.js
 * 对应需求: records/2026-11-04-20-17-27.md(管理页编辑隧道保存失败) */
const { execSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const Q = String.fromCharCode(34);  /* 双引号: 匹配 JSON.stringify 输出时避免转义地狱 */

const REL = "server/app/static/index.html";
const FIELD_IDS = ["tm-title", "tm-name", "tm-tid", "tm-proto", "tm-port", "tm-host",
  "tm-bw", "tm-htimeout", "tm-enabled", "tm-hint", "proto-hint"];

function extract(html) {
  const a = html.indexOf("function openTunnelModal");
  const b = html.indexOf("async function deleteTunnel");
  if (a < 0 || b < 0) throw new Error("anchor not found in index.html");
  return html.slice(a, b);
}

function harness(html) {
  const els = {};
  for (const id of FIELD_IDS) els[id] = { value: "", checked: true, textContent: "", readOnly: false };
  const document = { getElementById: (id) => els[id] };
  const state = { tunnels: [{ tunnel_id: "smGvs9xQ", name: "llm", proto: "http", local_port: 8080,
    local_target_host: "127.0.0.1", bandwidth_kbps: 0, header_timeout_s: 30, enabled: true }] };
  const calls = [], toasts = [];
  const api = async (p, opts) => {
    const method = (opts && opts.method) || "GET";
    const body = (opts && opts.body) || null;
    calls.push(method + " " + p + (body ? " " + JSON.stringify(body) : ""));
    // MySQL 默认 Collation: 大小写不敏感 -> 创建时短链撞车回 409
    if (method === "POST" && body && body.tunnel_id &&
        state.tunnels.some((t) => t.tunnel_id.toLowerCase() === String(body.tunnel_id).toLowerCase()))
      throw new Error("tunnel id already exists");
    return {};
  };
  const f = new Function("document", "state", "api", "toast", "showModal", "hideModal", "refreshAll",
    extract(html) + "\nreturn { openTunnelModal, saveTunnel };");
  const ui = f(document, state, api, (m, k) => toasts.push(k + ": " + m), () => {}, () => {}, () => {});
  return { ui, calls, toasts, set: (id, v) => { els[id].value = String(v); } };
}

function fill(s, tid, port, htime) {
  s.set("tm-name", "llm"); s.set("tm-tid", tid); s.set("tm-proto", "http");
  s.set("tm-port", port); s.set("tm-host", "127.0.0.1"); s.set("tm-bw", "0");
  s.set("tm-htimeout", htime);
}

async function scenarios(html) {
  const out = [];
  // A: 编辑已有隧道 smGvs9xQ, 响应超时改 300(用户截图的操作)
  let s = harness(html);
  s.ui.openTunnelModal("smGvs9xQ");
  s.set("tm-htimeout", "300");
  await s.ui.saveTunnel();
  out.push({ name: "A 编辑 smGvs9xQ(响应超时 300)", calls: s.calls, toasts: s.toasts });

  // B: 新建隧道手填混合大小写短链 abHjkmn2(应原样保留)
  s = harness(html);
  s.ui.openTunnelModal(null);
  s.set("tm-name", "新隧道"); s.set("tm-tid", "abHjkmn2"); s.set("tm-proto", "http");
  s.set("tm-port", "9000"); s.set("tm-host", "127.0.0.1"); s.set("tm-bw", "0"); s.set("tm-htimeout", "30");
  await s.ui.saveTunnel();
  out.push({ name: "B 新建手填 abHjkmn2", calls: s.calls, toasts: s.toasts });

  // C: 编辑态把 ID 改成 OTHERID1(应拒绝, 零请求)
  s = harness(html);
  s.ui.openTunnelModal("smGvs9xQ");
  s.set("tm-tid", "OTHERID1"); s.set("tm-htimeout", "300");
  await s.ui.saveTunnel();
  out.push({ name: "C 编辑态改 ID", calls: s.calls, toasts: s.toasts });

  // D: 响应超时 700 越界(应夹到 600 后 PATCH)
  s = harness(html);
  s.ui.openTunnelModal("smGvs9xQ");
  s.set("tm-htimeout", "700");
  await s.ui.saveTunnel();
  out.push({ name: "D 响应超时 700", calls: s.calls, toasts: s.toasts });

  // E: 9 位短链应拒
  s = harness(html);
  s.ui.openTunnelModal(null);
  s.set("tm-name", "x"); s.set("tm-tid", "abHjkmn2L"); s.set("tm-proto", "http");
  s.set("tm-port", "9000"); s.set("tm-host", "127.0.0.1"); s.set("tm-bw", "0"); s.set("tm-htimeout", "30");
  await s.ui.saveTunnel();
  out.push({ name: "E 9 位 ID", calls: s.calls, toasts: s.toasts });
  return out;
}

async function main() {
  const root = path.resolve(__dirname, "..");
  const fixed = fs.readFileSync(path.join(root, REL), "utf8");
  /* 基线钉死在修复前的 commit(否则提交后 HEAD 已含修复, 复现项会失效) */
  const BASE = "d3ec4ae";
  const head = execSync("git show " + BASE + ":" + REL, { cwd: root, encoding: "utf8" });
  const resFixed = await scenarios(fixed);
  const resHead = await scenarios(head);
  console.log("== 工作区(修复后) ==");
  for (const r of resFixed) console.log("  " + r.name + ": calls=" + JSON.stringify(r.calls) + " toast=" + JSON.stringify(r.toasts));
  console.log("== HEAD(改动前) ==");
  for (const r of resHead) console.log("  " + r.name + ": calls=" + JSON.stringify(r.calls) + " toast=" + JSON.stringify(r.toasts));

  const has = (arr, key, sub) => arr.length === 1 && arr[0].startsWith(key) && arr[0].includes(sub);
  const checks = [];
  const A = resFixed[0];
  checks.push(["A PATCH 原大小写 ID + header_timeout_s 300 + toast 隧道已更新",
    has(A.calls, "PATCH api/tunnels/smGvs9xQ", Q + "header_timeout_s" + Q + ":300") && A.toasts.join("|").includes("隧道已更新")]);
  const Bt = resFixed[1];
  checks.push(["B POST 保留小写 ID abHjkmn2",
    has(Bt.calls, "POST api/tunnels", Q + "tunnel_id" + Q + ":" + Q + "abHjkmn2" + Q) && Bt.toasts.join("|").includes("隧道已创建")]);
  const C = resFixed[2];
  checks.push(["C 编辑态改 ID 被拒(零请求)", C.calls.length === 0 && C.toasts.join("|").includes("隧道 ID 不可修改")]);
  const D = resFixed[3];
  checks.push(["D 700 夹到 600 后 PATCH", has(D.calls, "PATCH api/tunnels/smGvs9xQ", Q + "header_timeout_s" + Q + ":600")]);
  const E = resFixed[4];
  checks.push(["E 9 位 ID 被拒(零请求)", E.calls.length === 0 && E.toasts.join("|").includes("8 位")]);
  const Ah = resHead[0];
  checks.push(["HEAD 复现: POST 大写 ID -> toast tunnel id already exists",
    has(Ah.calls, "POST api/tunnels", Q + "tunnel_id" + Q + ":" + Q + "SMGVS9XQ" + Q) && Ah.toasts.join("|").includes("tunnel id already exists")]);
  for (const [n, ok] of checks) console.log((ok ? "  [PASS] " : "  [FAIL] ") + n);
  const fails = checks.filter((c) => !c[1]).length;
  console.log("台架: PASS " + (checks.length - fails) + " / FAIL " + fails);
  process.exit(fails ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(2); });
