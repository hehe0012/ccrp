# ccrp: private cc-switch reverse proxy over SSH

`ccrp` is a dependency-free Python helper that exposes local HTTP routes, such as routes created by `cc-switch`, to a remote Linux server through SSH reverse tunnels.

The default deployment is private: remote ports bind to `127.0.0.1` on the server, so nothing is exposed to the public internet unless you explicitly configure it.

## Features

- Single Python file, no runtime dependencies.
- Works with any SSH host alias, not just one server.
- Private-by-default SSH reverse tunnels using `ssh -R 127.0.0.1:...`.
- Optional server-side HTTP reverse proxy for multiple path/host routes.
- Easy server install through SSH, with tmux or user systemd options.
- Optional token authentication and built-in HTTPS when you intentionally expose the proxy.

## Quick start: private access from the server

Assume your local `cc-switch` listens on `127.0.0.1:3456`, and your SSH config has a host alias named `my-server`.

```powershell
python .\ccrp.py init --ssh my-server --local 127.0.0.1:3456 --remote-port 18080 --force
python .\ccrp.py doctor -c .\ccrp.config.json
python .\ccrp.py up -c .\ccrp.config.json
```

While `up` is running locally, log into the server and test:

```bash
curl http://127.0.0.1:18080/__ccrp/health
```

This mode does not need any public server port, firewall opening, `GatewayPorts`, Nginx, Caddy, or TLS, because the HTTP traffic lives inside SSH.

## Quick start: path router on the server, still private

Use the server-side proxy when you want one stable server-local endpoint, path routing, host routing, health checks, or optional auth/TLS.

```powershell
python .\ccrp.py init --ssh my-server --local 127.0.0.1:3456 --remote-port 18080 --listen 127.0.0.1:8080 --force
python .\ccrp.py install-server -c .\ccrp.config.json --tmux
python .\ccrp.py up -c .\ccrp.config.json
```

Then on the server:

```bash
curl http://127.0.0.1:8080/__ccrp/health
```

## Configure Claude Code or Codex on the server

After the tunnel is running, you can configure server-side clients to use the reverse proxy endpoint:

```bash
# Run on the server after uploading scripts/install-agent-proxy-config.sh
~/.local/bin/install-agent-proxy-config.sh --target both --base-url http://127.0.0.1:18080
```

It generates Claude Code and Codex config/wrapper files that point to the private reverse proxy port. See [docs/AGENTS.md](docs/AGENTS.md).

## Deploy to another server

1. Make sure the new server can be reached by SSH:

   ```bash
   ssh new-server 'python3 --version && command -v tmux'
   ```

2. Create a config for that server:

   ```powershell
   python .\ccrp.py init --ssh new-server --local 127.0.0.1:3456 --remote-port 18080 --listen 127.0.0.1:8080 --out ccrp.new-server.json --force
   ```

3. Upload and start the server-side proxy, if you need path routing:

   ```powershell
   python .\ccrp.py install-server -c .\ccrp.new-server.json --tmux
   ```

4. Start the local SSH tunnel:

   ```powershell
   python .\ccrp.py up -c .\ccrp.new-server.json
   ```

## Public exposure, auth, and HTTPS

Private mode is recommended. If you intentionally bind the server proxy to `0.0.0.0`, add authentication and transport encryption.

Example config fragment:

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

Client requests must include one of:

```bash
curl -H "Authorization: Bearer $CCRP_TOKEN" https://server.example:8443/__ccrp/health
curl -H "X-CCRP-Token: $CCRP_TOKEN" https://server.example:8443/__ccrp/health
```

For a public domain, a real TLS certificate behind Caddy/Nginx is usually better than self-signed certificates.

## Common commands

```powershell
python .\ccrp.py --version
python .\ccrp.py print-ssh -c .\ccrp.config.json
python .\ccrp.py doctor -c .\ccrp.config.json
python .\ccrp.py install-server -c .\ccrp.config.json --tmux
python .\ccrp.py up -c .\ccrp.config.json
```

Remote tmux operations:

```bash
tmux attach -t ccrp-server
tmux kill-session -t ccrp-server
```

## Config reference

- `ssh.host`: SSH host alias, for example `my-server`, `h102`, or `user@example.com`.
- `ssh.options`: extra SSH `-o` options.
- `server_proxy.listen`: server-side reverse proxy listen address. Use `127.0.0.1:8080` for private-only access.
- `server_proxy.auth.token`: literal token. Avoid committing real tokens.
- `server_proxy.auth.token_env`: environment variable name containing the token on the server.
- `server_proxy.tls.enabled`: enable built-in HTTPS.
- `routes[].local`: local service address, for example `127.0.0.1:3456`.
- `routes[].remote_forward`: server loopback address created by SSH `-R`, for example `127.0.0.1:18080`.
- `routes[].path_prefix`: public path prefix matched by `ccrp server`.
- `routes[].strip_path_prefix`: strip the public prefix before proxying upstream.
- `routes[].target_path_prefix`: add this prefix before proxying upstream.
- `routes[].preserve_host`: forward the original `Host` header instead of using the upstream host.

## Development

Run tests:

```bash
python -m unittest discover -s tests
```

Run the CLI locally:

```bash
python ccrp.py --help
```
