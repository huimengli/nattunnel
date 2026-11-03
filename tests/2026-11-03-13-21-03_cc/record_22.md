# record_22.md — 第 22 轮 (exe 端到端验收: 真实构建产物 + 新旧 exe 对比)

- 时间: 2026-11-03T12:11:28.134Z (本地 2026/11/3 20:11:28)
- 场景: 新 exe(client/dist/nattunnel-client.exe, 2026-11-03 19:56 构建, PyInstaller 6.1.0, 含 cryptography 50.0.2 / websockets 17.4) 用 -s 覆盖到本地 sqlite 服务器; 内网 WS echo + HTTP 服务在测试进程内; 旧 exe 从 git HEAD~5 提取(2026-11-03 13:03 构建)做对比; 子进程 TEMP 指向仓库内目录(系统 TEMP 在本沙箱不可写, PyInstaller --onefile 解包需要)
- 期望: X1-X7 新 exe 全 PASS(tcp 字节流 + WS 桥接 echo; http 混合: 纯 HTTP 直转 + 热更新后 WS 桥接); X8-X10 旧 exe 对 http 隧道控制面可上线但数据面不兼容(WS 桥接 504, 日志走 udp 分支)
- 实际: exit=1; 旧 exe 提取 11531717 B; 断言: (无断言行)
        服务器桥接日志: (无) (504 行即旧 exe 数据面不兼容的表现)
- 结果: FAIL
- 备注: STDERR:   File "C:\Users\¥��\Desktop\git\nattunnel\tools\exe_e2e_test.py", line 143, in main /     print("exe:", EXE, os.path.getsize(EXE), "bytes") /                        ^^^^^^^^^^^^^^^^^^^^ /   File "<frozen genericpath>", line 62, in getsize / FileNotFoundError: [WinError 3] ϵͳ�Ҳ���ָ����·����: 'C:\\Users\\¥��\\Desktop\\git\\nattunnel\\tools\\client\\dist\\nattunnel-client.exe' / 
