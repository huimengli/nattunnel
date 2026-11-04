# 轮 3

- 时间: 2026-11-04T11:48:00.810Z
- 场景: 隧道配置项 header_timeout_s(默认 30 / 上限 600) + 全量回归(阶段 A-E)
- 期望: header 9 项全 PASS; selftest 32 项全 PASS; 窗口小于内网响应延迟时 504 且 detail 为 本地服务响应超时
- 实际:
```
  [PASS] F1 ������Ĭ�� header_timeout_s=30
  [PASS] F2 ָ�� header_timeout_s=45 ��Ⲣ����
  [PASS] F3 PATCH header_timeout_s=600 ����
  [PASS] F4 header_timeout_s=601 Խ�类��(422)
  [PASS] F5 lan ������(����������Ӧ��ʱ)
  [PASS] F6 ����45s: 40s ����Ӧ�� 200
  [PASS] F7 ����10s: 40s ����Ӧ 504 �� detail Ϊ���ط�����Ӧ��ʱ
  [PASS] F8 �Ļ�45��ͬһ���������ָ� 200(�����������, ��������)
  [PASS] F9 GET ���������� header_timeout_s=45

�׶� F: PASS 9 / FAIL 0

selftest vs http://127.0.0.1:8100
  [PASS] 00 ����������
  [PASS] 01 RSA ����ȡ JWT

== �׶� A: TCP(ԭ���ֽ���) �������� + �����ȸ��� ==
  [PASS] A1 �ͻ��� lan ������
  [PASS] A2 �������յ� HELLO(peer_id)
  [PASS] A3 TCP �������� (echo)
  [PASS] A4 API �Ѱ� local_port ��Ϊ 5522
  [PASS] A5 �ȸ���ת�����˿� (echo)

== �׶� B: UDP �������� ==
  [PASS] B1 �ͻ��� lan ������
  [PASS] B2 UDP ���ݱ����� (echo)

== �׶� C: ���ư����� ==
  [PASS] C1 ǩ������������
  [PASS] C2 /me ���ذ������� ID
  [PASS] C3 �ͻ���ƾ���ƶ�λ��ȷ���� (�ޱ�������)
  [PASS] C4 TCP ���� (�����ƻỰ)

== �׶� D: �� HTTP ֱת(�����������) ==
  [PASS] D1 �ͻ��� lan ������ (HTTP ����)
  [PASS] D2 GET ֱת: method/path/query ͸��
  [PASS] D3 POST ֱת: ������͸�� (bodylen=16)
  [PASS] D4 �� Connection: upgrade ͷ�� GET ������ֱת (nginx ����)
  [PASS] D6 HTML ֱתע�� <base> �� Content-Length ͬ��
  [PASS] D8 tcp ������ HTTP ���� -> 400(ֱת�� http ����)
  [PASS] D5 LAN ����ʱ���� 503
  [PASS] D7 �ͻ������ؽӿ� (200 + exe ��С)
  [PASS] D7b �ͻ��˾�ֱ̬�� (200 + ͬ��С)

== �׶� E: WebSocket �����Ž�(�������������� WS ����) ==
  [PASS] E1 tcp ���� lan ������
  [PASS] E2 tcp ���� WS ���ֵ������� WS ����(101, ����Э��)
  [PASS] E3 tcp ���� WS ����֡���� (echo)
  [PASS] E4 WS ��Э��Э�̻ش� (chat)
  [PASS] E5 WS �ı�֡���� (echo)
  [PASS] E6 http ���� lan ������
  [PASS] E7 http ���� WS �����Ž� (echo)
  [PASS] E8 http ������ HTTP ֱת�Կɴ�����(WS ����Է� WS ����� 400/426)
  [PASS] E9 udp ���� WS ���ֱ���(�� 101)
  [PASS] E10 udp ������Э�����������м�(HELLO)

���: 32 passed, 0 failed
ALL PASS

```
- 结果: PASS
- 备注: 504 后 bridge 的 finally 向 lan 发 T_CLOSE, 内网慢服务写响应时 ConnectionAborted(WinError 10053) 属预期(本地请求被掐)
