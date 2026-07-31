import json
import tempfile
import unittest
from pathlib import Path

import ccrp


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
            "ssh": {"host": "my-server"},
            "routes": [{"local": "127.0.0.1:3456", "remote_forward": "127.0.0.1:18080"}],
        }
        cmd = ccrp.build_ssh_tunnel_command(config)
        self.assertIn("-R", cmd)
        self.assertIn("127.0.0.1:18080:127.0.0.1:3456", cmd)
        self.assertEqual(cmd[-1], "my-server")


if __name__ == "__main__":
    unittest.main()
