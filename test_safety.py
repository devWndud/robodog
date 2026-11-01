"""카메라와 모터 없이 판단 로직 및 실제 로컬 WebSocket 통신을 검증한다."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("detector", Path(__file__).with_name("001.py"))
detector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detector)


class SafetyTests(unittest.TestCase):
    def test_regions_and_priority(self):
        self.assertEqual(detector.person_zone(0, 100, 900), "LEFT")
        self.assertEqual(detector.person_zone(700, 890, 900), "RIGHT")
        self.assertEqual(detector.person_zone(250, 310, 900), "CENTER")
        self.assertEqual(detector.person_zone(590, 700, 900), "CENTER")
        for zones, action in [(set(), "CLEAR"), ({"LEFT"}, "WARN_LEFT"),
                              ({"RIGHT"}, "WARN_RIGHT"),
                              ({"LEFT", "RIGHT"}, "WARN_BOTH"),
                              ({"LEFT", "CENTER", "RIGHT"}, "STOP")]:
            self.assertEqual(detector.choose_action(zones), action)

    def test_wait_stop_and_no_automatic_restart(self):
        server = detector.SafetyServer("127.0.0.1", 0)
        self.assertEqual(server.snapshot(), {"type": "safety", "action": "WAIT", "stop": True})
        server.update("CLEAR")
        self.assertFalse(server.snapshot()["stop"])
        server.update("STOP")
        server.update("CLEAR")
        self.assertEqual(server.snapshot()["action"], "STOP")

    def test_stale_detection_latches_even_if_inference_resumes(self):
        for check_snapshot in (True, False):
            server = detector.SafetyServer("127.0.0.1", 0)
            with patch.object(detector.time, "monotonic", return_value=10.0):
                server.update("CLEAR")
            with patch.object(detector.time, "monotonic", return_value=11.1):
                if check_snapshot:
                    self.assertTrue(server.snapshot()["stop"])
                server.update("CLEAR")
                self.assertTrue(server.snapshot()["stop"])

    def test_websocket_exchange_and_late_client(self):
        from websockets.sync.client import connect

        server = detector.SafetyServer("127.0.0.1", 0)
        server.start()
        url = f"ws://127.0.0.1:{server.server.socket.getsockname()[1]}"
        try:
            with connect(url) as client:
                client.send("STATUS")
                self.assertEqual(json.loads(client.recv(timeout=2))["action"], "WAIT")
                for action in ("WARN_LEFT", "WARN_RIGHT", "WARN_BOTH", "CLEAR"):
                    server.update(action)
                    client.send("STATUS")
                    message = json.loads(client.recv(timeout=2))
                    self.assertEqual(message["action"], action)
                    self.assertFalse(message["stop"])
                server.update("STOP")
                client.send("STATUS")
                self.assertTrue(json.loads(client.recv(timeout=2))["stop"])
            # 정지 후 접속하는 클라이언트도 STOP을 받는다.
            with connect(url) as client:
                client.send("STATUS")
                self.assertEqual(json.loads(client.recv(timeout=2))["action"], "STOP")
        finally:
            server.close()

    def test_server_shutdown_disconnects_client(self):
        from websockets.sync.client import connect
        from websockets.exceptions import ConnectionClosed

        server = detector.SafetyServer("127.0.0.1", 0)
        server.start()
        url = f"ws://127.0.0.1:{server.server.socket.getsockname()[1]}"
        try:
            with connect(url) as client:
                client.send("STATUS")
                client.recv(timeout=2)
                server.close()
                with self.assertRaises(ConnectionClosed):
                    client.recv(timeout=2)
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
