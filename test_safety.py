import importlib.util
import json
import threading
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("detector", Path(__file__).with_name("001.py"))
detector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detector)


class SafetyTests(unittest.TestCase):
    def active_server(self):
        server = detector.SafetyServer("127.0.0.1", 0)
        server.begin_control()
        return server

    def test_no_motion_planning_before_java_starts_requesting(self):
        server = detector.SafetyServer("127.0.0.1", 0)
        for t in (10, 10.1, 10.2, 10.3):
            message = self.observe(server, [], t)
            self.assertEqual(message["command"], "STOP")
        self.assertEqual(server.planner.attempts, 0)
        server.begin_control()
        for t in (10.4, 10.5, 10.6):
            message = self.observe(server, [], t)
        self.assertEqual(message["command"], "FORWARD")

    def test_latest_camera_discards_intermediate_frames(self):
        ready = threading.Event()
        finish = threading.Event()

        class Capture:
            count = 0

            def read(self):
                self.count += 1
                if self.count <= 3:
                    return True, self.count
                ready.set()
                finish.wait(2)
                return False, None

            def release(self):
                pass

        camera = detector.LatestCamera(Capture())
        try:
            self.assertTrue(ready.wait(1))
            frame, timestamp = camera.read()
            self.assertEqual(frame, 3)
            self.assertGreater(timestamp, 0)
            finish.set()
            camera.thread.join(1)
            with self.assertRaises(RuntimeError):
                camera.read()
        finally:
            finish.set()
            camera.release()

    def test_old_result_cannot_enable_driving(self):
        server = detector.SafetyServer("127.0.0.1", 0)
        with patch.object(detector.time, "monotonic", return_value=12):
            server.update([], 900, captured_at=10)
            self.assertTrue(server.snapshot()["fault"])

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

    def test_wait_fault_and_no_automatic_restart(self):
        server = self.active_server()
        self.assertEqual(server.snapshot()["command"], "STOP")
        self.assertEqual(server.snapshot()["power"], 0)
        for _ in range(3):
            server.update([], 900)
        self.assertEqual(server.snapshot()["command"], "FORWARD")
        self.assertEqual(server.snapshot()["power"], 0.2)
        server.stop("test fault")
        server.update([], 900)
        self.assertEqual(server.snapshot()["command"], "STOP")
        self.assertTrue(server.snapshot()["fault"])

    def test_stale_detection_latches_even_if_inference_resumes(self):
        for check_snapshot in (True, False):
            server = self.active_server()
            with patch.object(detector.time, "monotonic", return_value=10.0):
                server.update([], 900)
            with patch.object(detector.time, "monotonic", return_value=11.1):
                if check_snapshot:
                    self.assertTrue(server.snapshot()["fault"])
                server.update([], 900)
                self.assertTrue(server.snapshot()["fault"])

    def observe(self, server, boxes, now, captured_at=None):
        with patch.object(detector.time, "monotonic", return_value=now):
            server.update(boxes, 900, captured_at)
            return server.snapshot()

    def test_front_stops_then_turns_away_from_occupied_side(self):
        for side_box, expected in [([0, 0, 200, 100], "RIGHT"),
                                   ([700, 0, 899, 100], "LEFT")]:
            server = self.active_server()
            for t in (10, 10.1, 10.2):
                message = self.observe(server, [], t)
            self.assertEqual(message["command"], "FORWARD")
            obstacles = [[400, 0, 500, 100], side_box]
            self.assertEqual(self.observe(server, obstacles, 10.3)["command"], "STOP")
            self.assertEqual(self.observe(server, obstacles, 10.4)["command"], "STOP")
            message = self.observe(server, obstacles, 10.7)
            self.assertEqual(message["command"], expected)
            self.assertEqual(message["power"], 0.2)
            self.assertLessEqual(message["valid_for_ms"], 500)
            with patch.object(detector.time, "monotonic", return_value=11.21):
                self.assertEqual(server.snapshot()["command"], "STOP")
            self.assertEqual(self.observe(server, [], 11.3, 11.15)["command"], "STOP")
            for t in (11.51, 11.61):
                self.assertEqual(self.observe(server, [], t)["command"], "STOP")
            self.assertEqual(self.observe(server, [], 11.71)["command"], "FORWARD")

    def test_both_sides_blocked_and_spanning_box_stop(self):
        server = self.active_server()
        boxes = [[0, 0, 900, 100]]
        self.observe(server, boxes, 10)
        message = self.observe(server, boxes, 10.4)
        self.assertEqual(message["command"], "STOP")
        self.assertEqual(message["reason"], "BLOCKED")
        self.assertFalse(message["fault"])

    def test_obstacle_on_turn_side_interrupts_turn(self):
        server = self.active_server()
        front = [[400, 0, 500, 100]]
        self.observe(server, front, 10)
        self.assertEqual(self.observe(server, front, 10.4)["command"], "RIGHT")
        message = self.observe(server, front + [[700, 0, 800, 100]], 10.5)
        self.assertEqual(message["command"], "STOP")
        self.assertEqual(message["reason"], "TURN_SIDE_BLOCKED")

    def test_turn_attempt_limit(self):
        planner = detector.AvoidancePlanner()
        front = [[400, 0, 500, 100]]
        planner.observe(front, 900, 10, 10)
        now = 10.4
        for _ in range(4):
            planner.observe(front, 900, now, now)
            self.assertEqual(planner.command, "RIGHT")
            now += 0.81
        planner.observe(front, 900, now, now)
        self.assertEqual(planner.command, "STOP")
        self.assertEqual(planner.reason, "TURN_LIMIT")

    def test_duplicate_frame_does_not_count_as_clear_confirmation(self):
        server = self.active_server()
        for t in (10.1, 10.2, 10.3):
            self.assertEqual(self.observe(server, [], t, 10)["command"], "STOP")

    def test_websocket_exchange_and_late_client(self):
        from websockets.sync.client import connect

        server = detector.SafetyServer("127.0.0.1", 0)
        server.start()
        url = f"ws://127.0.0.1:{server.server.socket.getsockname()[1]}"
        try:
            with connect(url) as client:
                client.send("STATUS")
                self.assertEqual(json.loads(client.recv(timeout=2))["reason"], "WAIT")
                for _ in range(3):
                    server.update([], 900)
                client.send("STATUS")
                message = json.loads(client.recv(timeout=2))
                self.assertEqual(message["type"], "drive")
                self.assertEqual(message["version"], 2)
                self.assertEqual(message["command"], "FORWARD")
                self.assertEqual(message["power"], 0.2)
                server.update([[400, 0, 500, 100]], 900)
                client.send("STATUS")
                self.assertEqual(json.loads(client.recv(timeout=2))["command"], "STOP")
                server.stop("test fault")
            with connect(url) as client:
                client.send("STATUS")
                self.assertTrue(json.loads(client.recv(timeout=2))["fault"])
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
