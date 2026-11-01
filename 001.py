"""웹캠 + YOLOv8 사람 감지: 중앙 정지, 좌우 TTS 경고.

설치: pip install -r requirements.txt
실행: python 001.py
FTC Java 클라이언트: ws://이_PC의_IP:8765

Java 클라이언트가 상태를 요청하면 최신 감지 결과를 JSON으로 응답한다.
감지 전에는 주행 불가, 감지 갱신이 1초 이상 멈추면 정지를 유지한다.
자동 주행 재개는 하지 않는다. 화면 방향은 반전하지 않은 웹캠 기준이다.
macOS는 say, Linux는 espeak-ng 또는 espeak가 있어야 TTS가 동작한다.
"""

import argparse
import json
import shutil
import subprocess
import threading
import time


def person_zone(x1, x2, width):
    """사람 상자가 중앙 구역에 조금이라도 걸치면 중앙으로 처리한다."""
    if x2 >= width / 3 and x1 <= width * 2 / 3:
        return "CENTER"
    return "LEFT" if x2 < width / 3 else "RIGHT"


def choose_action(zones):
    """여러 사람이 있을 때 중앙 정지를 우선한다."""
    if "CENTER" in zones:
        return "STOP"
    if "LEFT" in zones and "RIGHT" in zones:
        return "WARN_BOTH"
    if "LEFT" in zones:
        return "WARN_LEFT"
    if "RIGHT" in zones:
        return "WARN_RIGHT"
    return "CLEAR"


class SafetyServer:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.stopped = False
        self.action = "WAIT"
        self.updated_at = None
        self.lock = threading.Lock()
        self.server = None
        self.thread = None
        self.clients = set()

    def update(self, action, captured_at=None):
        with self.lock:
            now = time.monotonic()
            if captured_at is not None and now - captured_at >= 1.0:
                self.stopped = True
            if self.updated_at is not None and now - self.updated_at >= 1.0:
                self.stopped = True
            self.stopped = self.stopped or action == "STOP"
            self.action = action
            self.updated_at = now if captured_at is None else captured_at

    def snapshot(self):
        with self.lock:
            if self.updated_at is not None and time.monotonic() - self.updated_at >= 1.0:
                self.stopped = True
            action = "STOP" if self.stopped else self.action
            return {"type": "safety", "action": action,
                    "stop": self.stopped or self.updated_at is None}

    def stop(self, reason):
        with self.lock:
            self.stopped = True
        print(f"[STOP] WebSocket 정지 상태 설정: {reason}", flush=True)

    def handle_client(self, websocket):
        from websockets.exceptions import ConnectionClosed

        with self.lock:
            self.clients.add(websocket)
        try:
            for message in websocket:
                if message != "STATUS":
                    websocket.close(code=1008, reason="Expected STATUS")
                    return
                websocket.send(json.dumps(self.snapshot()))
        except ConnectionClosed:
            pass
        finally:
            with self.lock:
                self.clients.discard(websocket)

    def start(self):
        from websockets.sync.server import serve

        self.server = serve(self.handle_client, self.host, self.port,
                            max_size=128, close_timeout=1)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"[WebSocket] {self.host}:{self.port} 서버 시작", flush=True)

    def close(self):
        if self.server is not None:
            with self.lock:
                clients = list(self.clients)
            for client in clients:
                client.close(code=1001, reason="Detector stopped")
            self.server.shutdown()
            self.thread.join(timeout=2)


class LatestCamera:
    """캡처를 계속 수행하고 아직 처리하지 않은 최신 한 장만 유지한다."""
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
        # 음성 재생을 기다리지 않아 영상 감지를 계속할 수 있다.
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--imgsz", type=int, default=320, help="YOLO 입력 크기")
    parser.add_argument("--no-display", action="store_true", help="영상 창 없이 실행")
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--confidence", type=float, default=0.5)
    parser.add_argument("--warning-interval", type=float, default=3.0)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
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
        from ultralytics import YOLO

        voice = VoiceWarning(args.warning_interval)
        model = YOLO(args.model)
        # COCO 사전학습 YOLOv8 모델의 person 클래스만 사용한다.
        if model.names.get(0) != "person":
            raise ValueError("0번 클래스가 person인 COCO YOLOv8 모델을 사용하세요.")
        camera = cv2.VideoCapture(args.camera)
        if not camera.isOpened():
            raise RuntimeError("웹캠을 열 수 없습니다. 연결과 --camera 번호를 확인하세요.")
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # 백엔드에 따라 지원하지 않을 수 있다.
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
            if result.boxes is not None:
                for x1, y1, x2, y2 in result.boxes.xyxy.cpu().tolist():
                    zone = person_zone(x1, x2, width)
                    zones.add(zone)
                    color = (0, 0, 255) if zone == "CENTER" else (0, 200, 255)
                    if not args.no_display:
                        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
                        cv2.putText(frame, zone, (int(x1), max(20, int(y1) - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            action = choose_action(zones)
            robot.update(action, captured_at)
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
            state = "STOP LATCHED" if robot.stopped else action
            cv2.putText(frame, f"WS SERVER | {state} | Q: quit", (10, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
            cv2.putText(frame, timing, (10, 55), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 255), 1)
            cv2.imshow("Robot person detection", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        # 영상/추론 오류와 프로그램 종료 시에도 정지를 요청한다.
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
