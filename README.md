# YOLOv8 → WebSocket → FTC 로봇

PC에 연결된 웹캠을 [001.py](001.py)가 분석하고, FTC의
[roboticsDog.java](roboticsDog.java)가 WebSocket으로 감지 상태를 받아 모터를 제어합니다.
게임패드 주행식, 하드웨어 이름과 서보 초기 위치는 제공된 코드와 같습니다.

- 사람 상자가 화면 중앙 1/3에 걸치면 네 바퀴 출력을 0으로 설정하고 BRAKE를 적용합니다.
- 좌우 사람은 PC 스피커 TTS로 경고합니다. 물리 부저 및 로봇 스피커 제어는 포함하지 않습니다.
- 사람의 접근 속도나 거리를 추정하지 않고 화면 내 위치로 판단합니다.
- 시작 전, 연결 실패, 잘못된 응답, 통신 중단 시 주행을 허용하지 않습니다.
- 중앙 감지 후에는 자동 재출발하지 않습니다. 사람이 없는 것을 확인한 다음 Python과 OpMode를 재시작합니다.
- 네트워크 오류 뒤에도 OpMode를 재시작해야 합니다. 자동 재연결/재출발은 없습니다.

## Python 서버 실행

```sh
python -m pip install -r requirements.txt
python 001.py --host 0.0.0.0 --port 8765 --camera 0
```

Python/PyTorch가 지원하는 환경을 사용하세요. 첫 실행은 `yolov8n.pt`를 다운로드합니다.
macOS는 `say`, Linux는 별도로 설치한 `espeak-ng` 또는 `espeak`가 필요합니다.
창에서 Q를 누르면 종료합니다. 모델 준비와 첫 추론 전에는 `WAIT` 상태입니다.

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
   예: `ws://192.168.0.10:8765`. 예시 IP를 그대로 사용하지 마세요.
4. PC와 FTC 컨트롤러를 서로 통신 가능한 동일 네트워크에 연결합니다.
   Control Hub의 Wi-Fi를 사용한다면 PC도 해당 네트워크에 연결합니다.
   PC 방화벽에서 TCP 8765 인바운드를 허용합니다.
5. Python을 실행한 후 FTC 앱을 빌드·설치하고 `roboticsDog (YOLO WebSocket)`을 INIT → START 합니다.
   `CLEAR`/`WARN_*` 상태일 때만 게임패드 주행을 허용합니다.

## 통신 및 정지 동작

Java는 최대 100ms 간격으로 문자열 `STATUS`를 보내고, Python은 JSON으로 응답합니다.
한 번에 한 요청만 진행하며 750ms 이상 늦은 응답은 주행 허가로 사용하지 않습니다.

```json
{"type": "safety", "action": "CLEAR", "stop": false}
```

`action`: `WAIT`, `CLEAR`, `WARN_LEFT`, `WARN_RIGHT`, `WARN_BOTH`, `STOP`.
`WAIT`/`STOP`은 `stop: true`, 나머지는 `stop: false`입니다.
Python 추론 갱신이 1초 이상 멈추면 서버가 `STOP`을 유지합니다.
Java는 마지막 정상 감지 응답 또는 진행 중 요청이 750ms를 넘으면 정지를 유지합니다.
모터 출력은 WebSocket 콜백이 아니라 OpMode 루프에서 변경합니다.
서보는 초기 자세를 유지합니다.

이 타임아웃은 기계의 실제 제동 시간을 보장하지 않습니다. 처음에는 바퀴를 지면에서 띄우고
중앙/좌우 감지, 서버 종료, 네트워크 단절 시 출력 0과 재출발 차단을 확인하세요.
현재 연결은 인증 없는 로컬 `ws://` 방식이므로 신뢰하는 로봇 네트워크에서 사용합니다.

API 참고: [Python websockets](https://websockets.readthedocs.io/en/stable/reference/sync/server.html),
[Java-WebSocket](https://github.com/TooTallNate/Java-WebSocket).
