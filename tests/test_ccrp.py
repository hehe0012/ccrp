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
        self.assertIn('remote set-url origin "$repo_url"', command)
        self.assertIn('repo_branch=release', command)

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
