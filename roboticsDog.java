package org.firstinspires.ftc.teamcode;

import com.qualcomm.robotcore.eventloop.opmode.LinearOpMode;
import com.qualcomm.robotcore.eventloop.opmode.TeleOp;
import com.qualcomm.robotcore.hardware.DcMotor;
import com.qualcomm.robotcore.hardware.Servo;
import org.java_websocket.client.WebSocketClient;
import org.java_websocket.handshake.ServerHandshake;
import org.json.JSONObject;
import java.net.URI;

@TeleOp(name = "roboticsDog (YOLO WebSocket)")
public class roboticsDog extends LinearOpMode {
    // Python을 실행하는 PC의 실제 Wi-Fi IP로 변경하세요. 로봇 자신의 IP가 아닙니다.
    private static final String SERVER_URL = "ws://192.168.0.10:8765";
    private DcMotor LeftFrontWheel, LeftBackWheel, RightFrontWheel, RightBackWheel;
    private Servo LeftFrontServo, LeftBackServo, RightFrontServo, RightBackServo;

    @Override
    public void runOpMode() {
        LeftFrontWheel = hardwareMap.get(DcMotor.class, "LeftFrontWheel");
        LeftBackWheel = hardwareMap.get(DcMotor.class, "LeftBackWheel");
        RightFrontWheel = hardwareMap.get(DcMotor.class, "RightFrontWheel");
        RightBackWheel = hardwareMap.get(DcMotor.class, "RightBackWheel");
        LeftFrontServo = hardwareMap.get(Servo.class, "LeftFrontServo");
        LeftBackServo = hardwareMap.get(Servo.class, "LeftBackServo");
        RightFrontServo = hardwareMap.get(Servo.class, "RightFrontServo");
        RightBackServo = hardwareMap.get(Servo.class, "RightBackServo");

        LeftFrontWheel.setDirection(DcMotor.Direction.REVERSE);
        LeftBackWheel.setDirection(DcMotor.Direction.REVERSE);
        RightFrontWheel.setDirection(DcMotor.Direction.FORWARD);
        RightBackWheel.setDirection(DcMotor.Direction.FORWARD);
        for (DcMotor motor : new DcMotor[]{LeftFrontWheel, LeftBackWheel,
                RightFrontWheel, RightBackWheel}) {
            motor.setPower(0);
            motor.setZeroPowerBehavior(DcMotor.ZeroPowerBehavior.BRAKE);
        }
        LeftFrontServo.setDirection(Servo.Direction.REVERSE);
        LeftBackServo.setDirection(Servo.Direction.REVERSE);
        RightFrontServo.setDirection(Servo.Direction.FORWARD);
        RightBackServo.setDirection(Servo.Direction.FORWARD);
        LeftFrontServo.setPosition(0.65);
        LeftBackServo.setPosition(0.65);
        RightFrontServo.setPosition(0.65);
        RightBackServo.setPosition(0.65);

        SafetyClient client = new SafetyClient(URI.create(SERVER_URL));
        try {
            client.connect(); // 비동기 연결: OpMode 스레드를 막지 않는다.
            telemetry.addData("Server", SERVER_URL);
            telemetry.addLine("Valid detection required before driving");
            telemetry.update();
            waitForStart();
            while (opModeIsActive()) {
                // 콜백은 상태만 변경하고, 모터는 이 OpMode 루프에서만 제어한다.
                synchronized (client) {
                    client.pollStatus();
                    if (client.canDrive()) {
                        LeftFrontWheel.setPower((-gamepad1.left_stick_x + gamepad1.left_stick_y) / 2 - gamepad1.right_stick_x);
                        LeftBackWheel.setPower((gamepad1.left_stick_x + gamepad1.left_stick_y) / 2 - gamepad1.right_stick_x);
                        RightFrontWheel.setPower((gamepad1.left_stick_x + gamepad1.left_stick_y) / 2 + gamepad1.right_stick_x);
                        RightBackWheel.setPower((-gamepad1.left_stick_x + gamepad1.left_stick_y) / 2 + gamepad1.right_stick_x);
                    } else {
                        stopWheels();
                    }
                    telemetry.addData("Vision", client.status);
                    telemetry.addData("Stop latched", client.stopLatched);
                }
                telemetry.update();
                sleep(10);
            }
        } finally {
            stopWheels();
            client.close();
        }
    }

    private void stopWheels() {
        LeftFrontWheel.setPower(0);
        LeftBackWheel.setPower(0);
        RightFrontWheel.setPower(0);
        RightBackWheel.setPower(0);
    }

    static class SafetyClient extends WebSocketClient {
        private static final long POLL_NS = 100_000_000L; // 100ms
        private static final long TIMEOUT_NS = 750_000_000L; // 750ms
        private boolean connected = false;
        private boolean pending = false;
        private boolean ready = false;
        private boolean stopLatched = false;
        private long sentAt = 0;
        private long receivedAt = 0;
        private String status = "CONNECTING";

        SafetyClient(URI uri) {
            super(uri);
        }

        private void block(String reason) {
            stopLatched = true;
            ready = false;
            status = reason;
        }

        synchronized void pollStatus() {
            long now = System.nanoTime();
            if ((pending && now - sentAt >= TIMEOUT_NS)
                    || (ready && now - receivedAt >= TIMEOUT_NS)) {
                block("TIMEOUT - restart OpMode");
            }
            if (connected && !pending && now - sentAt >= POLL_NS) {
                // 한 번에 한 요청만 전송해 오래된 CLEAR 응답이 쌓이지 않게 한다.
                pending = true;
                sentAt = now;
                try {
                    send("STATUS");
                } catch (RuntimeException e) {
                    block("SEND ERROR");
                }
            }
        }

        synchronized boolean canDrive() {
            return connected && ready && !stopLatched
                    && System.nanoTime() - receivedAt < TIMEOUT_NS;
        }

        @Override
        public synchronized void onOpen(ServerHandshake handshake) {
            connected = true;
            status = "WAITING FOR DETECTION";
        }

        @Override
        public synchronized void onMessage(String message) {
            long now = System.nanoTime();
            if (!pending || now - sentAt >= TIMEOUT_NS) {
                block("UNEXPECTED OR LATE RESPONSE");
                return;
            }
            pending = false;
            try {
                JSONObject data = new JSONObject(message);
                String action = data.getString("action");
                Object stopValue = data.get("stop");
                boolean allowed = "CLEAR".equals(action) || "WARN_LEFT".equals(action)
                        || "WARN_RIGHT".equals(action) || "WARN_BOTH".equals(action);
                if (!"safety".equals(data.getString("type"))
                        || !(stopValue instanceof Boolean)
                        || (!allowed && !"STOP".equals(action) && !"WAIT".equals(action))) {
                    block("INVALID RESPONSE");
                    return;
                }
                boolean stop = (Boolean) stopValue;
                if (stop != !allowed) {
                    block("INCONSISTENT RESPONSE");
                    return;
                }
                receivedAt = now;
                ready = allowed && !stop;
                if ("STOP".equals(action)) {
                    block("PERSON / DETECTOR STOP - restart server and OpMode");
                } else if (!stopLatched) {
                    status = action;
                }
            } catch (Exception e) {
                block("INVALID JSON");
            }
        }

        @Override
        public synchronized void onClose(int code, String reason, boolean remote) {
            connected = false;
            block("DISCONNECTED - restart OpMode");
        }

        @Override
        public synchronized void onError(Exception error) {
            block("NETWORK ERROR - restart OpMode");
        }
    }
}
