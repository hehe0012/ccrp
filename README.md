# ccrp:一个基于cc-switch路由来进行ssh反向代理的工具

`ccrp` 是一个零依赖、单文件的 Python 工具，通过 SSH 反向隧道把本地 `cc-switch` 路由提供给远程 Linux 服务器使用。

默认监听地址绑定到 `127.0.0.1`，只允许服务器本机访问，不会自动暴露到公网。

本文档使用纯命令启动，不依赖 Windows `.cmd`、PowerShell 启动器或服务器端启动脚本。

## Windows 图形界面

项目提供一个基于 Python Tkinter 的 Windows 图形界面，不需要第三方 Python 依赖。它可以：

1. 读取指定的 OpenSSH 配置文件，列出其中的 `Host` 目标并选择服务器。
2. 通过 SSH 读取服务器家目录下的文件夹，选择 CCRP 部署目录。
3. 在界面中设置本地 cc-switch 端口、SSH 反向端口、服务器代理端口和超时参数。
4. 在服务器指定目录克隆或更新 GitHub 仓库，将配置写入仓库目录，并在 tmux 会话中启动 CCRP 服务。
5. 启动和停止本地 SSH 反向隧道，周期性检查隧道、服务器健康接口和两个服务器端口。

启动方式：

```powershell
cd D:\workspace\projects\SSHRev
python .\ccrp_gui.py
```

如果通过 pip 安装了本项目，也可以运行：

```powershell
ccrp-gui
```

也可以使用 Windows 打包版本。下载 `ccrp-gui.exe` 和 `ccrp.exe` 后放在同一个文件夹，双击 `ccrp-gui.exe` 即可。`ccrp.exe` 是 GUI 使用的命令行组件，不要单独删除或改名。GitHub Actions 会在手动运行或推送 `v*` 标签时构建 Windows 工件。

GUI 不保存 SSH 密码和私钥。SSH 登录仍由 OpenSSH 配置、Windows ssh-agent 或系统凭据完成。选择的 SSH 配置文件会通过 `ssh -F` 传给底层命令。服务器需要有 `git`、`python3` 和 `tmux`；`curl` 和 `ss` 主要用于界面监控。

GUI 的典型使用顺序是：选择 SSH 配置文件并读取目标 -> 选择目标服务器 -> 输入或读取远程部署目录 -> 设置仓库地址、分支、端口和超时 -> 点击“部署并启动服务” -> 点击“启动本地隧道”。部署完成后，底部状态栏会每 5 秒刷新一次。

源码构建 Windows 版本需要安装 PyInstaller：

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --console --name ccrp ccrp.py
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ccrp-gui ccrp_gui.py
```

构建完成后，将 `dist\ccrp.exe` 和 `dist\ccrp-gui.exe` 放在同一个目录。命令行版本用于执行部署和 SSH 隧道，图形界面版本用于交互操作。

## 当前测试拓扑

```text
本地 cc-switch：       127.0.0.1:15721
服务器 SSH 反向端口：  127.0.0.1:18082
服务器 ccrp 代理端口：127.0.0.1:18083
服务器配置：           ~/software/SSHRev/ccrp.config.json
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

## 一、本地生成配置

如果本地还没有对应配置文件，可以在 Windows PowerShell 中生成：

```powershell
cd D:\workspace\projects\SSHRev

python .\ccrp.py init `
  --out .\ccrp.h102-15721-fresh.json `
  --ssh h102 `
  --local 127.0.0.1:15721 `
  --remote-port 18082 `
  --listen 127.0.0.1:18083 `
  --force
```

参数含义：

- `--out`：生成的本地配置文件。
- `--ssh`：SSH 主机别名，例如 `h102`。
- `--local`：本地 `cc-switch` 服务地址。
- `--remote-port`：服务器上由 SSH `-R` 创建的反向端口，这里是 `18082`。
- `--listen`：服务器端 `ccrp server` 的代理监听地址，这里是 `127.0.0.1:18083`。
- `--upstream-timeout`：服务器 CCRP 等待 cc-switch 上游连接/读取的空闲超时秒数，默认 `300`。
- `--ssh-connect-timeout`：SSH 建立连接的超时秒数，默认 `10`；它不控制 API 请求等待时间。

生成后先检查配置：

```powershell
python .\ccrp.py doctor `
  -c .\ccrp.h102-15721-fresh.json
```

再确认实际 SSH 反向转发命令：

```powershell
python .\ccrp.py print-ssh `
  -c .\ccrp.h102-15721-fresh.json
```

输出中必须包含：

```text
-R 127.0.0.1:18082:127.0.0.1:15721
```

如果看到的是 `18080`，说明使用了旧配置或生成参数不对，需要重新生成配置。
## 二、从本地一键克隆部署服务器

服务端部署由本地 `install-server` 命令通过 SSH 完成。它会先在服务器指定目录克隆仓库；如果目录已经是 Git 仓库，则校正 `origin` 并快进更新指定分支。随后把本地配置写到克隆目录的 `ccrp.config.json`，并在 tmux 会话中启动仓库里的 `ccrp.py`。

首次部署和后续更新都可以使用同一条命令：

```powershell
python .\ccrp.py install-server `
  --config .\ccrp.h102-15721-fresh.json `
  --remote-dir ~/software/SSHRev `
  --repo-url https://github.com/hehe0012/ccrp.git `
  --branch main `
  --tmux
```

如果仓库是公开仓库，可直接使用上面的 HTTPS 地址。私有仓库应改用服务器已配置 SSH key 的地址，例如 `git@github.com:hehe0012/ccrp.git`，并确认服务器上的 SSH key 有读取权限。不要把 GitHub Token 明文写入命令历史、仓库、README 或聊天内容。

部署行为和限制：

- 服务器必须安装 `git`、`python3` 和 `tmux`，并能访问 GitHub。
- 首次部署时，目标目录不存在或为空，命令会执行 `git clone`。
- 后续部署时，命令会把 `origin` 设置为 `--repo-url`，抓取指定分支并通过 `git pull --ff-only` 更新。若本地有无法快进的提交，更新会失败，不会重置或覆盖这些提交。
- 目标目录非空且不是 Git 仓库时会拒绝部署，不会删除或覆盖目录内容。
- 每次部署会重新写入克隆目录下的 `ccrp.config.json`，并重启 `ccrp-server` tmux 会话。

GUI 中的“仓库地址”和“分支”默认分别为 `https://github.com/hehe0012/ccrp.git` 与 `main`，可以按部署目标修改。

部署成功后，命令输出会显示仓库 revision、远程配置路径和 tmux 会话。服务器端运行配置默认为 `~/software/SSHRev/ccrp.config.json`；如需使用已有配置文件，请在 GUI 中调整相应配置，或通过 CLI 的 `--remote-config` 指定目标路径。

服务器端部署目录和运行配置确认命令：

```bash
cd ~/software/SSHRev
git remote -v
git log -1 --oneline
python3 --version
test -f ./ccrp.config.json && echo "配置文件存在" || echo "配置文件不存在"
grep -nE '"listen"|"local"|"remote_forward"|"upstream_timeout"' ./ccrp.config.json
```

配置中应为：

```json
"listen": "127.0.0.1:18083"
"local": "127.0.0.1:15721"
"remote_forward": "127.0.0.1:18082"
```

超时参数位于配置文件中：

```json
{
  "ssh": {
    "host": "h102",
    "connect_timeout": 10,
    "server_alive_interval": 30,
    "server_alive_count_max": 3
  },
  "server_proxy": {
    "listen": "127.0.0.1:18083",
    "upstream_timeout": 300
  }
}
```

- `server_proxy.upstream_timeout`：CCRP 到 SSH 反向端口的上游连接/读写空闲超时，单位为秒。默认 `300`，适用于较慢的长请求；只有上游连续无数据超过该时间才超时。
- `ssh.connect_timeout`：本地 SSH 客户端建立服务器连接的超时，单位为秒，默认 `10`。只影响隧道建连，不影响 API 请求时长。
- `ssh.server_alive_interval` 和 `ssh.server_alive_count_max`：SSH 隧道保活间隔和连续无响应次数，默认分别为 `30` 秒和 `3` 次。
- `ssh.config_file`：可选的 OpenSSH 配置文件路径，GUI 选择的 SSH 文件会通过 `ssh -F` 使用。

也可以在生成配置时设置超时，例如：

```powershell
python .\ccrp.py init `
  --out .\ccrp.h102-15721-fresh.json `
  --ssh h102 `
  --local 127.0.0.1:15721 `
  --remote-port 18082 `
  --listen 127.0.0.1:18083 `
  --upstream-timeout 300 `
  --ssh-connect-timeout 15 `
  --force
```

修改 `upstream_timeout` 后，必须在服务器端让正在运行的 `ccrp server` 重新读取配置：停止并重启对应的 tmux 会话（见下方启动步骤）。仅修改本地 JSON 不会改变已经运行的服务器进程。

如需在服务器上手动生成配置（一般 GUI/`install-server` 已经自动上传配置），可执行：

```bash
python3 ccrp.py init \
  --out ./ccrp.deploy-test.json \
  --ssh h102 \
  --local 127.0.0.1:15721 \
  --remote-port 18082 \
  --listen 127.0.0.1:18083 \
  --force
```

## 三、检查服务器端 ccrp

服务器端直接使用 `tmux` 启动 `ccrp.py server`：

```bash
cd ~/software/SSHRev
tmux kill-session -t ccrp-server 2>/dev/null || true
tmux new-session -d -s ccrp-server \
  "cd \$HOME/software/SSHRev && python3 ccrp.py server --config ./ccrp.config.json"
```

也可以临时覆盖配置文件中的上游等待时间，不修改 JSON：

```bash
tmux new-session -d -s ccrp-server \
  "cd \$HOME/software/SSHRev && python3 ccrp.py server --config ./ccrp.config.json --upstream-timeout 300"
```

如果配置文件中已经设置了 `server_proxy.upstream_timeout`，通常不需要这个命令行覆盖参数。

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
python3 ccrp.py server --config ./ccrp.config.json
```

否则会因为 `18083` 已经被占用而出现：

```text
OSError: [Errno 98] Address already in use
```

## 四、本地 Windows 启动 SSH 反向隧道

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

## 五、确认两个服务器端口

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

## 六、按层测试代理

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

## 七、确认 Codex 配置

服务器执行：

```bash
grep -nE 'model_provider|base_url|wire_api|env_key' ~/.codex/config.toml
```

示例：

```toml
model_provider = "ccrp"
model = "gpt-5.5"
model_reasoning_effort = "high"
sandbox_mode = "danger-full-access"
approval_policy = "never"
[model_providers.ccrp]
name = "ccrp"
base_url = "http://127.0.0.1:18083/v1"
wire_api = "responses"
requires_openai_auth = false

[model_providers.ccrp.http_headers]
Authorization = "Bearer sk-anything"

[tui]
screen_reader_detection_done = true

[marketplaces.openai-bundled]
source_type = "local"
source = "/home/guian/software/ccrp/home/.tmp/bundled-marketplaces/openai-bundled"

[plugins."visualize@openai-bundled"]
enabled = true

[permissions.workspace-custom.network]
enabled = true
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

## 八、测试 Codex

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

## 九、日常启动流程

### 服务器端

```bash
ssh h102
cd ~/software/SSHRev
tmux kill-session -t ccrp-server 2>/dev/null || true
tmux new-session -d -s ccrp-server \
  "cd \$HOME/software/SSHRev && python3 ccrp.py server --config ./ccrp.config.json"
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

## 十、停止流程

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
- `server_proxy.upstream_timeout`：服务器端等待上游响应的空闲超时，默认 `300` 秒。
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
