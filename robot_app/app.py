"""Main application orchestration."""

import queue
import subprocess
import time

from robot_app.assistant import AssistantService
from robot_app.config import (
    GREETING,
    INACTIVITY_GOODBYE,
    INACTIVITY_GRACE_PERIOD,
    INACTIVITY_TIMEOUT,
    INACTIVITY_WARNING,
)
from robot_app.logging_setup import setup_logging
from robot_app.motor import MotorController
from robot_app.speech import SpeechIO


class RobotApp:
    def __init__(self):
        self.log = setup_logging()
        self.motor = MotorController(self.log)
        self.speech = SpeechIO(self.log, self.motor.set_led)
        self.assistant = AssistantService(self.log)

    def shutdown_pi(self):
        self.log.info("[Shutdown] Fahre Raspberry Pi jetzt herunter ...")
        try:
            subprocess.run(["sudo", "shutdown", "-h", "now"], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            self.log.error(f"[Shutdown] Herunterfahren fehlgeschlagen: {error}")

    def run(self):
        self.motor.setup()
        self.speech.calibrate()
        self.speech.say(GREETING)

        self.speech.start_background_listening()
        self.log.info("Roboter bereit. Sprich einen Befehl oder eine Frage (Strg+C zum Beenden).")

        try:
            pending_text = None
            last_activity = time.time()

            while True:
                if pending_text is not None:
                    spoken_text = pending_text
                    pending_text = None
                else:
                    try:
                        spoken_text = self.speech.audio_queue.get(timeout=1)
                    except queue.Empty:
                        spoken_text = None

                if not spoken_text:
                    if time.time() - last_activity >= INACTIVITY_TIMEOUT:
                        self.log.info(f"[Inaktivität] {INACTIVITY_TIMEOUT}s ohne Aktivität - Warnung ausgeben")
                        self.speech.stop_background_listening()
                        self.speech.say(INACTIVITY_WARNING)

                        response = None
                        grace_start = time.time()
                        while time.time() - grace_start < INACTIVITY_GRACE_PERIOD:
                            try:
                                response = self.speech.listen_once()
                            except Exception:
                                response = None
                            if response:
                                break

                        if response:
                            self.log.info(f"[Inaktivität] Reaktion erhalten: '{response}' - bleibe an")
                            last_activity = time.time()
                            pending_text = response
                            self.speech.start_background_listening()
                            continue

                        self.log.info("[Inaktivität] Keine Reaktion - Pi wird heruntergefahren")
                        self.speech.say(INACTIVITY_GOODBYE)
                        self.shutdown_pi()
                        break
                    continue

                last_activity = time.time()
                self.log.info(f"[Claude] Sende: '{spoken_text}'")
                result = self.assistant.ask(spoken_text)
                interrupt_text = self.speech.say_interruptible(result.get("reply", ""))

                action = result.get("action", "STOP")
                speed = result.get("speed", 0)
                duration = result.get("duration", 0)

                if interrupt_text:
                    self.motor.stop()
                    pending_text = interrupt_text
                    last_activity = time.time()
                elif action == "SHUTDOWN":
                    self.log.info("[Shutdown] Per Sprachbefehl ausgelöst")
                    self.motor.stop()
                    self.shutdown_pi()
                    break
                elif action != "STOP" and speed > 0:
                    self.motor.drive(action, speed, duration)
                else:
                    self.motor.stop()

        except KeyboardInterrupt:
            self.log.info("Beende Roboter ...")
        except Exception as error:
            self.log.exception(f"Unerwarteter Fehler: {error!r}")
            raise
        finally:
            self.speech.stop_background_listening()
            self.motor.cleanup()
