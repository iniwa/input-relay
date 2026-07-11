import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


class SenderLauncherStaticTests(unittest.TestCase):
    """Reads start_sender.bat as plain text only: never invokes pip, netsh,
    powershell, python, or any subprocess, and never touches real config."""

    @classmethod
    def setUpClass(cls):
        cls.text = (_REPO_ROOT / "start_sender.bat").read_text(encoding="utf-8")

    def test_default_port_variables_are_set_before_config_read(self):
        default_idx = self.text.index('set "HTTP_PORT=8082"')
        default_monitor_idx = self.text.index('set "MONITOR_PORT=8083"')
        config_read_idx = self.text.index('if exist "config\\sender_config.json"')
        self.assertLess(default_idx, config_read_idx)
        self.assertLess(default_monitor_idx, config_read_idx)

    def test_config_derived_ports_are_range_validated_1_to_65535(self):
        self.assertIn("-ge 1", self.text)
        self.assertIn("-le 65535", self.text)

    def test_config_is_only_parsed_as_data_never_evaluated(self):
        self.assertIn("ConvertFrom-Json", self.text)
        self.assertNotIn("Invoke-Expression", self.text)
        self.assertNotIn("iex ", self.text.lower())

    def test_firewall_and_url_use_derived_variables_not_literal_ports(self):
        firewall_section = self.text[self.text.index("Configuring firewall"):]
        self.assertIn("localport=%HTTP_PORT%", firewall_section)
        self.assertIn("localport=%MONITOR_PORT%", firewall_section)
        self.assertIn("http://localhost:%HTTP_PORT%/", firewall_section)
        self.assertNotIn("localport=8082", firewall_section)
        self.assertNotIn("localport=8083", firewall_section)

    def test_pull_install_firewall_start_order_preserved(self):
        markers = ["git fetch", "git pull", "pip install",
                   "netsh advfirewall", "python sender"]
        positions = [self.text.index(m) for m in markers]
        self.assertEqual(positions, sorted(positions))

    def test_admin_elevation_and_entry_point_preserved(self):
        self.assertIn("Verb RunAs", self.text)
        self.assertIn("python sender\\input_sender.py", self.text)
        self.assertIn('name="InputSender GUI HTTP"', self.text)
        self.assertIn('name="InputSender Monitor WS"', self.text)


class StandaloneLauncherStaticTests(unittest.TestCase):
    """Reads start_standalone.bat as plain text only: never invokes pip,
    netsh, python, or any subprocess, and never touches real config."""

    @classmethod
    def setUpClass(cls):
        cls.text = (_REPO_ROOT / "start_standalone.bat").read_text(encoding="utf-8")

    def test_pip_install_line_includes_pygame(self):
        pip_lines = [ln for ln in self.text.splitlines() if ln.strip().startswith("pip install")]
        self.assertEqual(len(pip_lines), 1)
        packages = pip_lines[0].split("pip install", 1)[1]
        self.assertIn("pygame", packages)
        self.assertIn("websockets", packages)
        self.assertIn("pynput", packages)

    def test_fetch_pull_install_start_order_preserved(self):
        markers = ["git fetch", "git pull", "pip install", "python receiver"]
        positions = [self.text.index(m) for m in markers]
        self.assertEqual(positions, sorted(positions))

    def test_entry_point_unchanged(self):
        self.assertIn(
            "python receiver\\input_server.py --http-port 8081 --standalone",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
