import re
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


class SenderGuiMonitorPortStaticTests(unittest.TestCase):
    """String/regex-based static checks on sender_gui.html only: no browser,
    no JS execution, no real config access."""

    @classmethod
    def setUpClass(cls):
        cls.text = (_REPO_ROOT / "sender" / "sender_gui.html").read_text(encoding="utf-8")

    def test_connect_monitor_uses_monitor_port_variable_not_hardcoded_8083(self):
        m = re.search(r"function connectMonitor\(\)\s*\{(.*?)\n\}", self.text, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("monitorPort", body)
        self.assertNotIn("8083", body)

    def test_load_config_normalizes_and_stores_monitor_port(self):
        m = re.search(r"async function loadConfig\(\)\s*\{(.*?)\n\}", self.text, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("monitorPort = normalizePort(cfg.monitor_port, 8083)", body)

    def test_init_awaits_config_before_connect_monitor(self):
        m = re.search(r"async function initApp\(\)\s*\{(.*?)\n\}", self.text, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        load_idx = body.index("await loadConfig()")
        connect_idx = body.index("connectMonitor()")
        self.assertLess(load_idx, connect_idx)

    def test_reconnect_reuses_monitor_port_variable(self):
        # onclose schedules connectMonitor again; connectMonitor itself reads
        # the module-level monitorPort each time, so the reconnect
        # automatically reuses whatever was last normalized.
        self.assertIn("monitorWs.onclose = () => {\n    setTimeout(connectMonitor, 2000);", self.text)


if __name__ == "__main__":
    unittest.main()
