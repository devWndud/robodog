import argparse
import json
import shutil
import subprocess
import threading
import time


def person_zone(x1, x2, width):
    if x2 >= width / 3 and x1 <= width * 2 / 3:
        return "CENTER"
    return "LEFT" if x2 < width / 3 else "RIGHT"


def choose_action(zones):
    if "CENTER" in zones:
        return "STOP"
    if "LEFT" in zones and "RIGHT" in zones:
        return "WARN_BOTH"
    if "LEFT" in zones:
        return "WARN_LEFT"
    if "RIGHT" in zones:
        return "WARN_RIGHT"
    return "CLEAR"


class AvoidancePlanner:
    PAUSE_SECONDS = 0.3
    TURN_SECONDS = 0.5
    MAX_TURNS = 4
    CLEAR_FRAMES = 3

    def __init__(self):
        self.phase = "CHECK"
        self.command = "STOP"
        self.reason = "WAIT"
        self.deadline = 0.0
        self.clear_frames = 0
        self.attempts = 0

    def advance(self, now):
        if self.phase == "TURN" and now >= self.deadline:
            self.phase = "CHECK"
            self.command = "STOP"
            self.reason = "RECHECK"
            self.deadline += self.PAUSE_SECONDS
            self.clear_frames = 0

    def observe(self, boxes, width, captured_at, now):
        self.advance(now)
        left_edge, right_edge = width / 3, width * 2 / 3
        front = [box for box in boxes if box[2] >= left_edge and box[0] <= right_edge]
        left_blocked = any(box[0] < left_edge for box in boxes)
        right_blocked = any(box[2] > right_edge for box in boxes)

        if self.phase == "TURN":
            blocked = left_blocked if self.command == "LEFT" else right_blocked
            if blocked:
                self.command = "STOP"
                self.reason = "TURN_SIDE_BLOCKED"
                self.phase = "PAUSE"
                self.deadline = now + self.PAUSE_SECONDS
            return

        if self.phase in {"PAUSE", "CHECK"} and captured_at < self.deadline:
            return

        if not front:
            if self.phase == "FORWARD":
                return
            self.clear_frames += 1
            self.command = "STOP"
            self.reason = "CONFIRM_CLEAR"
            if self.clear_frames >= self.CLEAR_FRAMES:
                self.phase = "FORWARD"
                self.command = "FORWARD"
                self.reason = "FRONT_CLEAR"
                self.attempts = 0
            return

        self.clear_frames = 0
        if self.phase == "FORWARD" or self.reason == "WAIT":
            self.phase = "PAUSE"
            self.command = "STOP"
            self.reason = "FRONT_OBSTACLE"
            self.deadline = now + self.PAUSE_SECONDS
            return
        if (left_blocked and right_blocked) or self.attempts >= self.MAX_TURNS:
            self.phase = "CHECK"
            self.command = "STOP"
            self.reason = "BLOCKED" if self.attempts < self.MAX_TURNS else "TURN_LIMIT"
            return

        if left_blocked:
            direction = "RIGHT"
        elif right_blocked:
            direction = "LEFT"
        else:
            center = sum((b[0] + b[2]) / 2 for b in front) / len(front)
            direction = "RIGHT" if center <= width / 2 else "LEFT"
        self.phase = "TURN"
        self.command = direction
        self.reason = "AVOID_FRONT"
        self.deadline = now + self.TURN_SECONDS
        self.attempts += 1


class SafetyServer:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.stopped = False
        self.planner = AvoidancePlanner()
        self.fault_reason = ""
        self.updated_at = None
        self.lock = threading.Lock()
        self.server = None
        self.thread = None
        self.clients = set()
        self.control_active = False

    def begin_control(self):
        with self.lock:
            if not self.control_active:
                self.planner = AvoidancePlanner()
                self.control_active = True

    def update(self, boxes, width, captured_at=None):
        with self.lock:
            now = time.monotonic()
            captured_at = now if captured_at is None else captured_at
            if (now - captured_at >= 1.0
                    or (self.updated_at is not None and now - self.updated_at >= 1.0)):
                self.stopped = True
                self.fault_reason = "STALE_DETECTION"
            if self.updated_at is not None and captured_at <= self.updated_at:
                return
            self.updated_at = captured_at
            if not self.stopped and self.control_active:
                self.planner.observe(boxes, width, captured_at, now)

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            if self.updated_at is not None and now - self.updated_at >= 1.0:
                self.stopped = True
                self.fault_reason = "STALE_DETECTION"
            self.planner.advance(now)
            command = "STOP" if self.stopped else self.planner.command
            ttl = 0.5
            if self.updated_at is not None:
                ttl = min(ttl, max(0, self.updated_at + 1.0 - now))
            if command in {"LEFT", "RIGHT"}:
                ttl = min(ttl, max(0, self.planner.deadline - now))
            ttl_ms = int(ttl * 1000)
            if ttl_ms <= 0:
                command = "STOP"
            return {"type": "drive", "version": 2, "command": command,
                    "power": 0.0 if command == "STOP" else 0.2,
                    "valid_for_ms": ttl_ms, "fault": self.stopped,
                    "reason": self.fault_reason if self.stopped else self.planner.reason}

    def stop(self, reason):
        with self.lock:
            self.stopped = True
            self.fault_reason = reason
        print(f"[STOP] WebSocket 정지 상태 설정: {reason}", flush=True)

    def handle_client(self, websocket):
        from websockets.exceptions import ConnectionClosed

        with self.lock:
            busy = bool(self.clients)
            if not busy:
                self.clients.add(websocket)
        if busy:
            websocket.close(code=1008, reason="Only one robot controller is allowed")
            return
        activated = False
        try:
            for message in websocket:
                if message != "STATUS":
                    websocket.close(code=1008, reason="Expected STATUS")
                    return
                if not activated:
                    self.begin_control()
                    activated = True
                    response = json.dumps(self.snapshot())
                    print(f"[WebSocket TX first] {response}", flush=True)
                else:
                    response = json.dumps(self.snapshot())
                websocket.send(response)
        except ConnectionClosed:
            pass
        finally:
            if activated:
                self.stop("컨트롤러 연결 해제")
            with self.lock:
                self.clients.discard(websocket)

    def start(self):
        from websockets.sync.server import serve

        self.server = serve(self.handle_client, self.host, self.port,
                            max_size=128, close_timeout=1)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"[WebSocket] {self.host}:{self.port} 서버 시작 | protocol=drive v2", flush=True)
        print(f"[Source] {__file__}", flush=True)

    def close(self):
        if self.server is not None:
            with self.lock:
                clients = list(self.clients)
            for client in clients:
                client.close(code=1001, reason="Detector stopped")
            self.server.shutdown()
            self.thread.join(timeout=2)


class LatestCamera:
    def __init__(self, capture):
        self.capture = capture
        self.condition = threading.Condition()
        self.frame = None
        self.sequence = 0
        self.consumed = 0
        self.failed = False
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while not self.closed.is_set():
                ok, frame = self.capture.read()
                with self.condition:
                    if not ok:
                        self.failed = True
                        self.condition.notify_all()
                        return
                    self.frame = frame
                    self.captured_at = time.monotonic()
                    self.sequence += 1
                    self.condition.notify_all()
        finally:
            with self.condition:
                self.failed = True
                self.condition.notify_all()
            self.capture.release()

    def read(self):
        with self.condition:
            available = self.condition.wait_for(
                lambda: self.failed or self.sequence != self.consumed, timeout=1)
            if not available or self.failed:
                raise RuntimeError("카메라 프레임 수신 중단 또는 1초 초과 지연")
            self.consumed = self.sequence
            return self.frame, self.captured_at

    def release(self):
        self.closed.set()
        self.thread.join(timeout=2)


class VoiceWarning:
    MESSAGES = {
        "WARN_LEFT": "왼쪽에 사람이 있습니다. 주의하세요.",
        "WARN_RIGHT": "오른쪽에 사람이 있습니다. 주의하세요.",
        "WARN_BOTH": "양쪽에 사람이 있습니다. 주의하세요.",
    }

    def __init__(self, interval):
        self.interval = interval
        self.last_warning = float("-inf")
        self.process = None
        if executable := shutil.which("say"):
            self.command = [executable]
        elif executable := shutil.which("espeak-ng") or shutil.which("espeak"):
            self.command = [executable, "-v", "ko"]
        else:
            raise RuntimeError("TTS 실행기가 없습니다. macOS say 또는 Linux espeak-ng가 필요합니다.")

    def warn(self, action):
        if self.process is not None:
            status = self.process.poll()
            if status is None:
                return
            if status != 0:
                raise RuntimeError(f"TTS 실행 실패: 종료 코드 {status}")
        now = time.monotonic()
        if now - self.last_warning >= self.interval:
            self.process = subprocess.Popen(self.command + [self.MESSAGES[action]])
            self.last_warning = now

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--imgsz", type=int, default=320, help="YOLO 입력 크기")
    parser.add_argument("--no-display", action="store_true", help="영상 창 없이 실행")
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--confidence", type=float, default=0.5)
    parser.add_argument("--warning-interval", type=float, default=3.0)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    if args.imgsz < 32 or args.imgsz % 32:
        parser.error("imgsz는 32 이상의 32 배수여야 합니다.")
    if not 0 < args.confidence <= 1 or args.warning_interval <= 0:
        parser.error("confidence는 0 초과 1 이하, warning-interval은 0 초과여야 합니다.")

    if not 1 <= args.port <= 65535:
        parser.error("port는 1~65535여야 합니다.")
    robot = SafetyServer(args.host, args.port)
    camera = None
    voice = None
    cv2 = None
    try:
        robot.start()
        import cv2
        import numpy as np
        from ultralytics import YOLO

        voice = VoiceWarning(args.warning_interval)
        model = YOLO(args.model)
        if model.names.get(0) != "person":
            raise ValueError("0번 클래스가 person인 COCO YOLOv8 모델을 사용하세요.")
        model.predict(np.zeros((args.imgsz, args.imgsz, 3), dtype=np.uint8),
                      classes=[0], imgsz=args.imgsz, verbose=False)
        camera = cv2.VideoCapture(args.camera)
        if not camera.isOpened():
            raise RuntimeError("웹캠을 열 수 없습니다. 연결과 --camera 번호를 확인하세요.")
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        camera = LatestCamera(camera)
        last_log = 0.0

        while True:
            frame, captured_at = camera.read()
            height, width = frame.shape[:2]
            started = time.monotonic()
            result = model.predict(frame, classes=[0], conf=args.confidence,
                                   imgsz=args.imgsz, verbose=False)[0]
            inference_ms = (time.monotonic() - started) * 1000
            zones = set()
            boxes = []
            if result.boxes is not None:
                boxes = result.boxes.xyxy.cpu().tolist()
                for x1, y1, x2, y2 in boxes:
                    zone = person_zone(x1, x2, width)
                    zones.add(zone)
                    color = (0, 0, 255) if zone == "CENTER" else (0, 200, 255)
                    if not args.no_display:
                        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
                        cv2.putText(frame, zone, (int(x1), max(20, int(y1) - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            action = choose_action(zones)
            robot.update(boxes, width, captured_at)
            age_ms = (time.monotonic() - captured_at) * 1000
            timing = f"infer={inference_ms:.0f}ms frame-age={age_ms:.0f}ms"
            if time.monotonic() - last_log >= 2:
                print(f"[PERF] {timing} stop={robot.stopped}", flush=True)
                last_log = time.monotonic()
            if action == "STOP":
                voice.close()
            elif action.startswith("WARN"):
                voice.warn(action)

            if args.no_display:
                continue

            for boundary in (width // 3, width * 2 // 3):
                cv2.line(frame, (boundary, 0), (boundary, height), (180, 180, 180), 1)
            motion = robot.snapshot()
            state = f"{motion['command']} {motion['reason']}"
            cv2.putText(frame, f"WS SERVER | {state} | Q: quit", (10, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
            cv2.putText(frame, timing, (10, 55), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 255), 1)
            cv2.imshow("Robot person detection", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        try:
            robot.stop("프로그램 종료 또는 감지 오류")
        finally:
            robot.close()
            if voice is not None:
                voice.close()
            if camera is not None:
                camera.release()
            if cv2 is not None:
                cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
