# GCPMX GFW 探针服务

探针运行在大陆、香港或海外 VPS 上，面板只调用 `POST /v1/check` 测试目标公网 IP 的 TCP 端口。它不会代理网页请求，也不会接受 URL，因此不能被当作开放代理使用。

## 部署

每个位置部署一台 VPS。建议至少配置：

- 1 个大陆探针（`mainland`）
- 1 个香港或海外探针（`hk` / `overseas`）

在探针目录执行：

```bash
PROBE_LOCATION=mainland PROBE_PORT=9090 bash install.sh
```

香港和海外节点分别使用对应位置：

```bash
PROBE_LOCATION=hk bash install.sh
PROBE_LOCATION=overseas bash install.sh
```

安装脚本会随机生成令牌并保存到 `/etc/gcpmx-probe/probe.env`。查看令牌：

```bash
grep '^PROBE_TOKEN=' /etc/gcpmx-probe/probe.env
```

生产环境应通过 Nginx、Caddy 或云负载均衡提供 HTTPS，再把面板中的 URL 填成 `https://探针域名`。如果暂时使用直连 HTTP，请只在安全网络中使用，并在防火墙中限制来源。

测试接口：

```bash
curl http://127.0.0.1:9090/health
curl -H 'X-Probe-Token: 这里替换为令牌' \
  -H 'Content-Type: application/json' \
  -d '{"target_ip":"1.1.1.1","port":443,"timeout_ms":5000}' \
  http://127.0.0.1:9090/v1/check
```

## 面板配置

打开面板的“系统 → GFW 探针节点”，填入：

```json
[
  {"name":"大陆探针1","location":"mainland","url":"https://cn-probe.example.com","token":"大陆节点令牌"},
  {"name":"香港探针1","location":"hk","url":"https://hk-probe.example.com","token":"香港节点令牌"},
  {"name":"海外探针1","location":"overseas","url":"https://us-probe.example.com","token":"海外节点令牌"}
]
```

“大陆失败探针数量”是判定阈值。配置两个大陆探针时可填 `2`，避免单台探针网络抖动触发换 IP。面板每 2 分钟检查一次；大陆探针达到阈值失败且香港/海外至少一个成功时，才标记疑似被墙，并在连续两次确认后为当前 AWS 账号更换 IP。探针超时或结果不足时状态为未知，不会替补账号。
