import json
import tempfile
import unittest
from pathlib import Path

import ccrp
import ccrp_gui


class CcrpConfigTests(unittest.TestCase):
    def test_load_config_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text('\ufeff{"routes": [{"local": "127.0.0.1:1", "remote_forward": "127.0.0.1:2"}]}', encoding="utf-8")
            data = ccrp.load_config(path)
            self.assertIn("routes", data)

    def test_default_ssh_host_is_generic(self):
        self.assertEqual(ccrp.get_ssh_host({}), "server")

    def test_transform_path_strip_and_target_prefix(self):
        route = ccrp.Route(
            name="api",
            local=ccrp.Endpoint("127.0.0.1", 1),
            remote_forward=ccrp.Endpoint("127.0.0.1", 2),
            path_prefix="/cc",
            strip_path_prefix=True,
            target_path_prefix="/v1",
        )
        self.assertEqual(ccrp.transform_path("/cc/chat?q=1", route), "/v1/chat?q=1")

    def test_token_matching(self):
        self.assertTrue(ccrp.token_matches("secret", "secret"))
        self.assertFalse(ccrp.token_matches("wrong", "secret"))
        self.assertTrue(ccrp.token_matches(None, None))

    def test_build_ssh_tunnel_binds_remote_loopback(self):
        config = {
            "ssh": {
                "host": "my-server",
                "connect_timeout": 25,
                "server_alive_interval": 45,
                "server_alive_count_max": 5,
            },
            "routes": [{"local": "127.0.0.1:3456", "remote_forward": "127.0.0.1:18080"}],
        }
        cmd = ccrp.build_ssh_tunnel_command(config)
        self.assertIn("-R", cmd)
        self.assertIn("127.0.0.1:18080:127.0.0.1:3456", cmd)
        self.assertEqual(cmd[-1], "my-server")
        self.assertIn("ConnectTimeout=25", cmd)
        self.assertIn("ServerAliveInterval=45", cmd)
        self.assertIn("ServerAliveCountMax=5", cmd)

    def test_timeout_config_defaults_and_values(self):
        self.assertEqual(ccrp.get_upstream_timeout({}), 300.0)
        self.assertEqual(
            ccrp.get_upstream_timeout({"server_proxy": {"upstream_timeout": 125}}),
            125.0,
        )
        self.assertEqual(
            ccrp.get_ssh_keepalive({"ssh": {"server_alive_interval": 12, "server_alive_count_max": 2}}),
            (12.0, 2),
        )
        self.assertEqual(ccrp.UPSTREAM_CONNECT_RETRIES, 3)

    def test_legacy_connect_timeout_option_is_preserved(self):
        config = {
            "ssh": {"host": "my-server", "options": ["ConnectTimeout=7"]},
            "routes": [{"local": "127.0.0.1:3456", "remote_forward": "127.0.0.1:18080"}],
        }
        cmd = ccrp.build_ssh_tunnel_command(config)
        self.assertIn("ConnectTimeout=7", cmd)

    def test_explicit_connect_timeout_overrides_legacy_option(self):
        config = {
            "ssh": {
                "host": "my-server",
                "connect_timeout": 25,
                "options": ["ConnectTimeout=7"],
            },
            "routes": [{"local": "127.0.0.1:3456", "remote_forward": "127.0.0.1:18080"}],
        }
        cmd = ccrp.build_ssh_tunnel_command(config)
        self.assertIn("ConnectTimeout=25", cmd)
        self.assertNotIn("ConnectTimeout=7", cmd)

    def test_ssh_config_file_is_passed_to_open_ssh(self):
        config = {
            "ssh": {"host": "my-server", "config_file": "C:/Users/test/.ssh/config"},
            "routes": [{"local": "127.0.0.1:3456", "remote_forward": "127.0.0.1:18080"}],
        }
        self.assertIn("-F", ccrp.ssh_base_command(config))
        self.assertIn("C:/Users/test/.ssh/config", ccrp.ssh_base_command(config))

    def test_ssh_probe_command_is_noninteractive(self):
        command = ccrp.ssh_probe_command(
            {"ssh": {"host": "my-server", "server_alive_interval": 12, "server_alive_count_max": 2}},
            "my-server",
        )
        self.assertIn("BatchMode=yes", command)
        self.assertIn("RequestTTY=no", command)
        self.assertIn("ConnectionAttempts=1", command)
        self.assertIn("ServerAliveInterval=12", command)
        self.assertIn("ServerAliveCountMax=2", command)
        self.assertEqual(command[-1], "my-server")

    def test_listening_port_parser_requires_listen_and_exact_port(self):
        self.assertTrue(ccrp_gui.CcrpGui._listening_port("LISTEN 0 5 127.0.0.1:18082 0.0.0.0:*", 18082))
        self.assertTrue(ccrp_gui.CcrpGui._listening_port("LISTEN 0 5 [::1]:18083 [::]:*", 18083))
        self.assertFalse(ccrp_gui.CcrpGui._listening_port("ESTAB 0 0 127.0.0.1:18082 127.0.0.1:1", 18082))
        self.assertFalse(ccrp_gui.CcrpGui._listening_port("LISTEN 0 5 127.0.0.1:180820 0.0.0.0:*", 18082))

    def test_http_probe_accepts_any_real_http_response(self):
        status, ok = ccrp_gui.CcrpGui._http_probe_status("HTTP_CODE:400\nHTTP_CODE:400\nHTTP_CODE:401\n", 0)
        self.assertEqual(status, "已打通（HTTP 400, 400, 401）")
        self.assertTrue(ok)
        status, ok = ccrp_gui.CcrpGui._http_probe_status("HTTP_CODE:000\nHTTP_CODE:000\nHTTP_CODE:000\n", 0, "Connection refused")
        self.assertIn("未打通", status)
        self.assertFalse(ok)

    def test_health_probe_requires_configured_upstream_target(self):
        body = '{"ok": true, "routes": [{"target": "127.0.0.1:18082"}]}'
        self.assertTrue(ccrp_gui.CcrpGui._health_response_ok(body, 18082))
        self.assertFalse(ccrp_gui.CcrpGui._health_response_ok(body, 18081))
        self.assertIn("目标端口不一致", ccrp_gui.CcrpGui._health_target_status(body, 18081))

    def test_read_ssh_hosts_skips_patterns_and_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config"
            path.write_text("Host *\n  User root\nHost h102 h100\nHost h102\n", encoding="utf-8")
            self.assertEqual(ccrp_gui.read_ssh_hosts(path), ["h102", "h100"])

    def test_resolve_remote_dir(self):
        self.assertEqual(ccrp.resolve_remote_dir("/home/test", "~"), "/home/test")
        self.assertEqual(ccrp.resolve_remote_dir("/home/test", "~/software/ccrp"), "/home/test/software/ccrp")
        self.assertEqual(ccrp.resolve_remote_dir("/home/test", "/opt/ccrp"), "/opt/ccrp")

    def test_repository_sync_command_clones_and_updates(self):
        command = ccrp.build_repository_sync_command(
            "https://github.com/example/ccrp.git", "release", "/home/test/ccrp"
        )
        self.assertIn("git clone --branch \"$repo_branch\"", command)
        self.assertIn('git -C "$repo_dir" fetch --prune origin "$repo_branch"', command)
        self.assertIn('git -C "$repo_dir" pull --ff-only origin "$repo_branch"', command)
        self.assertIn('current_url=$(git -C "$repo_dir" remote get-url origin', command)
        self.assertIn('different Git repository', command)
        self.assertNotIn('remote set-url origin', command)
        self.assertIn('repo_branch=release', command)

    def test_deploy_command_does_not_start_service(self):
        command = ccrp.build_repository_sync_command(
            "https://github.com/example/ccrp.git", "main", "/home/test/ccrp"
        )
        self.assertIn("git clone", command)
        self.assertIn("git -C", command)
        self.assertNotIn("tmux", command)
        self.assertNotIn("ccrp.py server", command)

    def test_start_command_uses_existing_checkout_only(self):
        command = ccrp.build_server_start_command(
            "/home/test/ccrp", "/home/test/ccrp/ccrp.config.json"
        )
        self.assertIn("test -f", command)
        self.assertIn("python3 /home/test/ccrp/ccrp.py server", command)
        self.assertIn("tmux kill-session", command)
        self.assertNotIn("git clone", command)
        self.assertNotIn("git pull", command)

    def test_server_command_parsers_are_separate(self):
        parser = ccrp.build_parser()
        deploy = parser.parse_args(["deploy-server", "--remote-dir", "~/ccrp"])
        start = parser.parse_args(["start-server", "--remote-dir", "~/ccrp"])
        self.assertEqual(deploy.remote_dir, "~/ccrp")
        self.assertEqual(start.remote_dir, "~/ccrp")
        self.assertTrue(start.restart)

    def test_install_server_parser_accepts_repository_options(self):
        parser = ccrp.build_parser()
        args = parser.parse_args([
            "install-server", "--remote-dir", "~/software/ccrp",
            "--repo-url", "https://github.com/example/ccrp.git", "--branch", "release",
        ])
        self.assertEqual(args.repo_url, "https://github.com/example/ccrp.git")
        self.assertEqual(args.branch, "release")


if __name__ == "__main__":
    unittest.main()
