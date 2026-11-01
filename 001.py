"""웹캠 + YOLOv8 사람 감지: 중앙 정지, 좌우 TTS 경고.

설치: pip install ultralytics opencv-python
실행: python 001.py
실제 로봇: python 001.py --stop-command '로봇의 정지 명령'

기본 실행은 정지 신호를 출력하는 모의 실행이다. --stop-command에는
속도를 0으로 만들고 정지 상태를 유지하는 실제 제어 명령을 지정해야 한다.
자동 주행 재개는 하지 않는다. 화면 방향은 반전하지 않은 웹캠 기준이다.
macOS는 say, Linux는 espeak-ng 또는 espeak가 있어야 TTS가 동작한다.
"""

import argparse
import shlex
import shutil
import subprocess
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


class RobotStop:
    def __init__(self, command):
        self.command = shlex.split(command) if command else None
        self.stopped = False

    def stop(self, reason):
        if self.stopped:
            return
        if self.command:
            # 로봇 SDK/ROS를 직접 사용할 경우 이 부분을 정지 API로 교체한다.
            subprocess.run(self.command, check=True, timeout=2)
            print(f"[STOP] 정지 명령 완료: {reason}", flush=True)
        else:
            print(f"[SIMULATION] 정지 요청 (하드웨어 미연결): {reason}", flush=True)
        self.stopped = True


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
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--confidence", type=float, default=0.5)
    parser.add_argument("--warning-interval", type=float, default=3.0)
    parser.add_argument("--stop-command", help="정지 상태를 유지하는 실제 로봇 제어 명령")
    args = parser.parse_args()
    if not 0 < args.confidence <= 1 or args.warning_interval <= 0:
        parser.error("confidence는 0 초과 1 이하, warning-interval은 0 초과여야 합니다.")

    robot = RobotStop(args.stop_command)
    camera = None
    voice = None
    cv2 = None
    if not robot.command:
        print("[SIMULATION] 실제 로봇 정지 기능 미연결. --stop-command를 지정하세요.", flush=True)
    try:
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

        while True:
            ok, frame = camera.read()
            if not ok:
                raise RuntimeError("웹캠 영상 수신이 중단되었습니다.")
            height, width = frame.shape[:2]
            result = model.predict(frame, classes=[0], conf=args.confidence, verbose=False)[0]
            zones = set()
            if result.boxes is not None:
                for x1, y1, x2, y2 in result.boxes.xyxy.cpu().tolist():
                    zone = person_zone(x1, x2, width)
                    zones.add(zone)
                    color = (0, 0, 255) if zone == "CENTER" else (0, 200, 255)
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
                    cv2.putText(frame, zone, (int(x1), max(20, int(y1) - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            action = choose_action(zones)
            if action == "STOP":
                robot.stop("정면 사람 감지")
                voice.close()
            elif action.startswith("WARN"):
                voice.warn(action)

            for boundary in (width // 3, width * 2 // 3):
                cv2.line(frame, (boundary, 0), (boundary, height), (180, 180, 180), 1)
            mode = "LIVE" if robot.command else "SIMULATION"
            state = "STOP LATCHED" if robot.stopped else action
            cv2.putText(frame, f"{mode} | {state} | Q: quit", (10, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
            cv2.imshow("Robot person detection", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        # 영상/추론 오류와 프로그램 종료 시에도 정지를 요청한다.
        try:
            robot.stop("프로그램 종료 또는 감지 오류")
        finally:
            if voice is not None:
                voice.close()
            if camera is not None:
                camera.release()
            if cv2 is not None:
                cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
