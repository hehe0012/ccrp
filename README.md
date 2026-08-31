# ccrp:一个基于cc-switch路由来进行ssh反向代理的工具

`ccrp` 是一个零依赖、单文件的 Python 工具，用来把本地 HTTP 服务（尤其是 `cc-switch` 创建的路由）通过 SSH 反向隧道转发到远程 Linux 服务器上。

默认部署方式是私有的：服务器端监听地址绑定到 `127.0.0.1`，不会暴露到公网。只有当你主动把监听地址配置为 `0.0.0.0`，并开放防火墙/安全组时，服务才会对外提供访问。

## 特性

- 单个 Python 文件，无运行时第三方依赖。
- 支持任意 SSH 主机别名，不绑定某一台服务器。
- 默认使用 `ssh -R 127.0.0.1:...` 创建仅服务器本机可访问的反向隧道。
- 可选服务器端 HTTP 反向代理，支持多路由、路径路由、Host 路由和健康检查。
- 支持通过 SSH 一键安装到服务器，可选择 `tmux` 或用户级 `systemd` 持久运行。
- 当确实需要公网访问时，支持可选 Token 鉴权和内置 HTTPS。
- 提供 Claude Code / Codex 配置脚本，可让服务器端 Agent 直接连接到反向代理端口。

## 适用场景

典型场景是：你的本地机器上运行了 `cc-switch`，例如监听在：

```text
http://127.0.0.1:15721
```

你希望服务器上的 Claude Code、Codex 或其他程序访问这个本地路由，但又不想把本地服务或服务器端口直接暴露到公网。`ccrp` 可以通过 SSH 反向代理实现：

```text
服务器客户端 -> 服务器 127.0.0.1:8080(ccrp server) -> 服务器 127.0.0.1:18080(SSH -R) -> 本地 127.0.0.1:15721(cc-switch)
```

## 快速开始：只创建 SSH 反向隧道

假设：

- 本地 `cc-switch` 监听在 `127.0.0.1:15721`
- SSH 配置里有一台服务器别名叫 `my-server`
- 希望服务器本机通过 `127.0.0.1:18080` 访问本地服务

在本地 Windows PowerShell 中执行：

```powershell
python .\ccrp.py init --ssh my-server --local 127.0.0.1:15721 --remote-port 18080 --force
python .\ccrp.py doctor -c .\ccrp.config.json
python .\ccrp.py up -c .\ccrp.config.json
```

当 `up` 在本地持续运行时，登录服务器测试：

```bash
curl http://127.0.0.1:18080/__ccrp/health
```

这种模式不需要开放任何公网端口，也不需要配置服务器防火墙、`GatewayPorts`、Nginx、Caddy 或 TLS，因为 HTTP 流量都在 SSH 隧道里传输。

### 一键启动（Windows）

配置文件生成后，可以直接双击项目根目录下的 `start-ccrp.cmd` 启动本地 SSH 反向隧道：

```text
SSHRev\start-ccrp.cmd
```

脚本会自动查找 `ccrp.config.json`、`ccrp.h102-15721.json`，然后运行 `ccrp doctor` 检查配置，最后启动 `ccrp up`。如果目录中有多个配置文件，建议明确指定配置：

```powershell
.\start-ccrp.cmd -Config .\ccrp.h102-15721.json
```

也可以直接使用 PowerShell 脚本：

```powershell
.\scripts\start-ccrp.ps1 -Config .\ccrp.h102-15721.json
```

启动窗口需要保持运行。关闭窗口或按 `Ctrl+C` 会停止 SSH 反向隧道。脚本不会自动隐藏窗口，这样可以直接看到 SSH 断线、端口占用等错误。

## 快速开始：启用服务器端代理入口

如果你希望服务器上有一个稳定的本机入口，并且需要路径路由、Host 路由、健康检查，或者后续添加鉴权/TLS，可以启用服务器端代理：

```powershell
python .\ccrp.py init --ssh my-server --local 127.0.0.1:15721 --remote-port 18080 --listen 127.0.0.1:8080 --force
python .\ccrp.py install-server -c .\ccrp.config.json --tmux
python .\ccrp.py up -c .\ccrp.config.json
```

然后在服务器上访问：

```bash
curl http://127.0.0.1:8080/__ccrp/health
```

链路如下：

```text
服务器客户端 -> 127.0.0.1:8080(ccrp server) -> 127.0.0.1:18080(SSH -R) -> 本地 127.0.0.1:15721(cc-switch)
```

## 服务器端一键启动 ccrp

服务器上的 `ccrp server` 负责提供 HTTP 代理入口；本地 Windows 端的 `ccrp.py up` 仍然负责建立 SSH 反向隧道。两者需要同时运行。

在服务器项目目录中执行：

```bash
cd ~/software/SSHRev
chmod +x scripts/start-ccrp-server.sh
scripts/start-ccrp-server.sh --config ~/.config/ccrp/config.json
```

如果配置文件位于项目目录的 `ccrp.config.json`，可以直接执行：

```bash
scripts/start-ccrp-server.sh
```

脚本默认使用 `tmux` 在后台运行，常用操作如下：

```bash
# 重启服务器端代理
scripts/start-ccrp-server.sh --restart

# 查看运行状态
scripts/start-ccrp-server.sh --status

# 查看最近 100 行日志
scripts/start-ccrp-server.sh --logs

# 进入 tmux 会话
tmux attach -t ccrp-server

# 停止服务器端代理
scripts/start-ccrp-server.sh --stop
```

也可以不使用 tmux，直接以前台模式运行：

```bash
scripts/start-ccrp-server.sh \
  --config ~/.config/ccrp/config.json \
  --foreground
```

启动后，可以用健康检查确认服务器端代理已经监听：

```bash
scripts/start-ccrp-server.sh \
  --health http://127.0.0.1:8080/__ccrp/health
```

注意：这个脚本只启动服务器端 `ccrp server`，不会创建 SSH 反向隧道。SSH 反向隧道仍需要在本地 Windows 端运行：

```powershell
.\start-ccrp.cmd -Config .\ccrp.h102-15721.json
```
## 在服务器上从 GitHub 零部署

```bash
mkdir -p ~/software
cd ~/software
git clone https://github.com/hehe0012/SSHRev.git
cd SSHRev
python3 -m unittest discover -s tests
python3 ccrp.py --version
```

如果只需要服务器本机访问本地服务，可以直接在本地运行 `ccrp.py up`；如果还需要服务器端代理入口，则在服务器上启动：

```bash
python3 ccrp.py init --out ccrp.server.json --ssh my-server --local 127.0.0.1:15721 --remote-port 18080 --listen 127.0.0.1:8080 --force
tmux new-session -d -s ccrp-server "cd $HOME/software/SSHRev && python3 ccrp.py server --config ccrp.server.json"
```

## 配置 Claude Code 或 Codex

反向隧道启动后，可以把服务器上的 Claude Code / Codex 配置为访问这个私有代理端口。

在服务器上执行：

```bash
cd ~/software/SSHRev
scripts/install-agent-proxy-config.sh --target both --base-url http://127.0.0.1:18080
```

脚本会生成或更新以下文件：

- `~/.config/ccrp/agent-proxy.env`
- `~/.local/bin/claude-ccrp`
- `~/.local/bin/codex-ccrp`
- `~/.claude/settings.json`
- `~/.codex/config.toml`
- `~/.codex/.env`
- `~/.codex/auth.json`

之后可以用包装命令启动客户端：

```bash
claude-ccrp
codex-ccrp
```

Codex 默认会写入：

```toml
wire_api = "responses"
```

如果你的代理只支持旧的 Chat Completions 风格接口，可以显式指定：

```bash
scripts/install-agent-proxy-config.sh \
  --target codex \
  --base-url http://127.0.0.1:18080 \
  --codex-wire-api chat
```

更多说明见 `docs/AGENTS.md`。

## 部署到其他服务器

1. 确认新服务器可以通过 SSH 连接，并且有 Python 3：

   ```bash
   ssh new-server 'python3 --version && command -v tmux'
   ```

2. 为新服务器生成配置：

   ```powershell
   python .\ccrp.py init --ssh new-server --local 127.0.0.1:15721 --remote-port 18080 --listen 127.0.0.1:8080 --out ccrp.new-server.json --force
   ```

3. 如果需要服务器端路径路由，安装并启动服务器端代理：

   ```powershell
   python .\ccrp.py install-server -c .\ccrp.new-server.json --tmux
   ```

4. 启动本地 SSH 反向隧道：

   ```powershell
   python .\ccrp.py up -c .\ccrp.new-server.json
   ```

## 公网暴露、鉴权和 HTTPS

推荐使用默认的私有模式。如果你确实要把服务器端代理绑定到 `0.0.0.0` 并暴露到公网，请务必配置鉴权和传输加密。

配置片段示例：

```json
{
  "server_proxy": {
    "listen": "0.0.0.0:8443",
    "auth": { "token_env": "CCRP_TOKEN" },
    "tls": {
      "enabled": true,
      "certfile": "/home/you/.config/ccrp/cert.pem",
      "keyfile": "/home/you/.config/ccrp/key.pem"
    }
  }
}
```

客户端请求需要携带以下任意一种 Token：

```bash
curl -H "Authorization: Bearer $CCRP_TOKEN" https://server.example:8443/__ccrp/health
curl -H "X-CCRP-Token: $CCRP_TOKEN" https://server.example:8443/__ccrp/health
```

如果绑定公网域名，通常建议使用 Caddy/Nginx 反代并申请真实 TLS 证书，而不是依赖自签名证书。

## 常用命令

```powershell
python .\ccrp.py --version
python .\ccrp.py print-ssh -c .\ccrp.config.json
python .\ccrp.py doctor -c .\ccrp.config.json
python .\ccrp.py install-server -c .\ccrp.config.json --tmux
python .\ccrp.py up -c .\ccrp.config.json
```

服务器端 `tmux` 操作：

```bash
tmux attach -t ccrp-server
tmux kill-session -t ccrp-server
```

## 配置说明

- `ssh.host`：SSH 主机别名，例如 `my-server`、`h102` 或 `user@example.com`。
- `ssh.options`：额外 SSH `-o` 参数。
- `server_proxy.listen`：服务器端反向代理监听地址。私有访问建议使用 `127.0.0.1:8080`。
- `server_proxy.auth.token`：明文 Token。不要把真实 Token 提交到 Git。
- `server_proxy.auth.token_env`：服务器上保存 Token 的环境变量名。
- `server_proxy.tls.enabled`：启用内置 HTTPS。
- `routes[].local`：本地服务地址，例如 `127.0.0.1:15721`。
- `routes[].remote_forward`：由 SSH `-R` 在服务器本机创建的转发地址，例如 `127.0.0.1:18080`。
- `routes[].path_prefix`：`ccrp server` 匹配的对外路径前缀。
- `routes[].strip_path_prefix`：转发到上游前是否去掉对外路径前缀。
- `routes[].target_path_prefix`：转发到上游前额外添加的路径前缀。
- `routes[].preserve_host`：是否保留原始 `Host` 请求头，而不是使用上游地址作为 `Host`。

## 开发

运行测试：

```bash
python -m unittest discover -s tests
```

查看 CLI 帮助：

```bash
python ccrp.py --help
```

## 安全建议

- 优先使用 `127.0.0.1` 监听，避免误暴露到公网。
- 不要提交真实 Token、API Key 或服务器私钥。
- 暴露公网前请确认防火墙、安全组、鉴权和 TLS 都已正确配置。
- 长期运行建议使用 `tmux` 或用户级 `systemd` 管理服务进程。