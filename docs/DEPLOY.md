# Deployment guide

## Recommended default: private SSH reverse tunnel

Use this when only programs on the server need to call your local `cc-switch`.

```bash
ssh -N -T -o ExitOnForwardFailure=yes -R 127.0.0.1:18080:127.0.0.1:3456 my-server
```

No public port is opened. The server reaches the local service through:

```bash
curl http://127.0.0.1:18080
```

## Server-side one-click launcher

After the repository and a server config have been installed, start the server-side proxy with:

```bash
cd ~/software/SSHRev
chmod +x scripts/start-ccrp-server.sh
scripts/start-ccrp-server.sh --config ~/.config/ccrp/config.json
```

The script starts `ccrp.py server` in the `ccrp-server` tmux session. Useful commands:

```bash
scripts/start-ccrp-server.sh --status
scripts/start-ccrp-server.sh --logs
scripts/start-ccrp-server.sh --restart
scripts/start-ccrp-server.sh --stop
```

Use `--foreground` when tmux is unavailable. The local machine must still run `ccrp.py up` to create the SSH reverse tunnel.
## Server-side router with tmux

Use this when you want a stable local endpoint on the server, route health checks, and multiple route prefixes.

Local machine:

```powershell
python .\ccrp.py install-server -c .\ccrp.config.json --tmux
python .\ccrp.py up -c .\ccrp.config.json
```

Server:

```bash
curl http://127.0.0.1:8080/__ccrp/health
tmux attach -t ccrp-server
```

## Server requirements

Minimum:

- Linux server reachable through SSH.
- `python3` on server.
- OpenSSH client on local machine.

Recommended:

- `tmux` on server for easy long-running server proxy management.

Optional:

- user systemd if you prefer a service unit.
- Caddy/Nginx if you expose the proxy publicly with a domain.

## Public exposure checklist

Only bind to `0.0.0.0` if all of these are true:

- You intentionally want access from outside the server.
- You configured token auth.
- You configured HTTPS directly in `ccrp`, or put Caddy/Nginx with TLS in front of it.
- Your firewall/security group only allows the ports you actually need.

## GitHub release checklist

Before tagging a release:

1. Run `python -m unittest discover -s tests`.
2. Run `python ccrp.py --help` and `python ccrp.py init --help`.
3. Verify `examples/ccrp.config.json` contains no secrets.
4. Update `VERSION` in `ccrp.py`.
5. Create a Git tag, for example `v0.2.0`.
