# YOLOv8 → WebSocket → FTC 로봇

PC에 연결된 웹캠을 [001.py](001.py)가 분석하고, FTC의
[roboticsDog.java](roboticsDog.java)가 WebSocket으로 감지 상태를 받아 모터를 제어합니다.
하드웨어 이름, 서보 초기 위치, Go/Left/Right의 모터 방향은 제공된 코드와 같습니다.
게임패드 수동 주행 대신 Python 명령으로 자동 주행합니다. 출력 0.2는 m/s가 아니라
FTC `setPower`의 출력 비율입니다.

- 기본 회피 대상은 기존과 동일하게 **사람만**입니다.
- 정면이 3개 새 프레임에서 연속으로 비어 있으면 출력 0.2로 전진합니다.
- 사람 상자가 화면 중앙 1/3에 걸치면 네 바퀴 출력을 0으로 설정하고 BRAKE를 적용합니다.
- 정지 후, 감지 상자가 없는 쪽으로 출력 0.2의 짧은 제자리 회전을 지시합니다.
  왼쪽이 막히면 오른쪽, 오른쪽이 막히면 왼쪽을 선택합니다. 양쪽 모두 막히면 정지합니다.
  양옆에 감지 대상이 없으면 정면 사람 중심에서 먼 쪽을 선택하며 정확히 중앙이면 오른쪽입니다.
- 회전 명령은 최대 0.5초이고, 정지·재확인 후 정면이 3프레임 연속 비면 다시 전진합니다.
  회전 중 찍힌 프레임으로 재출발하지 않습니다. 4번 회전해도 정면이 막혀 있으면 정지를 유지합니다.
  그 상태에서 정면이 3프레임 연속 비면 다시 전진할 수 있습니다.
- Java는 방향 전환 사이에 출력 0을 최소 300ms 유지합니다. 따라서 실제 회전 시간은
  0.5초보다 짧을 수 있으며, 회전 각도는 보정되지 않았습니다.
- 좌우 사람은 PC 스피커 TTS로 경고합니다. 물리 부저 및 로봇 스피커 제어는 포함하지 않습니다.
- 사람의 접근 속도나 거리를 추정하지 않고 화면 내 위치로 판단합니다.
- 시작 전, 연결 실패, 잘못된 응답, 통신 중단 시 주행을 허용하지 않습니다.
- **FTC START 후 자동으로 이동합니다. 게임패드 B 또는 DS STOP으로 정지하세요.**
- B 버튼 정지는 OpMode를 다시 시작해야 해제됩니다. OpMode 종료로 연결이 닫히면
  Python도 재시작합니다.
- 연결 해제와 감지 지연/오류는 Python과 OpMode를 모두 다시 시작해야 해제됩니다.
- 자동 재연결은 없습니다. 서버는 동시에 한 컨트롤러만 허용하며, Java START 후
  첫 `STATUS` 요청 전에는 회피 판단을 진행하지 않습니다.

## Python 서버 실행

라즈베리파이 지연 감소: 기본 추론 크기는 320이며, 캡처 스레드가 최신 한 장만
유지합니다. 작은 사람 감지가 어려우면 `--imgsz 640`으로 높일 수 있지만 처리량은 증가합니다.
화면 출력이 필요 없으면 `--no-display`를 추가하세요(Ctrl+C로 종료).
`[PERF] infer=...ms frame-age=...ms`로 추론 시간과 캡처 반환 후 결과까지의 시간을
확인할 수 있습니다. 카메라 내부 지연은 이 값에 포함되지 않습니다.
1초 이상 오래된 결과는 정지 상태를 유지하므로, 느린 추론을 타임아웃 연장으로 감추지 않습니다.

```sh
python -m pip install -r requirements.txt
python 001.py --host 0.0.0.0 --port 5000 --camera 0
```

Python/PyTorch가 지원하는 환경을 사용하세요. 첫 실행은 `yolov8n.pt`를 다운로드합니다.
macOS는 `say`, Linux는 별도로 설치한 `espeak-ng` 또는 `espeak`가 필요합니다.
창에서 Q를 누르면 종료합니다. 모델 준비와 첫 추론 전에는 `STOP / WAIT` 상태입니다.
초기 모델 준비를 위한 추론은 카메라를 열기 전에 실행합니다.

## FTC Android Studio 프로젝트 적용

1. `roboticsDog.java`를 FTC 프로젝트의
   `TeamCode/src/main/java/org/firstinspires/ftc/teamcode/`에 복사합니다.
   기존 동일 클래스는 교체합니다.
2. TeamCode 모듈의 Gradle `dependencies`에 다음을 추가합니다.

   ```groovy
   implementation 'org.java-websocket:Java-WebSocket:1.6.0'
   ```

   프로젝트 저장소 설정에는 `mavenCentral()`이 필요합니다.
   FTC Robot Controller 앱의 병합된 Manifest에 `android.permission.INTERNET` 권한이 있어야 합니다.
   이 저장소는 전체 FTC/Gradle 프로젝트가 아니므로 위 설정은 실제 프로젝트에 적용해야 합니다.
3. Java 상단의 `SERVER_URL`을 **Python PC의 IP**로 변경합니다.
   예: `ws://192.168.0.10:5000`. 예시 IP를 그대로 사용하지 마세요.
4. PC와 FTC 컨트롤러를 서로 통신 가능한 동일 네트워크에 연결합니다.
   Control Hub의 Wi-Fi를 사용한다면 PC도 해당 네트워크에 연결합니다.
   PC 방화벽에서 TCP 5000 인바운드를 허용합니다.
5. Python을 실행한 후 FTC 앱을 빌드·설치하고 `roboticsDog (YOLO Auto Avoid)`를 INIT → START 합니다.
   이전 Java 코드는 새 프로토콜을 처리하지 못하므로 **Python과 Java를 함께 업데이트**하세요.

## 통신 및 정지 동작

Java는 최대 100ms 간격으로 문자열 `STATUS`를 보내고, Python은 JSON으로 응답합니다.
한 번에 한 요청만 진행하며 750ms 이상 늦은 응답은 주행 허가로 사용하지 않습니다.

```json
{"type": "drive", "version": 2, "command": "FORWARD", "power": 0.2,
 "valid_for_ms": 500, "fault": false, "reason": "FRONT_CLEAR"}
```

`command`: `FORWARD`, `LEFT`, `RIGHT`, `STOP`. STOP의 출력은 항상 0입니다.
`fault: false`인 STOP은 장애물 회피 중 정상 정지이며 새 감지 결과에 따라 이동할 수 있습니다.
`fault: true`인 STOP은 오류로 인한 정지이며 자동 해제되지 않습니다.
명령의 유효 시간은 최대 500ms이고 남은 회전 시간/감지 결과의 유효 시간에 맞춰 줄어듭니다.
Java는 요청을 보낸 시점부터 유효 시간을 계산해 네트워크 지연을 보수적으로 반영하고,
명령이 만료되면 즉시 출력 0으로 변경합니다. 수신이 750ms 끊기면 오류 정지를 유지합니다.
Python에서 촬영 후 1초 이상 지난 감지 결과는 오류 정지로 처리합니다.
모터 출력은 WebSocket 콜백이 아니라 OpMode 루프에서 변경합니다.
서보는 초기 자세를 유지합니다.

단안 카메라의 바운딩 박스는 거리·실제 통행 가능 공간을 알 수 없습니다. 사람 이외의
장애물, 화면 밖 장애물, 미검출 대상은 피하지 못하며 충돌 방지를 보장하지 않습니다.
이 타임아웃은 기계의 실제 제동 시간을 보장하지 않습니다. 처음에는 바퀴를 지면에서 띄우고
Go/Left/Right의 실제 방향과 출력 0.2, 중앙/좌우 감지, B 버튼, 서버 종료,
네트워크 단절 시 정지를 확인한 다음 빈 공간에서 회전 동작을 확인하세요.
현재 연결은 인증 없는 로컬 `ws://` 방식이므로 신뢰하는 로봇 네트워크에서 사용합니다.

API 참고: [Python websockets](https://websockets.readthedocs.io/en/stable/reference/sync/server.html),
[Java-WebSocket](https://github.com/TooTallNate/Java-WebSocket).

테스트: `python -m unittest -v test_safety` (카메라/로봇 없이 판단 로직과 로컬 통신 검증).
