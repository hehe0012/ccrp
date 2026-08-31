# ccrp:一个基于cc-switch路由来进行ssh反向代理的工具

`ccrp` 是一个零依赖、单文件的 Python 工具，通过 SSH 反向隧道把本地 `cc-switch` 路由提供给远程 Linux 服务器使用。

默认监听地址绑定到 `127.0.0.1`，只允许服务器本机访问，不会自动暴露到公网。

本文档使用纯命令启动，不依赖 Windows `.cmd`、PowerShell 启动器或服务器端启动脚本。

## 当前测试拓扑

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

`18082` 和 `18083` 必须与两端配置一致。不要使用旧配置中把反向端口设置为 `18080` 的文件。

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

确认 Python 和配置文件：

```bash
python3 --version
test -f ./ccrp.deploy-test.json && echo "配置文件存在" || echo "配置文件不存在"
grep -nE '"listen"|"local"|"remote_forward"' ./ccrp.deploy-test.json
```

配置中应为：

```json
"listen": "127.0.0.1:18083"
"local": "127.0.0.1:15721"
"remote_forward": "127.0.0.1:18082"
```

如果配置文件不存在，在服务器执行：

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

服务器端直接使用 `tmux` 启动 `ccrp.py server`：

```bash
cd ~/software/SSHRev
tmux kill-session -t ccrp-server 2>/dev/null || true
tmux new-session -d -s ccrp-server \
  "cd \$HOME/software/SSHRev && python3 ccrp.py server --config ./ccrp.deploy-test.json"
```

检查服务器端代理是否启动：

```bash
tmux list-sessions
ss -lnt | grep -E '18082|18083' || true
curl -i http://127.0.0.1:18083/__ccrp/health
```

此时通常只能看到 `18083`，因为本地 SSH 反向隧道还没有启动。健康检查应返回：

```json
{
  "ok": true
}
```

查看服务器端日志：

```bash
tmux capture-pane -pt ccrp-server -S -100
```

进入 tmux 会话：

```bash
tmux attach -t ccrp-server
```

进入后按 `Ctrl+B`，再按 `D`，可以退出 tmux 而不停止服务。

不要在 tmux 服务已经运行时再次手动执行：

```bash
python3 ccrp.py server --config ./ccrp.deploy-test.json
```

否则会因为 `18083` 已经被占用而出现：

```text
OSError: [Errno 98] Address already in use
```

## 三、本地 Windows 启动 SSH 反向隧道

服务器端启动后，在本地 Windows 打开新的 PowerShell 窗口：

```powershell
cd D:\workspace\projects\SSHRev
```

确认本地 `cc-switch` 可访问：

```powershell
curl.exe -i http://127.0.0.1:15721/v1/models
```

如果没有 `/v1/models`，可以测试根路径：

```powershell
curl.exe -i http://127.0.0.1:15721/
```

先检查本地配置：

```powershell
python .\ccrp.py doctor `
  -c .\ccrp.h102-15721-fresh.json
```

直接运行已经验证过的 Python 命令：

```powershell
python -u .\ccrp.py up `
  -c .\ccrp.h102-15721-fresh.json
```

这个 PowerShell 窗口需要保持运行。关闭窗口或按 `Ctrl+C` 会停止 SSH 反向隧道。

启动命令使用的配置必须包含：

```json
"local": "127.0.0.1:15721",
"remote_forward": "127.0.0.1:18082"
```

如需确认实际 SSH 命令，可以单独执行：

```powershell
python .\ccrp.py print-ssh `
  -c .\ccrp.h102-15721-fresh.json
```

输出中必须包含：

```text
-R 127.0.0.1:18082:127.0.0.1:15721
```

## 四、确认两个服务器端口

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

如果只有 `18083`：

- 本地 `ccrp.py up` 没有运行；
- 本地使用了错误配置；
- SSH 反向端口建立失败。

如果看到 `18080` 而不是 `18082`，说明仍有旧的隧道进程或旧配置在运行。停止本地旧的 `ccrp.py up`，再使用正确配置重新启动。

## 五、按层测试代理

### 1. 测试 SSH 反向隧道

```bash
curl -i http://127.0.0.1:18082/v1/models
```

这一步测试：

```text
服务器 18082 -> SSH -R -> 本地 15721 -> cc-switch
```

返回 `200`、`401`、`404` 或其他 HTTP 响应，说明请求已经到达本地服务。`Connection refused` 才表示端口没有建立或本地服务不可用。

### 2. 测试服务器端代理

```bash
curl -i http://127.0.0.1:18083/__ccrp/health
curl -i http://127.0.0.1:18083/v1/models
```

第一个请求验证服务器代理自身，第二个请求验证完整转发。

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

重点确认：

```toml
model_provider = "ccrp"
base_url = "http://127.0.0.1:18083/v1"
wire_api = "responses"
```

不要直接打印完整的 `auth.json`，只检查 API Key 是否存在：

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

如果当前版本不支持 `--skip-git-repo-check`，执行：

```bash
codex exec "请只回复 CCRP_CODEX_OK，不要输出其他内容。"
```

成功返回：

```text
CCRP_CODEX_OK
```

## 八、日常启动流程

### 服务器端

```bash
ssh h102
cd ~/software/SSHRev
tmux kill-session -t ccrp-server 2>/dev/null || true
tmux new-session -d -s ccrp-server \
  "cd \$HOME/software/SSHRev && python3 ccrp.py server --config ./ccrp.deploy-test.json"
curl -sS http://127.0.0.1:18083/__ccrp/health
```

### 本地 Windows

```powershell
cd D:\workspace\projects\SSHRev
python -u .\ccrp.py up `
  -c .\ccrp.h102-15721-fresh.json
```

### 服务器端验证

```bash
ss -lnt | grep -E '18082|18083'
curl -sS http://127.0.0.1:18083/__ccrp/health
curl -i http://127.0.0.1:18083/v1/models
```

### 启动 Codex

```bash
codex exec --skip-git-repo-check \
  "请只回复 CCRP_CODEX_OK，不要输出其他内容。"
```

## 九、停止流程

### 停止本地 SSH 隧道

在本地运行 `ccrp.py up` 的 PowerShell 窗口按：

```text
Ctrl+C
```

### 停止服务器端代理

服务器执行：

```bash
tmux kill-session -t ccrp-server
```

确认端口释放：

```bash
ss -lnt | grep -E '18082|18083' || echo "18082 和 18083 都已停止"
```

## 配置字段

- `ssh.host`：SSH 主机别名，例如 `h102` 或 `user@example.com`。
- `ssh.options`：额外 SSH `-o` 参数。
- `server_proxy.listen`：服务器端代理监听地址。私有访问使用 `127.0.0.1:18083`。
- `routes[].local`：本地服务地址，例如 `127.0.0.1:15721`。
- `routes[].remote_forward`：SSH `-R` 在服务器本机创建的转发地址，例如 `127.0.0.1:18082`。
- `routes[].path_prefix`：代理匹配的路径前缀。
- `routes[].strip_path_prefix`：转发到上游前是否去掉路径前缀。
- `routes[].target_path_prefix`：转发到上游前额外添加的路径前缀。
- `routes[].preserve_host`：是否保留原始 `Host` 请求头。

## 安全建议

- 优先使用 `127.0.0.1`，不要无意中绑定到 `0.0.0.0`。
- 不要把真实 API Key、代理 Token 或服务器私钥提交到 Git。
- 暴露公网前必须配置鉴权、TLS、防火墙和安全组。
- 本地 `ccrp.py up` 进程和服务器端 `ccrp-server` tmux 会话必须同时运行。