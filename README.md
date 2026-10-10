# GCP IP 管理面板（第一版）

独立管理 GCP Compute Engine 实例，提供开关机、当前公网 IP、IP 探测、IP 黑名单、自动换 IP 和操作日志。暂不接入 V2Board、V2bX、DNS 和 Telegram。

## 运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

访问 `http://服务器IP:8080/docs` 查看 API。

## GitHub 一键安装和更新

把仓库推送到 GitHub 后，可以在服务器执行：

```bash
REPO_URL=https://github.com/你的账号/你的仓库.git bash -c \
  "\$(curl -fsSL https://raw.githubusercontent.com/你的账号/你的仓库/main/install.sh)"
```

在已经安装的面板上更新：

```bash
/opt/gcp-ip-panel/update.sh
```

更新脚本会从 GitHub 拉取 `main` 分支最新代码、安装新增依赖并重启服务。面板功能更新后，在本地提交并推送即可：

```bash
git add .
git commit -m "update panel"
git push origin main
```

生产环境建议使用 GCP 服务账号，并设置：

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

## 自动换 IP 逻辑

1. 检测当前公网 IP。
2. 连续失败时把旧 IP 写入黑名单。
3. 停止实例，解除临时公网 IP，启动实例获取新 IP。
4. 从香港或海外探针检测新 IP。
5. 新 IP 不合格则重复更换，直到成功或达到本次任务的最大尝试次数。

默认使用 SQLite 保存黑名单和日志。GCP API 未配置时，应用以 mock 模式运行，便于先测试面板流程。

## AWS 配额检测与 Telegram 通知

系统后台支持按账号定期检查指定区域的 EC2 标准型按需实例 vCPU 配额，默认每 120 秒检查一次；同时汇总已支持 AWS 区域中的 EC2 实例数量。配额检测和 Telegram 是整个站点的全局设置，不放在账号卡片内；账号卡片只显示最新配额结果、运行中的 EC2 数量和检测时间。状态从正常变为接近上限、达到上限、配额上限发生变化或检测异常时发送一次 Telegram 通知，恢复正常时也会通知。

在面板的“AWS 配额检测与 Telegram”区域填写检测区域、告警阈值、Telegram Bot Token 和 Chat ID 后保存即可。建议为每个 AWS 账号授予以下只读权限：

- `servicequotas:GetServiceQuota`
- `servicequotas:GetAWSDefaultServiceQuota`
- `ec2:DescribeInstances`
- `ec2:DescribeInstanceTypes`（用于读取非固定型号的 vCPU 数量）

也可以直接附加 AWS 托管策略 `ServiceQuotasReadOnlyAccess`，并补充 EC2 描述权限。Telegram Token 只保存到服务器的 SQLite 配置中，不会返回到账号卡片。

AWS 账号卡片的“EC2 实例”会跳转到 `#/ec2-instances?aid=账号ID` 独立实例页，读取账号在已支持区域已有的实例并以卡片显示；“EC2 创建”支持数量、架构、硬盘、公网 IP、Spot、IPv6、静态 IP、开机脚本以及 GFW 屏蔽检测配置。启用 GFW 检测后，连续两次健康检查失败会在当前账号尝试更换 IP；只有 AWS 账号/API 异常才触发同组账号替补。GFW 检测配置会随部署记录保存，账号替补重建时继承这些设置。

EC2 实例卡片提供“删除实例”按钮。删除会调用 AWS TerminateInstances 永久终止实例，并移除面板中的自动检测记录；操作前会二次确认。终止实例不会自动释放独立 Elastic IP，避免误释放仍在使用的地址。

创建 EC2 时不要求填写 EC2 Key Pair，面板使用创建表单中的密码初始化登录：

- Linux 实例使用 root 用户登录，并开启 SSH root 密码登录。
- Linux 密码和“开机脚本”通过标准 cloud-init 写入；普通 Shell 脚本会在首次启动的 runcmd 阶段执行。
- Windows 实例使用 Administrator 登录。
- 每个 VPC 自动创建并复用 gcpmx-open-all 安全组，放行全部 IPv4/IPv6 入站端口，并为实例绑定公网 IPv4（勾选“禁用公网 IP”时除外）。
- 因为该安全组会开放所有端口，只建议用于自有测试账号；生产环境应在 AWS 控制台收紧入站来源。
- 创建账号需要额外具备 ec2:DescribeVpcs、ec2:DescribeSubnets、ec2:DescribeSecurityGroups、ec2:CreateSecurityGroup 和 ec2:AuthorizeSecurityGroupIngress 权限。

删除请求会立即返回并放到后台线程执行，避免 AWS TerminateInstances 或区域扫描等待时阻塞整个面板。

删除 AWS 账号时只删除面板保存的账号、配额、运行状态和自动检测记录，不会终止 AWS 云端实例；这样即使账号仍有关联部署，也可以安全移除账号卡片。

## GFW 多地探针

仓库中的 `probe-agent/` 是独立探针服务。建议在大陆、香港或海外 VPS 各部署一个，面板通过探针从不同网络测试 AWS 实例公网 IP。大陆探针达到失败阈值且香港/海外探针成功时，才判定疑似被墙；探针超时或结果不足只显示未知，不会替补 AWS 账号。探针每 2 分钟由面板后台检查，连续两次确认后才换当前账号的 IP。

部署示例：

```bash
cd probe-agent
PROBE_LOCATION=mainland bash install.sh
```

香港或海外节点把位置改成 `hk` 或 `overseas`。安装后从 `/etc/gcpmx-probe/probe.env` 读取 `PROBE_TOKEN`，并在面板“系统 → GFW 探针节点”粘贴 JSON 配置。生产环境建议用 Nginx/Caddy 为 9090 端口加 HTTPS；详细接口和配置格式见 `probe-agent/README.md`。
