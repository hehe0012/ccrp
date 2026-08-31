# ccrp:一个基于cc-switch路由来进行ssh反向代理的工具

`ccrp` 是一个零依赖、单文件的 Python 工具，用 SSH 反向隧道把本地 `cc-switch` 路由提供给远程 Linux 服务器使用。

默认监听地址绑定到 `127.0.0.1`，只允许服务器本机访问，不会自动暴露到公网。

## 当前测试拓扑

本文档使用以下配置和端口：

```text
本地 cc-switch：       127.0.0.1:15721
服务器 SSH 反向端口：  127.0.0.1:18082
服务器 ccrp 代理端口：127.0.0.1:18083
服务器配置：           ~/software/SSHRev/ccrp.deploy-test.json
本地配置：             D:\workspace\projects\SSHRev\ccrp.h102-15721-fresh.json
SSH 主机别名：         h102
```

完整链路：

```text
服务器 Codex
  -> 127.0.0.1:18083（服务器端 ccrp server）
  -> 127.0.0.1:18082（SSH -R 反向端口）
  -> 本地 127.0.0.1:15721（cc-switch）
```

注意：`18082` 和 `18083` 必须与两端配置一致。不要把旧配置中使用 `18080` 的文件拿来启动这条链路。

## 一、服务器端更新代码

在本地 PowerShell 登录服务器：

```powershell
ssh h102
```

在服务器执行：

```bash
cd ~/software/SSHRev
git remote set-url origin git@github.com:hehe0012/ccrp.git
git pull origin main
```

确认新脚本存在并具有执行权限：

```bash
chmod +x ./scripts/start-ccrp-server.sh
ls -l ./scripts/start-ccrp-server.sh
```

检查服务器配置：

```bash
test -f ./ccrp.deploy-test.json && echo "配置文件存在" || echo "配置文件不存在"
grep -nE '"listen"|"local"|"remote_forward"' ./ccrp.deploy-test.json
```

配置中应该是：

```json
"listen": "127.0.0.1:18083"
"local": "127.0.0.1:15721"
"remote_forward": "127.0.0.1:18082"
```

如果配置文件不存在，可以在服务器上生成：

```bash
python3 ccrp.py init \
  --out ./ccrp.deploy-test.json \
  --ssh h102 \
  --local 127.0.0.1:15721 \
  --remote-port 18082 \
  --listen 127.0.0.1:18083 \
  --force
```

## 二、启动服务器端 ccrp

服务器端启动的是 HTTP 代理，推荐放在 `tmux` 中运行：

```bash
cd ~/software/SSHRev
./scripts/start-ccrp-server.sh \
  --config ./ccrp.deploy-test.json \
  --restart \
  --health http://127.0.0.1:18083/__ccrp/health
```

`--health` 后面必须是纯 URL，不能复制 Markdown 链接。正确写法：

```text
http://127.0.0.1:18083/__ccrp/health
```

启动过程中第一次出现：

```text
curl: (7) Failed to connect
```

通常只是服务还没来得及监听，脚本会自动重试。只要随后返回：

```json
{
  "ok": true
}
```

就表示服务器端代理已经启动。

查看服务器端状态：

```bash
./scripts/start-ccrp-server.sh --config ./ccrp.deploy-test.json --status
```

查看最近日志：

```bash
./scripts/start-ccrp-server.sh --config ./ccrp.deploy-test.json --logs
```

进入 tmux 会话：

```bash
tmux attach -t ccrp-server
```

进入后按 `Ctrl+B`，再按 `D`，可以退出 tmux 但不停止服务。停止服务器端代理：

```bash
./scripts/start-ccrp-server.sh --config ./ccrp.deploy-test.json --stop
```

确认服务器代理端口：

```bash
ss -lnt | grep -E '18082|18083' || true
curl -i http://127.0.0.1:18083/__ccrp/health
```

此时在本地隧道启动前，通常只能看到 `18083`；`18082` 要等本地 SSH 反向隧道建立后才会出现。

## 三、本地 Windows 启动 SSH 反向隧道

服务器端代理启动后，在本地 Windows 打开新的 PowerShell 窗口。

进入项目目录：

```powershell
cd D:\workspace\projects\SSHRev
```

确认本地 `cc-switch` 可访问：

```powershell
curl.exe -i http://127.0.0.1:15721/v1/models
```

如果没有 `/v1/models`，也可以测试根路径：

```powershell
curl.exe -i http://127.0.0.1:15721/
```

返回 `404` 仍然说明端口上有 HTTP 服务。

先检查本地配置：

```powershell
python .\ccrp.py doctor `
  -c .\ccrp.h102-15721-fresh.json
```

直接运行 PowerShell 启动脚本：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\start-ccrp.ps1 `
  -Config .\ccrp.h102-15721-fresh.json
```

也可以在当前 PowerShell 中运行：

```powershell
.\scripts\start-ccrp.ps1 `
  -Config .\ccrp.h102-15721-fresh.json
```

启动前脚本会打印实际 SSH 命令。必须看到：

```text
-R 127.0.0.1:18082:127.0.0.1:15721
```

如果看到的是 `18080`，立即按 `Ctrl+C` 停止，并检查 `-Config` 是否指定成了旧配置文件。

这个 PowerShell 窗口需要保持运行。关闭窗口或按 `Ctrl+C` 会停止 SSH 反向隧道。

## 四、检查服务器上的两个端口

回到服务器执行：

```bash
ss -lnt | grep -E '18082|18083'
```

正常应该同时看到：

```text
127.0.0.1:18082
127.0.0.1:18083
```

含义：

```text
18082：SSH 反向隧道
18083：服务器端 ccrp server
```

## 五、按层测试代理

### 1. 测试 SSH 反向隧道

```bash
curl -i http://127.0.0.1:18082/v1/models
```

这一步直接测试：

```text
服务器 18082 -> SSH -R -> 本地 15721 -> cc-switch
```

返回 `200`、`401`、`404` 或其他 HTTP 响应，说明请求已经到达本地服务。`Connection refused` 才表示端口没有建立或本地服务不可用。

### 2. 测试服务器端 ccrp 完整转发

```bash
curl -i http://127.0.0.1:18083/__ccrp/health
curl -i http://127.0.0.1:18083/v1/models
```

第一个请求验证服务器代理自身，第二个请求验证完整转发。

### 3. 查看服务器端日志

```bash
./scripts/start-ccrp-server.sh --config ./ccrp.deploy-test.json --logs
```

不要再次手动执行下面这条命令：

```bash
python3 ccrp.py server --config ./ccrp.deploy-test.json
```

如果 tmux 中已经有服务，再执行会报：

```text
OSError: [Errno 98] Address already in use
```

这是因为 `18083` 已经被第一个服务占用。

## 六、确认 Codex 配置

服务器执行：

```bash
grep -nE 'model_provider|base_url|wire_api|env_key' ~/.codex/config.toml
```

应该包含：

```toml
model_provider = "ccrp"

[model_providers.ccrp]
name = "ccrp"
base_url = "http://127.0.0.1:18083/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
```

如果仍然是 `wire_api = "chat"`，执行：

```bash
cd ~/software/SSHRev
scripts/install-agent-proxy-config.sh \
  --target codex \
  --base-url http://127.0.0.1:18083 \
  --codex-wire-api responses
```

不要直接打印完整的 `auth.json`，只检查 Key 是否存在：

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = Path.home() / ".codex" / "auth.json"
print("auth.json 存在：", path.exists())
if path.exists():
    data = json.loads(path.read_text(encoding="utf-8"))
    print("OPENAI_API_KEY 已设置：", bool(data.get("OPENAI_API_KEY")))
PY
```

## 七、测试 Codex

```bash
command -v codex
codex --version
codex exec --help
```

执行最小请求：

```bash
codex exec --skip-git-repo-check \
  "请只回复 CCRP_CODEX_OK，不要输出其他内容。"
```

如果当前版本不支持 `--skip-git-repo-check`，去掉该参数：

```bash
codex exec "请只回复 CCRP_CODEX_OK，不要输出其他内容。"
```

成功返回：

```text
CCRP_CODEX_OK
```

## 八、每天的简化启动流程

服务器端：

```bash
ssh h102
cd ~/software/SSHRev
./scripts/start-ccrp-server.sh \
  --config ./ccrp.deploy-test.json \
  --restart \
  --health http://127.0.0.1:18083/__ccrp/health
```

本地 Windows 端：

```powershell
cd D:\workspace\projects\SSHRev
.\scripts\start-ccrp.ps1 `
  -Config .\ccrp.h102-15721-fresh.json
```

服务器端验证：

```bash
ss -lnt | grep -E '18082|18083'
curl -sS http://127.0.0.1:18083/__ccrp/health
curl -i http://127.0.0.1:18083/v1/models
```

启动 Codex：

```bash
codex exec --skip-git-repo-check \
  "请只回复 CCRP_CODEX_OK，不要输出其他内容。"
```

## 九、停止流程

本地 PowerShell 窗口按：

```text
Ctrl+C
```

服务器端执行：

```bash
cd ~/software/SSHRev
./scripts/start-ccrp-server.sh \
  --config ./ccrp.deploy-test.json \
  --stop
```

确认两个端口已释放：

```bash
ss -lnt | grep -E '18082|18083' || echo "18082 和 18083 都已停止"
```

## 功能说明

- `ccrp.py up`：本地运行，负责 SSH `-R` 反向隧道。
- `ccrp.py server`：服务器运行，负责 HTTP 反向代理。
- `scripts/start-ccrp-server.sh`：服务器端 tmux 一键启动、重启、停止、状态和日志管理。
- `scripts/start-ccrp.ps1`：本地 Windows 一键启动，并在启动前检查配置、打印实际 SSH 转发命令。

## 安全建议

- 优先使用 `127.0.0.1`，不要无意中绑定到 `0.0.0.0`。
- 不要把真实 API Key、代理 Token 或服务器私钥提交到 Git。
- 暴露公网前必须配置鉴权、TLS、防火墙和安全组。
- 本地 PowerShell 隧道窗口和服务器端 `ccrp-server` tmux 会话必须同时运行。