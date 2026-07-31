# Agent client configuration

This guide shows how to configure server-side Claude Code and/or Codex to call a local `cc-switch` route through a private ccrp SSH reverse proxy.

## Assumed topology

Local machine:

```text
cc-switch: http://127.0.0.1:15721
```

SSH reverse tunnel:

```text
local 127.0.0.1:15721 -> server 127.0.0.1:18080
```

Server-side clients should call:

```text
http://127.0.0.1:18080
```

## Install client config on the server

Upload the script to the server:

```powershell
scp .\scripts\install-agent-proxy-config.sh h102:~/.local/bin/install-agent-proxy-config.sh
ssh h102 'chmod +x ~/.local/bin/install-agent-proxy-config.sh'
```

Run it on the server:

```bash
~/.local/bin/install-agent-proxy-config.sh --target both --base-url http://127.0.0.1:18080
```

This creates:

- `~/.config/ccrp/agent-proxy.env`
- `~/.local/bin/claude-ccrp`
- `~/.local/bin/codex-ccrp`
- `~/.claude/settings.json` for Claude Code
- `~/.codex/config.toml` and `~/.codex/.env` for Codex

Then start clients with:

```bash
claude-ccrp
codex-ccrp
```

## Token-protected proxy

If the server-side ccrp proxy has token auth enabled, keep the token in a server environment variable and reference it from generated configs:

```bash
export CCRP_TOKEN='replace-with-your-token'
~/.local/bin/install-agent-proxy-config.sh \
  --target both \
  --base-url http://127.0.0.1:18080 \
  --token-env CCRP_TOKEN
```

The generated wrappers will source `~/.config/ccrp/agent-proxy.env` before launching clients.

## Claude Code only

```bash
~/.local/bin/install-agent-proxy-config.sh \
  --target claude \
  --base-url http://127.0.0.1:18080 \
  --claude-model claude-sonnet-4-5
```

The script writes `ANTHROPIC_BASE_URL` into `~/.claude/settings.json` under `env`.

## Codex only

Most OpenAI-compatible proxy endpoints use `/v1`, so the script defaults Codex to:

```text
http://127.0.0.1:18080/v1
```

Run:

```bash
~/.local/bin/install-agent-proxy-config.sh \
  --target codex \
  --base-url http://127.0.0.1:18080 \
  --codex-model gpt-5-codex \
  --codex-wire-api chat
```

The script creates a `ccrp` model provider in `~/.codex/config.toml` and makes it the default provider unless `--no-set-default` is passed.

## Dry run

Preview changes without writing files:

```bash
~/.local/bin/install-agent-proxy-config.sh --target both --base-url http://127.0.0.1:18080 --dry-run
```

## Safety notes

- The recommended reverse proxy URL is server-local: `http://127.0.0.1:18080`.
- Do not bind the proxy to `0.0.0.0` unless you intentionally want public access and have auth/TLS configured.
- The script backs up existing `~/.codex/config.toml` before modifying it.
- Real API/proxy tokens should not be committed to Git.
