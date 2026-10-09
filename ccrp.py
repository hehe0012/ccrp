#!/usr/bin/env python3
"""
ccrp - cc-switch reverse proxy over SSH.

A dependency-free helper that exposes local HTTP routes (for example routes
created by cc-switch) on a remote Linux server through SSH reverse tunnels.

Typical private flow:
  1. python ccrp.py init --ssh my-server --local 127.0.0.1:3456 --remote-port 18080 --listen 127.0.0.1:8080
  2. python ccrp.py deploy-server -c ccrp.config.json --remote-dir ~/your_path
  3. python ccrp.py start-server -c ccrp.config.json --remote-dir ~/your_path
  4. python ccrp.py up -c ccrp.config.json
"""
from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import posixpath
import shlex
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

VERSION = "0.2.1"
DEFAULT_CONFIG = "ccrp.config.json"
DEFAULT_REPOSITORY_URL = "https://github.com/hehe0012/ccrp.git"
DEFAULT_REPOSITORY_BRANCH = "main"
DEFAULT_UPSTREAM_TIMEOUT = 300.0
UPSTREAM_CONNECT_RETRIES = 3
UPSTREAM_CONNECT_RETRY_DELAY = 0.25
DEFAULT_SSH_CONNECT_TIMEOUT = 10.0
DEFAULT_SSH_SERVER_ALIVE_INTERVAL = 30.0
DEFAULT_SSH_SERVER_ALIVE_COUNT_MAX = 3
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int

    def __str__(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class Route:
    name: str
    local: Endpoint
    remote_forward: Endpoint
    path_prefix: str = "/"
    host: str | None = None
    strip_path_prefix: bool = False
    target_path_prefix: str = ""
    preserve_host: bool = False


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def parse_endpoint(value: Any, *, default_host: str = "127.0.0.1", default_port: int | None = None) -> Endpoint:
    """Parse host:port, port, http://host:port, or {host, port}."""
    if isinstance(value, dict):
        host = str(value.get("host") or value.get("bind") or default_host)
        port_raw = value.get("port", default_port)
        if port_raw is None:
            raise ValueError(f"endpoint object lacks port: {value!r}")
        return Endpoint(host, int(port_raw))

    if isinstance(value, int):
        return Endpoint(default_host, value)

    if value is None:
        if default_port is None:
            raise ValueError("endpoint is required")
        return Endpoint(default_host, default_port)

    s = str(value).strip()
    if not s:
        raise ValueError("empty endpoint")

    if "://" in s:
        u = urlsplit(s)
        if not u.hostname or not u.port:
            raise ValueError(f"endpoint URL must include host and port: {s}")
        return Endpoint(u.hostname, int(u.port))

    if s.isdigit():
        return Endpoint(default_host, int(s))

    # IPv6 literals are intentionally not optimized here; use object syntax if needed.
    if ":" not in s:
        if default_port is None:
            raise ValueError(f"endpoint must include port: {s}")
        return Endpoint(s, default_port)

    host, port = s.rsplit(":", 1)
    host = host.strip() or default_host
    return Endpoint(host, int(port))


def normalize_path_prefix(prefix: str | None) -> str:
    if not prefix:
        return "/"
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    # Keep root as '/', trim trailing slash for stable prefix matching.
    if len(prefix) > 1:
        prefix = prefix.rstrip("/")
    return prefix


def load_config(path: str | os.PathLike[str]) -> dict[str, Any]:
    p = Path(path)
    try:
        # utf-8-sig keeps Windows-edited/example files with a BOM readable.
        with p.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"config not found: {p}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {p}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit("config root must be a JSON object")
    return data


def save_config(path: str | os.PathLike[str], data: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_routes(config: dict[str, Any]) -> list[Route]:
    raw_routes = config.get("routes")
    if not isinstance(raw_routes, list) or not raw_routes:
        raise SystemExit("config must contain a non-empty routes array")

    routes: list[Route] = []
    for i, raw in enumerate(raw_routes):
        if not isinstance(raw, dict):
            raise SystemExit(f"route #{i + 1} must be an object")
        name = str(raw.get("name") or f"route-{i + 1}")
        local = parse_endpoint(raw.get("local"), default_host="127.0.0.1")
        remote_raw = raw.get("remote_forward", raw.get("remote"))
        remote = parse_endpoint(remote_raw, default_host="127.0.0.1")
        routes.append(
            Route(
                name=name,
                local=local,
                remote_forward=remote,
                path_prefix=normalize_path_prefix(raw.get("path_prefix", "/")),
                host=str(raw["host"]).lower() if raw.get("host") else None,
                strip_path_prefix=bool(raw.get("strip_path_prefix", False)),
                target_path_prefix=normalize_path_prefix(raw.get("target_path_prefix")) if raw.get("target_path_prefix") else "",
                preserve_host=bool(raw.get("preserve_host", False)),
            )
        )
    return routes


def get_ssh_host(config: dict[str, Any], override: str | None = None) -> str:
    if override:
        return override
    ssh_cfg = config.get("ssh", {})
    if isinstance(ssh_cfg, str):
        return ssh_cfg
    if isinstance(ssh_cfg, dict) and ssh_cfg.get("host"):
        return str(ssh_cfg["host"])
    return "server"


def get_ssh_extra_args(config: dict[str, Any]) -> list[str]:
    ssh_cfg = config.get("ssh", {})
    args: list[str] = []
    if isinstance(ssh_cfg, dict):
        port = ssh_cfg.get("port")
        user = ssh_cfg.get("user")
        identity_file = ssh_cfg.get("identity_file")
        config_file = ssh_cfg.get("config_file")
        if port:
            args += ["-p", str(port)]
        if identity_file:
            args += ["-i", str(identity_file)]
        if config_file:
            args += ["-F", str(config_file)]
        # If user is configured and host is not already user@host, add it later in caller.
        if user:
            args += ["-l", str(user)]
        connect_timeout = ssh_cfg.get("connect_timeout")
        if connect_timeout is not None:
            try:
                connect_timeout_value = float(connect_timeout)
            except (TypeError, ValueError):
                raise SystemExit("ssh.connect_timeout must be a positive number of seconds")
            if connect_timeout_value <= 0:
                raise SystemExit("ssh.connect_timeout must be greater than 0")
            if connect_timeout_value.is_integer():
                connect_timeout_text = str(int(connect_timeout_value))
            else:
                connect_timeout_text = str(connect_timeout_value)
            args += ["-o", f"ConnectTimeout={connect_timeout_text}"]
        for opt in ssh_cfg.get("options", []) or []:
            # OpenSSH generally uses the first obtained value; let the explicit
            # config field take precedence over legacy options entries.
            if connect_timeout is not None and str(opt).lower().startswith("connecttimeout="):
                continue
            args += ["-o", str(opt)]
    return args


def get_ssh_keepalive(config: dict[str, Any]) -> tuple[float, int]:
    """Return SSH keepalive interval and missed-response limit."""
    ssh_cfg = config.get("ssh", {})
    if not isinstance(ssh_cfg, dict):
        ssh_cfg = {}
    interval_raw = ssh_cfg.get("server_alive_interval", DEFAULT_SSH_SERVER_ALIVE_INTERVAL)
    count_raw = ssh_cfg.get("server_alive_count_max", DEFAULT_SSH_SERVER_ALIVE_COUNT_MAX)
    try:
        interval = float(interval_raw)
        count = int(count_raw)
    except (TypeError, ValueError):
        raise SystemExit("ssh.server_alive_interval and ssh.server_alive_count_max must be numeric")
    if interval < 0 or count < 1:
        raise SystemExit("ssh.server_alive_interval must be >= 0 and ssh.server_alive_count_max must be >= 1")
    return interval, count


def ssh_base_command(config: dict[str, Any], ssh_host: str | None = None) -> list[str]:
    return ["ssh", *get_ssh_extra_args(config), get_ssh_host(config, ssh_host)]


def ssh_probe_command(config: dict[str, Any], ssh_host: str | None = None) -> list[str]:
    """Build a non-interactive SSH command for short remote probes."""
    command = ["ssh", *get_ssh_extra_args(config)]
    alive_interval, alive_count_max = get_ssh_keepalive(config)
    command += [
        "-o", "BatchMode=yes",
        "-o", "RequestTTY=no",
        "-o", "ConnectionAttempts=1",
        "-o", f"ServerAliveInterval={alive_interval:g}",
        "-o", f"ServerAliveCountMax={alive_count_max}",
    ]
    return [*command, get_ssh_host(config, ssh_host)]


def check_tcp(endpoint: Endpoint, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=timeout):
            return True, "ok"
    except OSError as exc:
        return False, str(exc)


def read_exact_body(handler: BaseHTTPRequestHandler) -> bytes | None:
    length = handler.headers.get("Content-Length")
    if length is None:
        return None
    try:
        n = int(length)
    except ValueError:
        return b""
    if n <= 0:
        return b""
    return handler.rfile.read(n)


def route_score(route: Route) -> int:
    return len(route.path_prefix or "/") + (10000 if route.host else 0)


def host_without_port(host_header: str | None) -> str:
    if not host_header:
        return ""
    h = host_header.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        return h[1:end] if end > 0 else h
    return h.rsplit(":", 1)[0] if ":" in h else h


def path_matches(path: str, prefix: str) -> bool:
    if prefix == "/":
        return True
    return path == prefix or path.startswith(prefix + "/")


def join_paths(prefix: str, suffix: str) -> str:
    if not prefix:
        return suffix or "/"
    prefix = normalize_path_prefix(prefix)
    suffix = suffix or "/"
    if suffix == "/":
        return prefix
    return posixpath.join(prefix, suffix.lstrip("/"))


def secrets_compare(a: bytes, b: bytes) -> bool:
    # Small constant-time compare without importing extra dependencies.
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= x ^ y
    return result == 0


def token_matches(provided: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    if not provided:
        return False
    return secrets_compare(provided.encode("utf-8"), expected.encode("utf-8"))


def get_auth_token(config: dict[str, Any]) -> str | None:
    """Return the configured proxy token, if any.

    Supported config shapes:
      server_proxy.auth_token = "..."
      server_proxy.auth = {"token": "..."}
      server_proxy.auth = {"token_env": "CCRP_TOKEN"}
    """
    proxy_cfg = config.get("server_proxy", {}) if isinstance(config.get("server_proxy", {}), dict) else {}
    direct = proxy_cfg.get("auth_token")
    if direct:
        return str(direct)
    auth = proxy_cfg.get("auth")
    if isinstance(auth, dict):
        if auth.get("token"):
            return str(auth["token"])
        if auth.get("token_env"):
            return os.environ.get(str(auth["token_env"]))
    return None


def get_tls_config(config: dict[str, Any]) -> dict[str, Any]:
    proxy_cfg = config.get("server_proxy", {}) if isinstance(config.get("server_proxy", {}), dict) else {}
    tls = proxy_cfg.get("tls", {})
    return tls if isinstance(tls, dict) else {}


def get_upstream_timeout(config: dict[str, Any], override: float | None = None) -> float:
    """Return the HTTP upstream connect/read inactivity timeout in seconds."""
    proxy_cfg = config.get("server_proxy", {})
    if not isinstance(proxy_cfg, dict):
        proxy_cfg = {}
    raw = override if override is not None else proxy_cfg.get("upstream_timeout", DEFAULT_UPSTREAM_TIMEOUT)
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        raise SystemExit("server_proxy.upstream_timeout must be a positive number of seconds")
    if timeout <= 0:
        raise SystemExit("server_proxy.upstream_timeout must be greater than 0")
    return timeout


def transform_path(original: str, route: Route) -> str:
    split = urlsplit(original)
    path = split.path or "/"

    if route.strip_path_prefix and route.path_prefix != "/" and path_matches(path, route.path_prefix):
        path = path[len(route.path_prefix):] or "/"
        if not path.startswith("/"):
            path = "/" + path

    if route.target_path_prefix:
        path = join_paths(route.target_path_prefix, path)

    rebuilt = SplitResult("", "", path, split.query, split.fragment)
    return urlunsplit(rebuilt)


class ReverseProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    routes: list[Route] = []
    auth_token: str | None = None
    upstream_timeout: float = DEFAULT_UPSTREAM_TIMEOUT
    server_version = f"ccrp/{VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s - %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), self.address_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        self.proxy()

    def do_HEAD(self) -> None:  # noqa: N802
        self.proxy()

    def do_POST(self) -> None:  # noqa: N802
        self.proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self.proxy()

    def do_PATCH(self) -> None:  # noqa: N802
        self.proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self.proxy()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.proxy()

    def find_route(self) -> Route | None:
        request_host = host_without_port(self.headers.get("Host"))
        request_path = urlsplit(self.path).path or "/"
        candidates: list[Route] = []
        for route in self.routes:
            if route.host and route.host != request_host:
                continue
            if not path_matches(request_path, route.path_prefix):
                continue
            candidates.append(route)
        if not candidates:
            return None
        return sorted(candidates, key=route_score, reverse=True)[0]

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def authorized(self) -> bool:
        if not self.auth_token:
            return True
        bearer = self.headers.get("Authorization", "")
        provided = None
        if bearer.lower().startswith("bearer "):
            provided = bearer[7:].strip()
        provided = provided or self.headers.get("X-CCRP-Token")
        return token_matches(provided, self.auth_token)

    def proxy(self) -> None:
        if not self.authorized():
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return

        if urlsplit(self.path).path == "/__ccrp/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "version": VERSION,
                    "routes": [
                        {
                            "name": r.name,
                            "host": r.host,
                            "path_prefix": r.path_prefix,
                            "target": str(r.remote_forward),
                        }
                        for r in self.routes
                    ],
                },
            )
            return

        route = self.find_route()
        if not route:
            self.send_json(404, {"ok": False, "error": "no route matched", "path": self.path})
            return

        body = read_exact_body(self)
        headers: dict[str, str] = {}
        connection_tokens: set[str] = set()
        for token in self.headers.get("Connection", "").split(","):
            token = token.strip().lower()
            if token:
                connection_tokens.add(token)

        for key, value in self.headers.items():
            lk = key.lower()
            if lk in HOP_BY_HOP_HEADERS or lk in connection_tokens:
                continue
            if lk == "host" and not route.preserve_host:
                continue
            headers[key] = value

        if not route.preserve_host:
            headers["Host"] = str(route.remote_forward)
        client_ip = self.client_address[0] if self.client_address else ""
        prior_xff = self.headers.get("X-Forwarded-For")
        headers["X-Forwarded-For"] = f"{prior_xff}, {client_ip}" if prior_xff else client_ip
        if self.headers.get("Host"):
            headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers.setdefault("X-Forwarded-Proto", "https" if isinstance(self.request, ssl.SSLSocket) else "http")

        target_path = transform_path(self.path, route)
        try:
            # SSH reverse ports can disappear briefly while the local tunnel
            # process is reconnecting. Retry only TCP connection establishment;
            # once connected, send the request exactly once.
            conn: http.client.HTTPConnection | None = None
            last_error: OSError | None = None
            for attempt in range(UPSTREAM_CONNECT_RETRIES):
                conn = http.client.HTTPConnection(
                    route.remote_forward.host,
                    route.remote_forward.port,
                    timeout=self.upstream_timeout,
                )
                try:
                    conn.connect()
                    last_error = None
                    break
                except OSError as exc:
                    last_error = exc
                    conn.close()
                    if attempt + 1 < UPSTREAM_CONNECT_RETRIES:
                        time.sleep(UPSTREAM_CONNECT_RETRY_DELAY)
            if last_error is not None or conn is None:
                raise last_error or OSError("upstream connection failed")
            conn.request(self.command, target_path, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            self.send_json(502, {"ok": False, "error": "upstream unavailable", "route": route.name, "detail": str(exc)})
            return

        try:
            self.send_response(resp.status, resp.reason)
            response_connection_tokens: set[str] = set()
            for token in resp.getheader("Connection", "").split(","):
                token = token.strip().lower()
                if token:
                    response_connection_tokens.add(token)
            for key, value in resp.getheaders():
                lk = key.lower()
                if lk in HOP_BY_HOP_HEADERS or lk in response_connection_tokens:
                    continue
                # BaseHTTPRequestHandler will manage connection close itself.
                self.send_header(key, value)
            self.send_header("Via", f"1.1 ccrp/{VERSION}")
            self.end_headers()
            if self.command == "HEAD":
                return
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            conn.close()


def run_server(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    routes = get_routes(config)
    proxy_cfg = config.get("server_proxy", {}) if isinstance(config.get("server_proxy", {}), dict) else {}
    listen_raw = args.listen or proxy_cfg.get("listen") or "0.0.0.0:8080"
    listen = parse_endpoint(listen_raw, default_host="0.0.0.0")
    ReverseProxyHandler.routes = routes
    ReverseProxyHandler.auth_token = get_auth_token(config)
    ReverseProxyHandler.upstream_timeout = get_upstream_timeout(config, args.upstream_timeout)
    httpd = ThreadingHTTPServer((listen.host, listen.port), ReverseProxyHandler)
    tls = get_tls_config(config)
    scheme = "http"
    if tls.get("enabled"):
        certfile = tls.get("certfile")
        keyfile = tls.get("keyfile")
        if not certfile or not keyfile:
            raise SystemExit("server_proxy.tls.enabled requires certfile and keyfile")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(certfile), str(keyfile))
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    print(f"ccrp server proxy listening on {scheme}://{listen.host}:{listen.port}")
    if ReverseProxyHandler.auth_token:
        print("  auth: enabled (Authorization: Bearer <token> or X-CCRP-Token)")
    for r in routes:
        host_part = f" host={r.host}" if r.host else ""
        print(f"  route {r.name}:{host_part} path={r.path_prefix} -> {r.remote_forward}")
    print(f"  upstream timeout: {ReverseProxyHandler.upstream_timeout:g}s")
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nserver stopping")
    finally:
        httpd.server_close()
    return 0


def build_ssh_tunnel_command(config: dict[str, Any], ssh_host: str | None = None) -> list[str]:
    routes = get_routes(config)
    alive_interval, alive_count_max = get_ssh_keepalive(config)
    cmd = [
        "ssh",
        *get_ssh_extra_args(config),
        "-N",
        "-T",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        f"ServerAliveInterval={alive_interval:g}",
        "-o",
        f"ServerAliveCountMax={alive_count_max}",
    ]
    for route in routes:
        rf = route.remote_forward
        lf = route.local
        # bind:port:host:hostport keeps the exposed reverse port on server loopback
        # by default, avoiding GatewayPorts requirements and letting the server
        # proxy decide what is public.
        spec = f"{rf.host}:{rf.port}:{lf.host}:{lf.port}"
        cmd += ["-R", spec]
    cmd.append(get_ssh_host(config, ssh_host))
    return cmd


def cmd_print_ssh(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(" ".join(shlex.quote(part) for part in build_ssh_tunnel_command(config, args.ssh)))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    routes = get_routes(config)
    ok_all = True
    print(f"ccrp {VERSION}")
    print(f"ssh host: {get_ssh_host(config, args.ssh)}")
    print("\nlocal route checks:")
    for route in routes:
        ok, msg = check_tcp(route.local, timeout=args.timeout)
        ok_all = ok_all and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {route.name} local {route.local}: {msg}")
    ssh = shutil.which("ssh")
    print(f"\nssh client: {ssh or 'not found'}")
    ok_all = ok_all and bool(ssh)
    if ssh:
        probe = [*ssh_base_command(config, args.ssh), "python3 --version && echo __ccrp_ssh_ok__"]
        try:
            p = subprocess.run(probe, text=True, capture_output=True, timeout=args.ssh_timeout)
            passed = p.returncode == 0 and "__ccrp_ssh_ok__" in p.stdout
            ok_all = ok_all and passed
            print(f"remote python: {'OK' if passed else 'FAIL'}")
            if p.stdout.strip():
                print(textwrap.indent(p.stdout.strip(), "  "))
            if p.stderr.strip():
                print(textwrap.indent(p.stderr.strip(), "  stderr: "))
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok_all = False
            print(f"remote python: FAIL ({exc})")
    return 0 if ok_all else 2


def cmd_up(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    routes = get_routes(config)
    if not args.no_check:
        failed = False
        for route in routes:
            ok, msg = check_tcp(route.local, timeout=2)
            if ok:
                print(f"[OK] local {route.name} reachable at {route.local}")
            else:
                failed = True
                print(f"[WARN] local {route.name} not reachable at {route.local}: {msg}")
        if failed and not args.allow_down:
            print("Refusing to start tunnel because at least one local route is down. Use --allow-down to override.")
            return 2

    cmd = build_ssh_tunnel_command(config, args.ssh)
    print("starting ssh reverse tunnel:")
    print("  " + " ".join(shlex.quote(part) for part in cmd))
    delay = 1.0
    stop = False

    def mark_stop(signum: int, frame: Any) -> None:  # noqa: ARG001
        nonlocal stop
        stop = True

    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGINT, mark_stop)
        signal.signal(signal.SIGTERM, mark_stop)

    while not stop:
        started = time.time()
        try:
            proc = subprocess.Popen(cmd)
        except FileNotFoundError:
            print("ssh executable not found", file=sys.stderr)
            return 127
        while proc.poll() is None and not stop:
            time.sleep(0.3)
        if stop and proc.poll() is None:
            proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
            if proc.poll() is None:
                proc.kill()
        code = proc.returncode
        if stop or args.once:
            return code or 0
        ran_for = time.time() - started
        if ran_for > 60:
            delay = 1.0
        print(f"ssh tunnel exited with code {code}; restarting in {delay:.0f}s ...", file=sys.stderr)
        time.sleep(delay)
        delay = min(delay * 2, 30)
    return 0


def remote_run(config: dict[str, Any], ssh_host: str | None, remote_cmd: str, *, input_text: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    cmd = [*ssh_base_command(config, ssh_host), remote_cmd]
    return subprocess.run(cmd, input=input_text, text=True, capture_output=True, timeout=timeout)


def require_success(proc: subprocess.CompletedProcess[str], action: str) -> None:
    if proc.returncode != 0:
        msg = f"{action} failed with code {proc.returncode}"
        if proc.stdout.strip():
            msg += "\nstdout:\n" + proc.stdout
        if proc.stderr.strip():
            msg += "\nstderr:\n" + proc.stderr
        raise SystemExit(msg)


def remote_home(config: dict[str, Any], ssh_host: str | None) -> str:
    proc = remote_run(config, ssh_host, "printf %s \"$HOME\"", timeout=20)
    require_success(proc, "detect remote home")
    home = proc.stdout.strip()
    if not home.startswith("/"):
        raise SystemExit(f"unexpected remote HOME: {home!r}")
    return home


def resolve_remote_dir(home: str, raw_dir: str | None) -> str | None:
    if not raw_dir:
        return None
    remote_dir = raw_dir.rstrip("/") or "/"
    if remote_dir == "~":
        return home
    if remote_dir.startswith("~/"):
        return home + remote_dir[1:]
    return remote_dir


def build_repository_sync_command(repository_url: str, branch: str, remote_dir: str) -> str:
    """Clone the repository or fast-forward an existing checkout in place."""
    repo_dir = shlex.quote(remote_dir)
    parent_dir = posixpath.dirname(remote_dir.rstrip("/")) or "/"
    parent = shlex.quote(parent_dir)
    url = shlex.quote(repository_url)
    branch_arg = shlex.quote(branch)
    return "\n".join(
        [
            "set -eu",
            f"repo_dir={repo_dir}",
            f"repo_url={url}",
            f"repo_branch={branch_arg}",
            f"mkdir -p {parent}",
            'if [ -d "$repo_dir/.git" ]; then',
            '  current_url=$(git -C "$repo_dir" remote get-url origin 2>/dev/null || true)',
            '  if [ "$current_url" != "$repo_url" ]; then',
            '    echo "remote deployment directory is a different Git repository" >&2',
            '    echo "expected origin: $repo_url" >&2',
            '    echo "actual origin:   ${current_url:-<missing>}" >&2',
            '    exit 3',
            '  fi',
            '  git -C "$repo_dir" fetch --prune origin "$repo_branch"',
            '  git -C "$repo_dir" checkout "$repo_branch" 2>/dev/null || git -C "$repo_dir" checkout -b "$repo_branch" "origin/$repo_branch"',
            '  git -C "$repo_dir" pull --ff-only origin "$repo_branch"',
            'elif [ -e "$repo_dir" ] && [ -n "$(find "$repo_dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then',
            '  echo "remote deployment directory exists and is not an empty Git checkout" >&2',
            "  exit 2",
            "else",
            '  git clone --branch "$repo_branch" --single-branch "$repo_url" "$repo_dir"',
            "fi",
            'printf "repository revision: "',
            'git -C "$repo_dir" rev-parse --short HEAD',
        ]
    )


def build_server_start_command(remote_dir: str, remote_config: str, session: str = "ccrp-server", *, restart: bool = True) -> str:
    """Build a remote command that starts an already-deployed server checkout."""
    quoted_dir = shlex.quote(remote_dir)
    quoted_bin = shlex.quote(posixpath.join(remote_dir.rstrip("/"), "ccrp.py"))
    quoted_config = shlex.quote(remote_config)
    quoted_session = shlex.quote(session)
    start_cmd = " ".join(
        shlex.quote(part)
        for part in ["python3", posixpath.join(remote_dir.rstrip("/"), "ccrp.py"), "server", "--config", remote_config]
    )
    stop_cmd = f"tmux kill-session -t {quoted_session} 2>/dev/null || true; " if restart else ""
    return "\n".join(
        [
            "set -eu",
            f"test -f {quoted_bin} || {{ echo 'ccrp.py not found in deployed directory' >&2; exit 2; }}",
            f"test -f {quoted_config} || {{ echo 'server config not found' >&2; exit 2; }}",
            stop_cmd + f"tmux new-session -d -s {quoted_session} -c {quoted_dir} {shlex.quote(start_cmd)}",
            f"tmux list-sessions | grep -- {quoted_session}",
        ]
    )


def deploy_server(args: argparse.Namespace) -> int:
    """Clone/update a server checkout and upload its config, without starting it."""
    config_path = Path(args.config)
    config = load_config(config_path)
    host = get_ssh_host(config, args.ssh)
    home = remote_home(config, host)
    remote_dir = resolve_remote_dir(home, args.remote_dir)
    if not remote_dir:
        raise SystemExit("deploy-server requires --remote-dir")

    print(f"deploying repository on {host}")
    print(f"  repository:    {args.repo_url} ({args.branch})")
    print(f"  remote_dir:    {remote_dir}")
    proc = remote_run(
        config,
        host,
        build_repository_sync_command(args.repo_url, args.branch, remote_dir),
        timeout=180,
    )
    require_success(proc, "clone or update repository")
    if proc.stdout.strip():
        print(proc.stdout.strip())

    remote_config = args.remote_config or posixpath.join(remote_dir, "ccrp.config.json")
    parent_dir = posixpath.dirname(remote_config) or "/"
    proc = remote_run(config, host, f"mkdir -p {shlex.quote(parent_dir)}", timeout=30)
    require_success(proc, "create remote config directory")
    config_text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    proc = remote_run(config, host, f"cat > {shlex.quote(remote_config)}", input_text=config_text, timeout=60)
    require_success(proc, "upload server config")
    print(f"remote_config:  {remote_config}")
    print("deployment complete; server was not started")
    return 0


def start_server(args: argparse.Namespace) -> int:
    """Start/restart a server checkout that was deployed previously."""
    config = load_config(Path(args.config))
    host = get_ssh_host(config, args.ssh)
    home = remote_home(config, host)
    remote_dir = resolve_remote_dir(home, args.remote_dir)
    if not remote_dir:
        raise SystemExit("start-server requires --remote-dir")
    remote_config = args.remote_config or posixpath.join(remote_dir, "ccrp.config.json")
    session = args.tmux_session
    command = build_server_start_command(remote_dir, remote_config, session, restart=args.restart)
    print(f"starting server service on {host}")
    print(f"  remote_dir:    {remote_dir}")
    print(f"  remote_config: {remote_config}")
    print(f"  tmux_session:  {session}")
    proc = remote_run(config, host, command, timeout=60)
    require_success(proc, "start server service")
    if proc.stdout.strip():
        print(proc.stdout.strip())
    print("server service started")
    return 0


def install_server(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = load_config(config_path)
    host = get_ssh_host(config, args.ssh)
    home = remote_home(config, host)
    remote_dir = resolve_remote_dir(home, args.remote_dir)
    if remote_dir:
        print(f"  repository:    {args.repo_url} ({args.branch})")
        sync_cmd = build_repository_sync_command(args.repo_url, args.branch, remote_dir)
        proc = remote_run(config, host, sync_cmd, timeout=180)
        require_success(proc, "clone or update repository")
        if proc.stdout.strip():
            print(proc.stdout.strip())
    remote_bin = args.remote_bin or (f"{remote_dir}/ccrp.py" if remote_dir else f"{home}/.local/bin/ccrp.py")
    remote_config = args.remote_config or (f"{remote_dir}/ccrp.config.json" if remote_dir else f"{home}/.config/ccrp/config.json")

    print(f"installing server helper on {host}")
    print(f"  remote_bin:    {remote_bin}")
    print(f"  remote_config: {remote_config}")

    mkdir_cmd = "mkdir -p " + " ".join(shlex.quote(str(Path(p).parent).replace('\\\\', '/')) for p in [remote_bin, remote_config])
    proc = remote_run(config, host, mkdir_cmd, timeout=30)
    require_success(proc, "create remote directories")

    if not remote_dir:
        script_text = Path(__file__).read_text(encoding="utf-8")
        proc = remote_run(config, host, f"cat > {shlex.quote(remote_bin)} && chmod 755 {shlex.quote(remote_bin)}", input_text=script_text, timeout=60)
        require_success(proc, "upload ccrp.py")

    # Store a copy of the JSON config for the server proxy. The local-only fields
    # are harmless; the server command uses routes[*].remote_forward and
    # server_proxy.listen.
    config_text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    proc = remote_run(config, host, f"cat > {shlex.quote(remote_config)}", input_text=config_text, timeout=60)
    require_success(proc, "upload config")

    if args.tmux:
        session = args.tmux_session
        start_cmd = " ".join(shlex.quote(part) for part in ["python3", remote_bin, "server", "--config", remote_config])
        tmux_cmd = (
            f"tmux has-session -t {shlex.quote(session)} 2>/dev/null && "
            f"tmux kill-session -t {shlex.quote(session)}; "
            f"tmux new-session -d -s {shlex.quote(session)} {shlex.quote(start_cmd)} && "
            f"tmux list-sessions | grep -- {shlex.quote(session)}"
        )
        proc = remote_run(config, host, tmux_cmd, timeout=30)
        require_success(proc, "start tmux session")
        print(proc.stdout)
        print("tmux server started. Useful remote commands:")
        print(f"  ssh {shlex.quote(host)} {shlex.quote('tmux attach -t ' + session)}")
        print(f"  ssh {shlex.quote(host)} {shlex.quote('tmux kill-session -t ' + session)}")
    elif args.systemd_user:
        unit_dir = f"{home}/.config/systemd/user"
        unit_path = f"{unit_dir}/ccrp-server.service"
        service = textwrap.dedent(
            f"""
            [Unit]
            Description=ccrp server-side HTTP reverse proxy
            After=network-online.target

            [Service]
            Type=simple
            ExecStart=/usr/bin/env python3 {remote_bin} server --config {remote_config}
            Restart=always
            RestartSec=3
            WorkingDirectory={home}

            [Install]
            WantedBy=default.target
            """
        ).lstrip()
        proc = remote_run(config, host, f"mkdir -p {shlex.quote(unit_dir)} && cat > {shlex.quote(unit_path)}", input_text=service, timeout=30)
        require_success(proc, "upload systemd unit")
        if not args.no_start:
            proc = remote_run(config, host, "systemctl --user daemon-reload && systemctl --user enable --now ccrp-server.service && systemctl --user --no-pager status ccrp-server.service", timeout=60)
            if proc.returncode != 0:
                print("systemd user service could not be started automatically.", file=sys.stderr)
                if proc.stdout.strip():
                    print(proc.stdout, file=sys.stderr)
                if proc.stderr.strip():
                    print(proc.stderr, file=sys.stderr)
                print("You can run the server manually with:")
                print(f"  ssh {shlex.quote(host)} {shlex.quote('python3 ' + remote_bin + ' server --config ' + remote_config)}")
                return 3
            print(proc.stdout)
    else:
        print("server helper uploaded. Start it on the server with:")
        print(f"  python3 {remote_bin} server --config {remote_config}")

    print("done. Keep the local SSH reverse tunnel running with:")
    print(f"  python {Path(__file__).name} up -c {config_path}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing file: {out} (use --force)")
    config = {
        "ssh": {
            "host": args.ssh,
            "connect_timeout": args.ssh_connect_timeout,
            "server_alive_interval": DEFAULT_SSH_SERVER_ALIVE_INTERVAL,
            "server_alive_count_max": DEFAULT_SSH_SERVER_ALIVE_COUNT_MAX,
        },
        "server_proxy": {
            "listen": args.listen,
            "upstream_timeout": args.upstream_timeout,
        },
        "routes": [
            {
                "name": args.name,
                "local": args.local,
                "remote_forward": f"127.0.0.1:{args.remote_port}",
                "path_prefix": args.path_prefix,
                "strip_path_prefix": args.strip_path_prefix,
                "target_path_prefix": args.target_path_prefix,
                "preserve_host": False,
            }
        ],
    }
    save_config(out, config)
    print(f"wrote {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccrp",
        description="Expose local cc-switch HTTP routes on a server through SSH reverse tunnels.",
    )
    parser.add_argument("--version", action="version", version=f"ccrp {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write a starter JSON config")
    p.add_argument("--out", default=DEFAULT_CONFIG, help="output config path")
    p.add_argument("--force", action="store_true", help="overwrite existing config")
    p.add_argument("--ssh", default="server", help="SSH host alias, e.g. my-server or h102")
    p.add_argument("--ssh-connect-timeout", type=float, default=10.0, help="SSH connection timeout in seconds")
    p.add_argument("--name", default="cc-switch", help="route name")
    p.add_argument("--local", default="127.0.0.1:3456", help="local cc-switch address, host:port or URL")
    p.add_argument("--remote-port", type=int, default=18080, help="server loopback port used by SSH -R")
    p.add_argument("--listen", default="127.0.0.1:8080", help="server proxy listen address; keep 127.0.0.1 for private-only access")
    p.add_argument("--path-prefix", default="/", help="public path prefix")
    p.add_argument("--strip-path-prefix", action="store_true", help="strip public prefix before proxying upstream")
    p.add_argument("--target-path-prefix", default="", help="prefix added to upstream requests")
    p.add_argument("--upstream-timeout", type=float, default=DEFAULT_UPSTREAM_TIMEOUT, help="server upstream timeout in seconds")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="check local route, ssh, and remote Python")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.add_argument("--ssh", help="override SSH host")
    p.add_argument("--timeout", type=float, default=2.0, help="local TCP timeout")
    p.add_argument("--ssh-timeout", type=int, default=20, help="SSH probe timeout")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("print-ssh", help="print the ssh -R command without running it")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.add_argument("--ssh", help="override SSH host")
    p.set_defaults(func=cmd_print_ssh)

    p = sub.add_parser("up", help="run the local SSH reverse tunnel in the foreground")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.add_argument("--ssh", help="override SSH host")
    p.add_argument("--once", action="store_true", help="do not restart if ssh exits")
    p.add_argument("--no-check", action="store_true", help="skip local TCP checks")
    p.add_argument("--allow-down", action="store_true", help="start even if local route check fails")
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("server", help="run the server-side HTTP reverse proxy")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.add_argument("--listen", help="override server_proxy.listen")
    p.add_argument("--upstream-timeout", type=float, help="override server_proxy.upstream_timeout in seconds")
    p.set_defaults(func=run_server)

    p = sub.add_parser("deploy-server", help="clone or update the server repository and upload config")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.add_argument("--ssh", help="override SSH host")
    p.add_argument("--remote-dir", required=True, help="remote repository directory; supports ~/...")
    p.add_argument("--remote-config", help="remote config path; defaults to <remote-dir>/ccrp.config.json")
    p.add_argument("--repo-url", default=DEFAULT_REPOSITORY_URL, help="repository URL to clone on the server")
    p.add_argument("--branch", default=DEFAULT_REPOSITORY_BRANCH, help="repository branch to clone or update")
    p.set_defaults(func=deploy_server)

    p = sub.add_parser("start-server", help="start or restart the deployed server service")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.add_argument("--ssh", help="override SSH host")
    p.add_argument("--remote-dir", required=True, help="remote repository directory; supports ~/...")
    p.add_argument("--remote-config", help="remote config path; defaults to <remote-dir>/ccrp.config.json")
    p.add_argument("--tmux-session", default="ccrp-server", help="tmux session name")
    p.add_argument("--no-restart", dest="restart", action="store_false", help="fail if the tmux session already exists")
    p.set_defaults(func=start_server, restart=True)

    p = sub.add_parser("install-server", help="legacy combined server deployment command")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.add_argument("--ssh", help="override SSH host")
    p.add_argument("--remote-bin", help="remote ccrp.py path")
    p.add_argument("--remote-config", help="remote config path")
    p.add_argument("--remote-dir", help="remote repository directory; supports ~/...")
    p.add_argument("--repo-url", default=DEFAULT_REPOSITORY_URL, help="repository URL to clone on the server")
    p.add_argument("--branch", default=DEFAULT_REPOSITORY_BRANCH, help="repository branch to clone or update")
    p.add_argument("--tmux", action="store_true", help="install and start the server proxy in a tmux session")
    p.add_argument("--tmux-session", default="ccrp-server", help="tmux session name for --tmux")
    p.add_argument("--systemd-user", action="store_true", help="install and start a user systemd service")
    p.add_argument("--no-start", action="store_true", help="with --systemd-user, only install unit; do not start")
    p.set_defaults(func=install_server)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
