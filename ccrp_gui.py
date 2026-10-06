"""Windows Tkinter control panel for ccrp.

The GUI deliberately uses the existing dependency-free ccrp.py commands.  It
does not keep SSH passwords or private keys; authentication remains delegated to
the user's OpenSSH configuration and agent.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import ccrp


def default_ssh_config() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".ssh" / "config"


def read_ssh_hosts(path: str | os.PathLike[str]) -> list[str]:
    """Read concrete Host aliases from an OpenSSH config file."""
    hosts: list[str] = []
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8-sig")
    except (FileNotFoundError, OSError):
        return hosts
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[0].lower() != "host":
            continue
        for alias in parts[1:]:
            if alias and not any(mark in alias for mark in "*!?") and alias not in hosts:
                hosts.append(alias)
    return hosts


def port_value(value: str, label: str) -> int:
    try:
        port = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label}必须是 1 到 65535 之间的整数") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{label}必须是 1 到 65535 之间的整数")
    return port


def positive_float(value: str, label: str) -> float:
    try:
        number = float(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label}必须是大于 0 的数字") from exc
    if number <= 0:
        raise ValueError(f"{label}必须是大于 0 的数字")
    return number


def nonnegative_float(value: str, label: str) -> float:
    try:
        number = float(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label}必须是大于等于 0 的数字") from exc
    if number < 0:
        raise ValueError(f"{label}必须是大于等于 0 的数字")
    return number


class CcrpGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("CCRP SSH 反向代理")
        self.root.geometry("980x760")
        self.root.minsize(860, 650)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.tunnel_process: subprocess.Popen[str] | None = None
        self.monitor_stop = threading.Event()
        self.monitor_inflight = False
        self.worker_running = False

        self.ssh_file = tk.StringVar(value=str(default_ssh_config()))
        self.host = tk.StringVar()
        self.remote_dir = tk.StringVar(value="~/software/ccrp")
        self.config_path = tk.StringVar(value=str(Path.cwd() / "ccrp.gui.json"))
        self.local_host = tk.StringVar(value="127.0.0.1")
        self.local_port = tk.StringVar(value="15721")
        self.ssh_port = tk.StringVar(value="18082")
        self.server_port = tk.StringVar(value="18083")
        self.upstream_timeout = tk.StringVar(value="300")
        self.ssh_connect_timeout = tk.StringVar(value="10")
        self.server_alive_interval = tk.StringVar(value="30")
        self.server_alive_count = tk.StringVar(value="3")
        self.tunnel_status = tk.StringVar(value="未启动")
        self.server_status = tk.StringVar(value="未检查")
        self.port_status = tk.StringVar(value="未检查")
        self.overall_status = tk.StringVar(value="未运行")

        self.build_ui()
        self.load_hosts()
        self.root.after(200, self.process_events)
        self.root.after(1000, self.monitor_tick)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def ccrp_command(self, *arguments: str) -> list[str]:
        """Build a command for the source checkout or the packaged build."""
        if getattr(sys, "frozen", False):
            cli_path = Path(sys.executable).resolve().with_name("ccrp.exe")
            if not cli_path.is_file():
                raise OSError(f"未找到命令行组件：{cli_path}。请将 ccrp.exe 与 ccrp-gui.exe 放在同一目录")
            return [str(cli_path), *arguments]
        return [sys.executable, str(Path(__file__).resolve().parent / "ccrp.py"), *arguments]

    def runtime_directory(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent

    def build_ui(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        ssh_frame = ttk.LabelFrame(root, text="1. SSH 目标")
        ssh_frame.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="ew")
        ssh_frame.columnconfigure(1, weight=1)
        ttk.Label(ssh_frame, text="SSH 配置文件").grid(row=0, column=0, padx=6, pady=5, sticky="w")
        ttk.Entry(ssh_frame, textvariable=self.ssh_file).grid(row=0, column=1, padx=6, pady=5, sticky="ew")
        ttk.Button(ssh_frame, text="选择文件", command=self.choose_ssh_file).grid(row=0, column=2, padx=4, pady=5)
        ttk.Button(ssh_frame, text="读取目标", command=self.load_hosts).grid(row=0, column=3, padx=6, pady=5)
        ttk.Label(ssh_frame, text="目标服务器").grid(row=1, column=0, padx=6, pady=5, sticky="w")
        self.host_combo = ttk.Combobox(ssh_frame, textvariable=self.host, state="normal", width=30)
        self.host_combo.grid(row=1, column=1, padx=6, pady=5, sticky="w")
        ttk.Button(ssh_frame, text="测试 SSH", command=self.test_ssh).grid(row=1, column=2, padx=4, pady=5)
        ttk.Label(ssh_frame, text="支持 Host 别名、User、Port、IdentityFile 等配置").grid(row=1, column=3, padx=6, pady=5, sticky="w")

        deploy_frame = ttk.LabelFrame(root, text="2. 服务器部署目录")
        deploy_frame.grid(row=1, column=0, padx=10, pady=6, sticky="ew")
        deploy_frame.columnconfigure(1, weight=1)
        ttk.Label(deploy_frame, text="远程文件夹").grid(row=0, column=0, padx=6, pady=5, sticky="w")
        ttk.Entry(deploy_frame, textvariable=self.remote_dir).grid(row=0, column=1, padx=6, pady=5, sticky="ew")
        ttk.Button(deploy_frame, text="读取服务器目录", command=self.load_remote_dirs).grid(row=0, column=2, padx=4, pady=5)
        ttk.Button(deploy_frame, text="部署/更新服务", command=self.deploy).grid(row=0, column=3, padx=4, pady=5)
        ttk.Button(deploy_frame, text="启动服务", command=self.start_server).grid(row=0, column=4, padx=4, pady=5)
        ttk.Label(deploy_frame, text="可输入 ~/your_path，也可从服务器家目录选择").grid(row=1, column=1, columnspan=4, padx=6, pady=(0, 5), sticky="w")
        ttk.Label(deploy_frame, text="仓库地址").grid(row=2, column=0, padx=6, pady=5, sticky="w")
        self.repo_url = tk.StringVar(value=ccrp.DEFAULT_REPOSITORY_URL)
        ttk.Entry(deploy_frame, textvariable=self.repo_url).grid(row=2, column=1, padx=6, pady=5, sticky="ew")
        ttk.Label(deploy_frame, text="分支").grid(row=2, column=2, padx=6, pady=5, sticky="e")
        self.repo_branch = tk.StringVar(value=ccrp.DEFAULT_REPOSITORY_BRANCH)
        ttk.Entry(deploy_frame, textvariable=self.repo_branch, width=14).grid(row=2, column=3, padx=6, pady=5, sticky="w")

        settings = ttk.LabelFrame(root, text="3. 端口与超时")
        settings.grid(row=2, column=0, padx=10, pady=6, sticky="ew")
        for col in range(8):
            settings.columnconfigure(col, weight=1 if col % 2 else 0)
        fields = [
            ("本地 cc-switch 主机", self.local_host),
            ("本地 cc-switch 端口", self.local_port),
            ("SSH 反向端口", self.ssh_port),
            ("服务器代理端口", self.server_port),
            ("上游等待秒数", self.upstream_timeout),
            ("SSH 建连秒数", self.ssh_connect_timeout),
            ("SSH 保活间隔", self.server_alive_interval),
            ("保活失败次数", self.server_alive_count),
        ]
        for index, (label, variable) in enumerate(fields):
            row, col = divmod(index, 4)
            base = col * 2
            ttk.Label(settings, text=label).grid(row=row, column=base, padx=(6, 3), pady=5, sticky="w")
            ttk.Entry(settings, textvariable=variable, width=13).grid(row=row, column=base + 1, padx=(0, 8), pady=5, sticky="ew")

        actions = ttk.Frame(root)
        actions.grid(row=3, column=0, padx=10, pady=(4, 6), sticky="nsew")
        actions.columnconfigure(0, weight=1)
        actions.rowconfigure(1, weight=1)
        button_row = ttk.Frame(actions)
        button_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(button_row, text="保存配置", command=self.save_config).pack(side="left", padx=(0, 6))
        ttk.Button(button_row, text="启动本地隧道", command=self.start_tunnel).pack(side="left", padx=6)
        ttk.Button(button_row, text="停止本地隧道", command=self.stop_tunnel).pack(side="left", padx=6)
        ttk.Button(button_row, text="重启服务器服务", command=self.start_server).pack(side="left", padx=6)
        ttk.Button(button_row, text="立即检查", command=self.check_now).pack(side="left", padx=6)
        ttk.Label(button_row, text="配置文件").pack(side="left", padx=(18, 4))
        ttk.Entry(button_row, textvariable=self.config_path, width=35).pack(side="left", fill="x", expand=True)
        ttk.Button(button_row, text="...", width=3, command=self.choose_config_path).pack(side="left", padx=(4, 0))

        log_frame = ttk.LabelFrame(actions, text="运行日志")
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)

        status = ttk.LabelFrame(root, text="4. 运行监控")
        status.grid(row=4, column=0, padx=10, pady=(6, 10), sticky="ew")
        for col in range(4):
            status.columnconfigure(col, weight=1)
        for col, (label, variable) in enumerate([
            ("总体", self.overall_status),
            ("本地隧道", self.tunnel_status),
            ("服务器健康", self.server_status),
            ("服务器端口", self.port_status),
        ]):
            ttk.Label(status, text=label).grid(row=0, column=col, padx=6, pady=(5, 0))
            ttk.Label(status, textvariable=variable).grid(row=1, column=col, padx=6, pady=(0, 6))

    def log_line(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def choose_ssh_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 SSH 配置文件",
            initialdir=str(Path(self.ssh_file.get()).expanduser().parent),
            filetypes=[("SSH config", "config"), ("所有文件", "*.*")],
        )
        if path:
            self.ssh_file.set(path)
            self.load_hosts()

    def load_hosts(self) -> None:
        hosts = read_ssh_hosts(self.ssh_file.get())
        self.host_combo["values"] = hosts
        if hosts and self.host.get() not in hosts:
            self.host.set(hosts[0])
        self.log_line(f"读取到 {len(hosts)} 个 SSH 目标")
        if not hosts:
            self.log_line("没有读取到 Host 别名，可直接在目标服务器框中输入别名")

    def choose_config_path(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存 CCRP 配置",
            initialfile=Path(self.config_path.get()).name,
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self.config_path.set(path)

    def values_to_config(self) -> dict[str, Any]:
        host = self.host.get().strip()
        if not host:
            raise ValueError("请先选择或输入 SSH 目标服务器")
        local_host = self.local_host.get().strip() or "127.0.0.1"
        local_port = port_value(self.local_port.get(), "本地 cc-switch 端口")
        ssh_port = port_value(self.ssh_port.get(), "SSH 反向端口")
        server_port = port_value(self.server_port.get(), "服务器代理端口")
        upstream = positive_float(self.upstream_timeout.get(), "上游等待秒数")
        connect = positive_float(self.ssh_connect_timeout.get(), "SSH 建连秒数")
        alive_interval = nonnegative_float(self.server_alive_interval.get(), "SSH 保活间隔")
        alive_count = port_value(self.server_alive_count.get(), "保活失败次数")
        if alive_count > 100:
            raise ValueError("保活失败次数不能超过 100")
        ssh: dict[str, Any] = {
            "host": host,
            "connect_timeout": connect,
            "server_alive_interval": alive_interval,
            "server_alive_count_max": alive_count,
        }
        ssh_file = self.ssh_file.get().strip()
        if ssh_file:
            ssh["config_file"] = str(Path(ssh_file).expanduser())
        return {
            "ssh": ssh,
            "server_proxy": {
                "listen": f"127.0.0.1:{server_port}",
                "upstream_timeout": upstream,
            },
            "routes": [{
                "name": "cc-switch",
                "local": f"{local_host}:{local_port}",
                "remote_forward": f"127.0.0.1:{ssh_port}",
                "path_prefix": "/",
                "strip_path_prefix": False,
                "target_path_prefix": "",
                "preserve_host": False,
            }],
        }

    def save_config(self, show_message: bool = True) -> Path:
        config = self.values_to_config()
        path = Path(self.config_path.get()).expanduser()
        ccrp.save_config(path, config)
        self.log_line(f"已保存配置：{path}")
        if show_message:
            messagebox.showinfo("保存成功", f"配置已保存到：\n{path}")
        return path

    def command_config(self) -> dict[str, Any]:
        config = self.values_to_config()
        return config

    def ssh_command(self, remote_command: str) -> list[str]:
        config = self.command_config()
        return [*ccrp.ssh_base_command(config), remote_command]

    def run_async(self, title: str, function: Callable[[], Any]) -> None:
        if self.worker_running:
            messagebox.showwarning("操作进行中", "请等待当前 SSH 操作完成")
            return
        self.worker_running = True
        self.log_line(title)

        def worker() -> None:
            try:
                result = function()
                self.events.put(("result", result))
            except Exception as exc:  # noqa: BLE001 - show operational errors in GUI
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("worker_done", None))

        threading.Thread(target=worker, daemon=True).start()

    def run_stream_command(self, command: list[str], title: str) -> None:
        self.log_line("执行：" + subprocess.list2cmdline(command))
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.runtime_directory()),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise RuntimeError(f"无法启动 {title}：{exc}") from exc
        assert process.stdout is not None
        for line in process.stdout:
            self.events.put(("log", line.rstrip()))
        code = process.wait()
        if code:
            raise RuntimeError(f"{title}失败，退出码 {code}")
        self.events.put(("log", f"{title}完成"))

    def deploy(self) -> None:
        try:
            path = self.save_config(False)
            remote_dir = self.remote_dir.get().strip()
            if not remote_dir:
                raise ValueError("请填写服务器部署文件夹")
            repo_url = self.repo_url.get().strip()
            repo_branch = self.repo_branch.get().strip()
            if not repo_url or not repo_branch:
                raise ValueError("请填写仓库地址和分支")
            command = [
                *self.ccrp_command("deploy-server"),
                "-c", str(path),
                "--remote-dir", remote_dir,
                "--repo-url", repo_url,
                "--branch", repo_branch,
            ]
        except (ValueError, OSError) as exc:
            messagebox.showerror("配置错误", str(exc))
            return
        self.run_async("开始部署/更新服务器端 CCRP（不会启动服务）", lambda: self.run_stream_command(command, "服务器部署/更新"))

    def test_ssh(self) -> None:
        try:
            command = self.ssh_command("python3 --version && echo __ccrp_ssh_ok__")
        except (ValueError, OSError) as exc:
            messagebox.showerror("配置错误", str(exc))
            return
        self.run_async("测试 SSH 连接", lambda: self.run_stream_command(command, "SSH 测试"))

    def load_remote_dirs(self) -> None:
        try:
            command = self.ssh_command("printf '__CCRP_HOME__%s\\n' \"$HOME\"; find \"$HOME\" -mindepth 1 -maxdepth 3 -type d -print 2>/dev/null | sort")
        except (ValueError, OSError) as exc:
            messagebox.showerror("配置错误", str(exc))
            return

        def query() -> list[str]:
            proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if proc.returncode:
                raise RuntimeError(proc.stderr.strip() or f"SSH 退出码 {proc.returncode}")
            values: list[str] = []
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("__CCRP_HOME__"):
                    values.append("~")
                else:
                    values.append(line)
            return values

        self.run_async("读取服务器家目录", query)

    def show_remote_dirs(self, values: list[str]) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("选择服务器部署目录")
        dialog.geometry("500x360")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="双击目录或选中后点击使用").pack(anchor="w", padx=10, pady=8)
        frame = ttk.Frame(dialog)
        frame.pack(fill="both", expand=True, padx=10)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        box = tk.Listbox(frame)
        box.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=box.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        box.configure(yscrollcommand=scroll.set)
        for value in values:
            box.insert("end", value)

        def use_selected(_event: Any = None) -> None:
            selection = box.curselection()
            if selection:
                self.remote_dir.set(box.get(selection[0]))
                dialog.destroy()

        box.bind("<Double-Button-1>", use_selected)
        ttk.Button(dialog, text="使用选中目录", command=use_selected).pack(side="right", padx=10, pady=8)
        ttk.Button(dialog, text="取消", command=dialog.destroy).pack(side="right", pady=8)

    def start_tunnel(self) -> None:
        if self.tunnel_process and self.tunnel_process.poll() is None:
            self.log_line("本地隧道已经在运行")
            return
        try:
            path = self.save_config(False)
        except (ValueError, OSError) as exc:
            messagebox.showerror("配置错误", str(exc))
            return
        command = self.ccrp_command("up", "-c", str(path))
        self.log_line("启动本地 SSH 反向隧道：" + subprocess.list2cmdline(command))
        try:
            self.tunnel_process = subprocess.Popen(
                command,
                cwd=str(self.runtime_directory()),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            messagebox.showerror("启动失败", str(exc))
            self.tunnel_process = None
            return
        threading.Thread(target=self.read_tunnel_output, args=(self.tunnel_process,), daemon=True).start()

    def read_tunnel_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is not None:
            for line in process.stdout:
                self.events.put(("log", line.rstrip()))
        code = process.wait()
        self.events.put(("tunnel_exit", code))

    def stop_tunnel(self) -> None:
        process = self.tunnel_process
        if process and process.poll() is None:
            if os.name == "nt":
                # ccrp.py owns an ssh child process; terminate the whole tree
                # so the reverse port is not left open after clicking Stop.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            else:
                process.terminate()
            self.log_line("已请求停止本地隧道")
        else:
            self.log_line("本地隧道当前未运行")

    def start_server(self) -> None:
        try:
            path = self.save_config(False)
            remote_dir = self.remote_dir.get().strip().rstrip("/")
            if not remote_dir:
                raise ValueError("请填写服务器部署文件夹")
            command = [
                *self.ccrp_command("start-server"),
                "-c", str(path),
                "--remote-dir", remote_dir,
            ]
        except (ValueError, OSError) as exc:
            messagebox.showerror("配置错误", str(exc))
            return
        self.run_async("启动/重启服务器端 CCRP（不更新代码）", lambda: self.run_stream_command(command, "服务器服务启动"))

    def restart_server(self) -> None:
        """Backward-compatible alias for callers that used the old method name."""
        self.start_server()

    def remote_status(self) -> tuple[bool, str, str, bool]:
        config = self.command_config()
        server_port = port_value(self.server_port.get(), "服务器代理端口")
        ssh_port = port_value(self.ssh_port.get(), "SSH 反向端口")
        timeout = max(5, int(positive_float(self.ssh_connect_timeout.get(), "SSH 建连秒数") + 10))
        health_command = f"curl -fsS --max-time 8 http://127.0.0.1:{server_port}/__ccrp/health"
        proc = subprocess.run([*ccrp.ssh_base_command(config), health_command], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        health_ok = proc.returncode == 0
        health = "正常" if health_ok else (proc.stderr.strip() or f"失败({proc.returncode})")
        ports_command = f"ss -lnt 2>/dev/null | grep -E ':{ssh_port} |:{server_port} ' || true"
        ports = subprocess.run([*ccrp.ssh_base_command(config), ports_command], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        lines = [line for line in ports.stdout.splitlines() if line.strip()]
        port_ok = any(f":{ssh_port}" in line for line in lines) and any(f":{server_port}" in line for line in lines)
        tunnel_ok = bool(self.tunnel_process and self.tunnel_process.poll() is None)
        return tunnel_ok, health, ("正常" if port_ok else "缺少配置端口中的一个或多个监听"), health_ok and port_ok

    def check_now(self) -> None:
        self.run_async("检查服务器状态", self.remote_status)

    def monitor_tick(self) -> None:
        if not self.monitor_stop.is_set() and not self.worker_running and not self.monitor_inflight:
            self.monitor_inflight = True
            threading.Thread(target=self.monitor_worker, daemon=True).start()
        self.root.after(5000, self.monitor_tick)

    def monitor_worker(self) -> None:
        tunnel_ok = bool(self.tunnel_process and self.tunnel_process.poll() is None)
        try:
            _tunnel_ok, health, ports, remote_ok = self.remote_status()
        except Exception as exc:  # noqa: BLE001
            health, ports, remote_ok = f"检查失败：{exc}", "检查失败", False
        self.events.put(("status", (tunnel_ok, health, ports, remote_ok)))
        self.events.put(("monitor_done", None))

    def process_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log_line(str(payload))
            elif kind == "error":
                self.log_line("错误：" + str(payload))
                messagebox.showerror("操作失败", str(payload))
            elif kind == "result":
                if isinstance(payload, list):
                    self.show_remote_dirs(payload)
                elif isinstance(payload, tuple) and len(payload) == 4:
                    self.update_status(payload)
            elif kind == "status":
                self.update_status(payload)
            elif kind == "tunnel_exit":
                self.log_line(f"本地隧道退出，退出码 {payload}")
            elif kind == "monitor_done":
                self.monitor_inflight = False
            elif kind == "worker_done":
                self.worker_running = False
        self.root.after(200, self.process_events)

    def update_status(self, values: tuple[bool, str, str, bool]) -> None:
        tunnel_ok, health, ports, remote_ok = values
        self.tunnel_status.set("运行中" if tunnel_ok else "未运行")
        self.server_status.set(health)
        self.port_status.set(ports)
        self.overall_status.set("正常运行" if tunnel_ok and remote_ok else "异常或未启动")

    def close(self) -> None:
        self.monitor_stop.set()
        self.stop_tunnel()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    CcrpGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
