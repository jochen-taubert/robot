"""GPIO and motor control."""

import logging
import time

from robot_app.config import (
    CURVE_RATIO,
    DEFAULT_DURATION,
    DEFAULT_SPEED,
    MAX_DURATION,
    MOTOR_COMMANDS,
    PIN_LED_BUSY,
    PIN_LPWM_L,
    PIN_LPWM_R,
    PIN_RPWM_L,
    PIN_RPWM_R,
    PWM_FREQUENCY_HZ,
    TURN_45_DURATION,
)

try:
    import RPi.GPIO as GPIO

    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    GPIO = None


class MotorController:
    def __init__(self, logger: logging.Logger):
        self.log = logger
        self.pwm_rpwm_l = None
        self.pwm_lpwm_l = None
        self.pwm_rpwm_r = None
        self.pwm_lpwm_r = None

        if not GPIO_AVAILABLE:
            self.log.warning("RPi.GPIO nicht verfügbar - Simulationsmodus (kein echtes GPIO)")

    def setup(self):
        if not GPIO_AVAILABLE:
            return
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(PIN_RPWM_L, GPIO.OUT)
        GPIO.setup(PIN_LPWM_L, GPIO.OUT)
        GPIO.setup(PIN_RPWM_R, GPIO.OUT)
        GPIO.setup(PIN_LPWM_R, GPIO.OUT)
        GPIO.setup(PIN_LED_BUSY, GPIO.OUT)
        GPIO.output(PIN_LED_BUSY, GPIO.LOW)

        self.pwm_rpwm_l = GPIO.PWM(PIN_RPWM_L, PWM_FREQUENCY_HZ)
        self.pwm_lpwm_l = GPIO.PWM(PIN_LPWM_L, PWM_FREQUENCY_HZ)
        self.pwm_rpwm_r = GPIO.PWM(PIN_RPWM_R, PWM_FREQUENCY_HZ)
        self.pwm_lpwm_r = GPIO.PWM(PIN_LPWM_R, PWM_FREQUENCY_HZ)
        for pwm in (self.pwm_rpwm_l, self.pwm_lpwm_l, self.pwm_rpwm_r, self.pwm_lpwm_r):
            pwm.start(0)

    def set_led(self, busy: bool):
        if GPIO_AVAILABLE:
            GPIO.output(PIN_LED_BUSY, GPIO.HIGH if busy else GPIO.LOW)
        self.log.debug(f"[LED] {'AN (beschäftigt)' if busy else 'AUS (bereit zum Zuhören)'}")

    def set_motors(self, pwm_left: int, dir_left: str, pwm_right: int, dir_right: str):
        rpwm_l = pwm_left if dir_left == "HIGH" else 0
        lpwm_l = pwm_left if dir_left == "LOW" else 0
        rpwm_r = pwm_right if dir_right == "HIGH" else 0
        lpwm_r = pwm_right if dir_right == "LOW" else 0

        if GPIO_AVAILABLE:
            self.pwm_rpwm_l.ChangeDutyCycle(rpwm_l)
            self.pwm_lpwm_l.ChangeDutyCycle(lpwm_l)
            self.pwm_rpwm_r.ChangeDutyCycle(rpwm_r)
            self.pwm_lpwm_r.ChangeDutyCycle(lpwm_r)

        self.log.info(
            f"[GPIO] RPWM_L={rpwm_l:4d}%  LPWM_L={lpwm_l:4d}%  |  "
            f"RPWM_R={rpwm_r:4d}%  LPWM_R={lpwm_r:4d}%"
        )

    def stop(self):
        self.set_motors(0, "HIGH", 0, "HIGH")

    def drive(self, action: str, speed: int, duration: float):
        speed = max(0, min(100, int(speed or DEFAULT_SPEED)))
        duration = max(0.0, min(MAX_DURATION, float(duration or DEFAULT_DURATION)))
        self.log.info(f"[MOTOR] Steuersignal: action={action}  speed={speed}%  duration={duration}s")

        inner_speed = max(0, int(speed * CURVE_RATIO))

        self.set_led(True)
        try:
            if action == "TURN_LEFT" or action == "TURN_RIGHT":
                turn_time = min(TURN_45_DURATION, duration)
                if action == "TURN_LEFT":
                    self.set_motors(speed, "LOW", speed, "HIGH")
                else:
                    self.set_motors(speed, "HIGH", speed, "LOW")
                if turn_time > 0:
                    time.sleep(turn_time)

                remaining = duration - turn_time
                if remaining > 0:
                    self.set_motors(speed, "HIGH", speed, "HIGH")
                    time.sleep(remaining)
                self.stop()
                return

            if action in MOTOR_COMMANDS:
                dir_left, dir_right = MOTOR_COMMANDS[action]
                self.set_motors(speed, dir_left, speed, dir_right)
            elif action == "CURVE_LEFT":
                self.set_motors(inner_speed, "HIGH", speed, "HIGH")
            elif action == "CURVE_RIGHT":
                self.set_motors(speed, "HIGH", inner_speed, "HIGH")
            else:
                self.stop()
                return

            if duration > 0:
                time.sleep(duration)
                self.stop()
        finally:
            self.set_led(False)

    def cleanup(self):
        self.stop()
        if GPIO_AVAILABLE:
            GPIO.cleanup()
