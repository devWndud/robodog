package org.firstinspires.ftc.teamcode;

import com.qualcomm.robotcore.eventloop.opmode.LinearOpMode;
import com.qualcomm.robotcore.eventloop.opmode.TeleOp;
import com.qualcomm.robotcore.hardware.DcMotor;
import com.qualcomm.robotcore.hardware.Servo;
import org.java_websocket.client.WebSocketClient;
import org.java_websocket.handshake.ServerHandshake;
import org.json.JSONObject;
import org.json.JSONException;
import java.net.URI;

@TeleOp(name = "roboticsDog (YOLO Auto Avoid)")
public class roboticsDog extends LinearOpMode {
    private static final String SERVER_URL = "ws://192.168.0.10:5000";
    private DcMotor LeftFrontWheel, LeftBackWheel, RightFrontWheel, RightBackWheel;
    private Servo LeftFrontServo, LeftBackServo, RightFrontServo, RightBackServo;
    private String appliedCommand = "STOP";
    private long stoppedAt = System.nanoTime();

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
            client.connect();
            telemetry.addData("Server", SERVER_URL);
            telemetry.addLine("AUTO: START enables motion at power 0.2. B = emergency stop.");
            telemetry.update();
            waitForStart();
            while (opModeIsActive()) {
                synchronized (client) {
                    client.pollStatus();
                    if (gamepad1.b) {
                        client.block("OPERATOR STOP - restart OpMode");
                    }
                    applyCommand(client.currentCommand(), client.power);
                    telemetry.addData("Vision", client.status);
                    telemetry.addData("Stop latched", client.stopLatched);
                    telemetry.addData("Motors", appliedCommand);
                    if (!client.errorResponse.isEmpty()) {
                        telemetry.addData("Failed response", client.errorResponse);
                    }
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

    private void applyCommand(String command, double power) {
        long now = System.nanoTime();
        if (!command.equals(appliedCommand) && !"STOP".equals(appliedCommand)) {
            stopWheels();
            appliedCommand = "STOP";
            stoppedAt = now;
        }
        if ("STOP".equals(command)
                || ("STOP".equals(appliedCommand) && now - stoppedAt < 300_000_000L)) {
            stopWheels();
            return;
        }
        double limitedPower = Math.max(0, Math.min(0.2, power));
        switch (command) {
            case "FORWARD": Go(limitedPower); break;
            case "LEFT": Left(limitedPower); break;
            case "RIGHT": Right(limitedPower); break;
            default: stopWheels(); return;
        }
        appliedCommand = command;
    }

    private void Go(double power) {
        LeftFrontWheel.setPower(power);
        LeftBackWheel.setPower(power);
        RightFrontWheel.setPower(power);
        RightBackWheel.setPower(power);
    }

    private void Left(double power) {
        LeftFrontWheel.setPower(-power);
        LeftBackWheel.setPower(-power);
        RightFrontWheel.setPower(power);
        RightBackWheel.setPower(power);
    }

    private void Right(double power) {
        LeftFrontWheel.setPower(power);
        LeftBackWheel.setPower(power);
        RightFrontWheel.setPower(-power);
        RightBackWheel.setPower(-power);
    }

    static class SafetyClient extends WebSocketClient {
        private static final long POLL_NS = 100_000_000L;
        private static final long TIMEOUT_NS = 750_000_000L;
        private boolean connected = false;
        private boolean pending = false;
        private boolean received = false;
        private boolean stopLatched = false;
        private long sentAt = 0;
        private long receivedAt = 0;
        private long expiresAt = 0;
        private String command = "STOP";
        private double power = 0;
        private String status = "CONNECTING";
        private String lastResponse = "";
        private String errorResponse = "";

        SafetyClient(URI uri) {
            super(uri);
        }

        private void block(String reason) {
            if (!stopLatched) {
                status = reason;
                errorResponse = lastResponse;
            }
            stopLatched = true;
            command = "STOP";
            power = 0;
        }

        synchronized void pollStatus() {
            long now = System.nanoTime();
            if ((pending && now - sentAt >= TIMEOUT_NS)
                    || (received && now - receivedAt >= TIMEOUT_NS)) {
                block("TIMEOUT - restart OpMode");
            }
            if (connected && !pending && now - sentAt >= POLL_NS) {
                pending = true;
                sentAt = now;
                try {
                    send("STATUS");
                } catch (RuntimeException e) {
                    block("SEND ERROR");
                }
            }
        }

        synchronized String currentCommand() {
            long now = System.nanoTime();
            if (!connected || !received || stopLatched || now >= expiresAt
                    || now - receivedAt >= TIMEOUT_NS) {
                return "STOP";
            }
            return command;
        }

        @Override
        public synchronized void onOpen(ServerHandshake handshake) {
            connected = true;
            if (!stopLatched) {
                status = "WAITING FOR DETECTION (drive v2)";
            }
        }

        @Override
        public synchronized void onMessage(String message) {
            lastResponse = message.replace('\n', ' ').replace('\r', ' ');
            if (lastResponse.length() > 220) {
                lastResponse = lastResponse.substring(0, 220) + "...";
            }
            long now = System.nanoTime();
            if (!pending || now - sentAt >= TIMEOUT_NS) {
                block("UNEXPECTED OR LATE RESPONSE");
                return;
            }
            pending = false;
            try {
                if (message.length() > 2048) {
                    block("OVERSIZED RESPONSE");
                    return;
                }
                JSONObject data = new JSONObject(message);
                if (!"drive".equals(data.optString("type")) || data.optInt("version", -1) != 2) {
                    block("PROTOCOL MISMATCH: need Python drive v2; update BOTH sides");
                    return;
                }
                for (String field : new String[]{"command", "fault", "power", "valid_for_ms", "reason"}) {
                    if (!data.has(field) || data.isNull(field)) {
                        block("MISSING FIELD: " + field);
                        return;
                    }
                }
                if (!(data.get("command") instanceof String) || !(data.get("reason") instanceof String)) {
                    block("INVALID FIELD TYPE: command/reason must be strings");
                    return;
                }
                String nextCommand = data.getString("command");
                String nextReason = data.getString("reason");
                Object faultValue = data.get("fault");
                Object powerValue = data.get("power");
                Object ttlValue = data.get("valid_for_ms");
                boolean moving = "FORWARD".equals(nextCommand) || "LEFT".equals(nextCommand)
                        || "RIGHT".equals(nextCommand);
                if (!"drive".equals(data.getString("type")) || data.getInt("version") != 2
                        || !(faultValue instanceof Boolean) || !(powerValue instanceof Number)
                        || !(ttlValue instanceof Number) || (!moving && !"STOP".equals(nextCommand))) {
                    block("INVALID RESPONSE");
                    return;
                }
                double nextPower = ((Number) powerValue).doubleValue();
                double ttl = ((Number) ttlValue).doubleValue();
                boolean fault = (Boolean) faultValue;
                if (Double.isNaN(nextPower) || Double.isInfinite(nextPower)
                        || nextPower < 0 || nextPower > 0.2
                        || Double.isNaN(ttl) || ttl < 0 || ttl > 500 || ttl != Math.floor(ttl)
                        || (moving && (nextPower <= 0 || ttl <= 0 || fault))
                        || (!moving && nextPower != 0)) {
                    block("INCONSISTENT RESPONSE");
                    return;
                }
                receivedAt = now;
                received = true;
                expiresAt = sentAt + (long) ttl * 1_000_000L;
                if (fault) {
                    block("DETECTOR FAULT - restart server and OpMode");
                } else if (!stopLatched) {
                    command = nextCommand;
                    power = nextPower;
                    status = nextCommand + " / " + nextReason;
                }
            } catch (JSONException e) {
                block("JSON ERROR: " + e.getMessage());
            } catch (RuntimeException e) {
                block("RESPONSE ERROR: " + e.getClass().getSimpleName());
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
