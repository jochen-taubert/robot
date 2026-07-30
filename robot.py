#!/usr/bin/env python3
"""
robot.py - Sprachgesteuerter Roboter (Raspberry Pi 3B)

Pipeline:
  Mikrofon -> Google Speech Recognition (de-DE) -> Claude Haiku (Intent-Parsing)
  -> gesprochene Antwort (TTS) + GPIO Motorsteuerung

Voraussetzungen:
  pip install SpeechRecognition anthropic RPi.GPIO --break-system-packages
  sudo apt install espeak flac   (espeak für TTS, flac für speech_recognition)

  export ANTHROPIC_API_KEY=sk-ant-...   (in ~/.bashrc, dann `source ~/.bashrc`)

Logs:
  Konsole + Datei unter ~/robot/logs/robot.log (rotiert bei 1 MB, 5 Backups)

Setup für automatisches Herunterfahren bei Inaktivität (siehe shutdown_pi()):
  Der Service-Nutzer braucht passwortlose sudo-Rechte NUR für 'shutdown', sonst
  scheitert der Befehl beim Ausführen ohne interaktives Terminal. Einrichten mit:
    sudo visudo -f /etc/sudoers.d/robot-shutdown
  Dort folgende Zeile eintragen (Nutzername anpassen):
    jostjansel ALL=(ALL) NOPASSWD: /sbin/shutdown, /usr/sbin/shutdown
"""

import difflib
import json
import logging
import logging.handlers
import os
import queue
import re
import subprocess
import sys
import time

import speech_recognition as sr
from anthropic import Anthropic, APIError, APIConnectionError

LOG_DIR = os.path.expanduser("~/robot/logs")
LOG_FILE = os.path.join(LOG_DIR, "robot.log")


def setup_logging() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)

    logger = logging.getLogger("robot")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Konsole: sichtbar beim manuellen Start und in `journalctl -u robot.service -f`
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    # Datei: rotiert bei 1 MB, behält 5 alte Logs, überlebt Neustarts
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


log = setup_logging()

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    log.warning("RPi.GPIO nicht verfügbar - Simulationsmodus (kein echtes GPIO)")
    GPIO_AVAILABLE = False

TTS_VOICE = "de"       # espeak Stimme, z.B. "de", "de+f3" für weibliche Variante
TTS_SPEED = 150         # Wörter/Minute (Standard 175, niedriger = ruhiger/weniger abgehackt)
TTS_PITCH = 40          # Tonhöhe 0-99 (Standard 50)
TTS_GAP = 8             # Pause zwischen Wörtern in 10ms-Einheiten (glättet den Rhythmus)

GREETING = "Hallo, ich bin dein Roboter DERO. D, E, R, O. Du kannst mir Bewegungsbefehle geben oder mir Fragen stellen."

# Nach dieser Zeit ganz ohne erkannte Sprache: einmalige Warnung, dann Abschalten,
# falls keine Reaktion kommt.
INACTIVITY_TIMEOUT = 60 * 60  # 60 Minuten in Sekunden
INACTIVITY_WARNING = "Ich schalte ab. Sag etwas, wenn ich weiterlaufen soll."
INACTIVITY_GOODBYE = "Keine Reaktion erhalten. Ich schalte jetzt ab. Bis bald."
INACTIVITY_GRACE_PERIOD = 15  # Sekunden Zeit zum Reagieren nach der Warnung

# Index des Mikrofons für sr.Microphone(). None = System-Default verwenden.
# Falls "No Default Input Device Available" auftritt, expliziten Index setzen -
# Liste verfügbarer Geräte ausgeben mit:
#   python3 -c "import speech_recognition as sr; [print(i, n) for i, n in enumerate(sr.Microphone.list_microphone_names())]"
MIC_DEVICE_INDEX = 1  # USB Microphone: Audio (hw:2,0) - bestätigt via list_audio_devices.py

# ALSA-Kontrolle zum Stummschalten des Mikrofons während der Wiedergabe (siehe unten) -
# verhindert zuverlässig, dass die eigene Stimme als Unterbrechung erkannt wird,
# da Schwellwert-Tuning allein sich als nicht robust genug erwiesen hat.
MIC_ALSA_CARD = 2
MIC_ALSA_CONTROL = "Mic"

# Empfindlichkeit der Spracherkennung: niedriger = empfindlicher (reagiert auf leisere
# Sprache, aber auch anfälliger für Hintergrundgeräusche). Startwert für die automatische
# Kalibrierung durch adjust_for_ambient_noise(); Standard bei speech_recognition ist 300.
ENERGY_THRESHOLD = 150

# Falls True, passt sich der Schwellwert laufend an Umgebungsgeräusche an (kann bei
# Sensitivitätsproblemen selbst zum Problem werden - False macht das Verhalten stabiler).
DYNAMIC_ENERGY_THRESHOLD = False
# Wie viel leiser als der Schwellwert eine Pause sein muss, um eine Phrase zu beenden.
PAUSE_THRESHOLD = 0.5
# Mindestdauer an Sprache (Sekunden), bevor überhaupt eine Phrase erkannt wird.
# Niedriger = kurze Wörter wie "stopp" werden eher erfasst.
PHRASE_THRESHOLD = 0.1
# Stille (Sekunden) die vor/nach der erkannten Sprache mit aufgenommen wird.
NON_SPEAKING_DURATION = 0.3


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

# GPIO-Pins (BCM-Nummerierung) - BTS7960-Schema: 2 PWM-fähige Pins pro Motor
# (RPWM = Vorwärts-PWM, LPWM = Rückwärts-PWM), kein separater Richtungspin.
# R_EN/L_EN beider Module fest an 5V verkabelt (nicht über GPIO gesteuert).
PIN_RPWM_L = 18  # Pin 12 - Vorwärts links
PIN_LPWM_L = 23  # Pin 16 - Rückwärts links
PIN_RPWM_R = 19  # Pin 35 - Vorwärts rechts
PIN_LPWM_R = 24  # Pin 18 - Rückwärts rechts

# Status-LED: leuchtet, solange der Roboter beschäftigt ist (spricht oder fährt) und
# KEINE neuen Sprachbefehle entgegennehmen kann. BCM25 = physischer Pin 22 (unbenutzt).
PIN_LED_BUSY = 25

PWM_FREQUENCY_HZ = 1000
DEFAULT_SPEED = 60          # Prozent, falls Claude keinen Wert liefert
DEFAULT_DURATION = 5.0      # Sekunden, falls Claude keinen Wert liefert

CLAUDE_MODEL = "claude-haiku-4-5-20251001"

VALID_ACTIONS = {"FORWARD", "BACKWARD", "LEFT", "RIGHT", "CURVE_LEFT", "CURVE_RIGHT", "TURN_LEFT", "TURN_RIGHT", "STOP", "SHUTDOWN"}

SYSTEM_PROMPT = """Du bist der Sprachassistent eines Roboters. Du bekommst gesprochene Nutzereingaben.

Antworte IMMER mit rohem JSON in exakt diesem Format, ohne Markdown, ohne Codeblock:
{"reply": "<natürliche Antwort auf die Frage/Aussage>", "action": "<FORWARD|BACKWARD|LEFT|RIGHT|CURVE_LEFT|CURVE_RIGHT|TURN_LEFT|TURN_RIGHT|STOP|SHUTDOWN>", "speed": <0-100>, "duration": <Sekunden, 0 wenn keine Bewegung>}

Regeln:
- "reply" beantwortet IMMER, was der Nutzer gesagt hat (Frage, Grußformel, Smalltalk, etc.)
- Bei Bewegungsbefehlen MUSS "reply" die tatsächliche Dauer nennen, mit dem passenden Verb:
  - FORWARD/BACKWARD: "fahren" (z.B. "Ich fahre 5 Sekunden vorwärts.")
  - LEFT/RIGHT (Drehung auf der Stelle, keine Vorwärtsbewegung): "drehen" (z.B. "Ich drehe
    10 Sekunden nach links.")
  - CURVE_LEFT/CURVE_RIGHT (durchgehende Kurvenfahrt): "fahre eine Kurve" (z.B. "Ich fahre
    5 Sekunden eine Kurve nach links.")
  - TURN_LEFT/TURN_RIGHT (kurz drehen, dann geradeaus): "fahre nach links/rechts" (z.B. "Ich
    fahre 5 Sekunden nach links.")
  Die genannte Zahl muss exakt dem "duration"-Wert entsprechen.
- "action" ist NUR dann etwas anderes als STOP, wenn der Nutzer explizit eine Bewegung verlangt
- Bei reiner Konversation ohne Bewegungsbefehl: action=STOP, speed=0, duration=0
- Der Roboter kann VIER Arten von Bewegung:
  (1) geradeaus fahren (FORWARD/BACKWARD)
  (2) auf der Stelle drehen, ohne sich vorwärts zu bewegen (LEFT/RIGHT)
  (3) durchgehend im Bogen fahren, Räder die ganze Zeit unterschiedlich schnell (CURVE_LEFT/
      CURVE_RIGHT)
  (4) kurz drehen (~45 Grad) und danach geradeaus weiterfahren (TURN_LEFT/TURN_RIGHT)
- Nutzer-Formulierungen:
  - "dreh dich nach links", "dreh nach links auf der Stelle" = LEFT
  - "fahr eine Kurve nach links", "fahr im Bogen nach links", "lenk nach links" = CURVE_LEFT
  - "fahr nach links" (ohne weiteren Kontext, Standardfall) = TURN_LEFT
  Analog für rechts.
- Wenn der Nutzer eine Zeitdauer nennt (z.B. "links 10 Sekunden", "5 Sekunden vorwärts",
  "eine halbe Minute rückwärts"), diese Zahl exakt in "duration" übernehmen (in Sekunden
  umgerechnet). Nennt der Nutzer KEINE Zeit, IMMER genau 5 Sekunden als duration verwenden.
- duration auf maximal 30 Sekunden begrenzen, auch wenn der Nutzer mehr verlangt (Sicherheit)
- SHUTDOWN NUR bei eindeutigem, explizitem Wunsch den Roboter/Pi abzuschalten. Erkenne dabei
  auch: "schalte dich ab", "fahr runter", "fahre runter", "schalte den Pi aus", "mach dich
  aus", "shut down" / "shutdown" (englisch). "Runter fahren"/"runter" im Kontext von
  Abschalten ist IMMER SHUTDOWN, nicht etwa BACKWARD - das ist keine Bewegungsrichtung.
  Bei SHUTDOWN: speed=0, duration=0, reply bestätigt kurz das Abschalten (z.B. "Ich schalte
  jetzt ab. Bis bald."). NIEMALS SHUTDOWN bei mehrdeutigen Formulierungen wie "stopp" oder
  "halt" auslösen - das bleibt STOP.
- speed zwischen 0 und 100 (Standard ca. 60, falls nicht anders angegeben oder aus Kontext
  wie "langsam"/"schnell" ableitbar)
"""

FALLBACK_PATTERNS = [
    (r"\b(schalte.*ab|fahr[e]?\s*runter|schalt.*aus|mach.*aus|shut\s*.?down)\b", "SHUTDOWN"),
    (r"\b(kurve|bogen).*(links)|links.*(kurve|bogen)\b", "CURVE_LEFT"),
    (r"\b(kurve|bogen).*(rechts)|rechts.*(kurve|bogen)\b", "CURVE_RIGHT"),
    (r"\bdreh.*(links)|links.*(auf der stelle)\b", "LEFT"),
    (r"\bdreh.*(rechts)|rechts.*(auf der stelle)\b", "RIGHT"),
    (r"\b(vorwärts|geradeaus|nach vorne)\b", "FORWARD"),
    (r"\b(rückwärts|zurück)\b", "BACKWARD"),
    (r"\b(links)\b", "TURN_LEFT"),
    (r"\b(rechts)\b", "TURN_RIGHT"),
    (r"\b(stopp|stop|halt|anhalten)\b", "STOP"),
]

# Bekannte Befehlswörter, gegen die jedes erkannte Wort per Fuzzy-Matching verglichen wird.
# Behebt kleine Fehlerkennungen von Google (z.B. "link" statt "links", "recht" statt
# "rechts") ohne die eigentliche Audio-Aufnahme zu beeinflussen.
COMMAND_VOCAB = [
    "links", "rechts", "vorwärts", "rückwärts", "geradeaus", "zurück",
    "stopp", "stop", "halt", "anhalten",
    "kurve", "bogen", "dreh", "drehen", "lenk", "lenken",
    "sekunde", "sekunden", "minute", "minuten", "fahre", "fahr",
]


def correct_command_words(text: str) -> str:
    """Ersetzt jedes Wort im erkannten Text durch das ähnlichste bekannte Befehlswort,
    falls die Ähnlichkeit hoch genug ist (>= 0.75). Sonst bleibt das Wort unverändert."""
    words = text.split()
    corrected_words = []
    changed = False
    for word in words:
        matches = difflib.get_close_matches(word.lower(), COMMAND_VOCAB, n=1, cutoff=0.75)
        if matches and matches[0] != word.lower():
            corrected_words.append(matches[0])
            changed = True
        else:
            corrected_words.append(word)
    corrected = " ".join(corrected_words)
    if changed:
        log.debug(f"[Mic] Fuzzy-Korrektur: '{text}' -> '{corrected}'")
    return corrected


# ---------------------------------------------------------------------------
# GPIO / Motorsteuerung
# ---------------------------------------------------------------------------

pwm_rpwm_l = None
pwm_lpwm_l = None
pwm_rpwm_r = None
pwm_lpwm_r = None


def setup_gpio():
    global pwm_rpwm_l, pwm_lpwm_l, pwm_rpwm_r, pwm_lpwm_r
    if not GPIO_AVAILABLE:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PIN_RPWM_L, GPIO.OUT)
    GPIO.setup(PIN_LPWM_L, GPIO.OUT)
    GPIO.setup(PIN_RPWM_R, GPIO.OUT)
    GPIO.setup(PIN_LPWM_R, GPIO.OUT)
    GPIO.setup(PIN_LED_BUSY, GPIO.OUT)
    GPIO.output(PIN_LED_BUSY, GPIO.LOW)

    pwm_rpwm_l = GPIO.PWM(PIN_RPWM_L, PWM_FREQUENCY_HZ)
    pwm_lpwm_l = GPIO.PWM(PIN_LPWM_L, PWM_FREQUENCY_HZ)
    pwm_rpwm_r = GPIO.PWM(PIN_RPWM_R, PWM_FREQUENCY_HZ)
    pwm_lpwm_r = GPIO.PWM(PIN_LPWM_R, PWM_FREQUENCY_HZ)
    for pwm in (pwm_rpwm_l, pwm_lpwm_l, pwm_rpwm_r, pwm_lpwm_r):
        pwm.start(0)


def set_led(busy: bool):
    """LED an, solange der Roboter beschäftigt ist (spricht/fährt) und nicht zuhören kann."""
    if GPIO_AVAILABLE:
        GPIO.output(PIN_LED_BUSY, GPIO.HIGH if busy else GPIO.LOW)
    log.debug(f"[LED] {'AN (beschäftigt)' if busy else 'AUS (bereit zum Zuhören)'}")


def set_motors(pwm_left, dir_left, pwm_right, dir_right):
    """Setzt Geschwindigkeit (0-100) und Richtung für beide Motoren.
    dir='HIGH' = vorwärts (RPWM aktiv), dir='LOW' = rückwärts (LPWM aktiv).
    BTS7960-Schema: pro Motor ist immer nur einer der beiden PWM-Kanäle aktiv,
    der jeweils andere liegt auf 0 - niemals beide gleichzeitig > 0."""
    rpwm_l = pwm_left if dir_left == "HIGH" else 0
    lpwm_l = pwm_left if dir_left == "LOW" else 0
    rpwm_r = pwm_right if dir_right == "HIGH" else 0
    lpwm_r = pwm_right if dir_right == "LOW" else 0

    if GPIO_AVAILABLE:
        pwm_rpwm_l.ChangeDutyCycle(rpwm_l)
        pwm_lpwm_l.ChangeDutyCycle(lpwm_l)
        pwm_rpwm_r.ChangeDutyCycle(rpwm_r)
        pwm_lpwm_r.ChangeDutyCycle(lpwm_r)

    log.info(f"[GPIO] RPWM_L={rpwm_l:4d}%  LPWM_L={lpwm_l:4d}%  |  "
             f"RPWM_R={rpwm_r:4d}%  LPWM_R={lpwm_r:4d}%")


def stop_motors():
    set_motors(0, "HIGH", 0, "HIGH")


MAX_DURATION = 30  # Sicherheits-Obergrenze in Sekunden, unabhängig davon was Claude liefert

# Bei Kurvenfahrt (CURVE_LEFT/CURVE_RIGHT): kurveninneres Rad bekommt diesen Anteil der
# Geschwindigkeit des äußeren Rads (0.0-1.0). Niedriger = engere Kurve.
CURVE_RATIO = 0.4

# Bei TURN_LEFT/TURN_RIGHT: wie lange (Sekunden) auf der Stelle gedreht wird, bevor geradeaus
# weitergefahren wird. MUSS empirisch kalibriert werden - hängt von Speed, Motoren, Untergrund
# ab. Zum Kalibrieren: Wert testweise setzen, "fahr nach links" sagen, mit Winkelmesser/Augenmaß
# prüfen ob ~45 Grad erreicht wurden, Wert anpassen.
TURN_45_DURATION = 0.5

MOTOR_COMMANDS = {
    "FORWARD": ("HIGH", "HIGH"),
    "BACKWARD": ("LOW", "LOW"),
    "LEFT": ("LOW", "HIGH"),
    "RIGHT": ("HIGH", "LOW"),
}


def drive(action: str, speed: int, duration: float):
    """Führt eine Bewegung aus und stoppt danach automatisch."""
    speed = max(0, min(100, int(speed or DEFAULT_SPEED)))
    duration = max(0.0, min(MAX_DURATION, float(duration or DEFAULT_DURATION)))

    log.info(f"[MOTOR] Steuersignal: action={action}  speed={speed}%  duration={duration}s")

    inner_speed = max(0, int(speed * CURVE_RATIO))

    set_led(True)
    try:
        if action == "TURN_LEFT" or action == "TURN_RIGHT":
            # Zweiphasige Bewegung: erst kurz auf der Stelle drehen (~45 Grad), dann geradeaus
            turn_time = min(TURN_45_DURATION, duration)
            if action == "TURN_LEFT":
                set_motors(speed, "LOW", speed, "HIGH")
            else:
                set_motors(speed, "HIGH", speed, "LOW")
            if turn_time > 0:
                time.sleep(turn_time)

            remaining = duration - turn_time
            if remaining > 0:
                set_motors(speed, "HIGH", speed, "HIGH")
                time.sleep(remaining)
            stop_motors()
            return

        if action in MOTOR_COMMANDS:
            dir_left, dir_right = MOTOR_COMMANDS[action]
            set_motors(speed, dir_left, speed, dir_right)
        elif action == "CURVE_LEFT":
            # Beide Räder vorwärts, linkes (kurveninneres) Rad langsamer -> Kurve nach links
            set_motors(inner_speed, "HIGH", speed, "HIGH")
        elif action == "CURVE_RIGHT":
            # Beide Räder vorwärts, rechtes (kurveninneres) Rad langsamer -> Kurve nach rechts
            set_motors(speed, "HIGH", inner_speed, "HIGH")
        else:  # STOP oder unbekannt
            stop_motors()
            return

        if duration > 0:
            time.sleep(duration)
            stop_motors()
    finally:
        set_led(False)


# ---------------------------------------------------------------------------
# TTS (espeak direkt via subprocess, kein pyttsx3)
# ---------------------------------------------------------------------------

def setup_tts():
    pass  # kein Setup nötig, espeak wird pro Aufruf direkt gestartet


is_speaking = False


def _build_espeak_command(text: str) -> list[str]:
    return [
        "espeak",
        "-v", TTS_VOICE,
        "-s", str(TTS_SPEED),
        "-p", str(TTS_PITCH),
        "-g", str(TTS_GAP),
        text,
    ]


def say(text: str):
    """Einfaches, nicht unterbrechbares Sprechen (z.B. für die Begrüßung)."""
    global is_speaking
    log.info(f"[Claude] Antwort: {text}")
    if not text:
        return

    is_speaking = True
    log.debug("[Status] Antwortet ...")
    set_led(True)
    try:
        subprocess.run(_build_espeak_command(text), check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        log.warning(f"TTS fehlgeschlagen: {e}")
    finally:
        is_speaking = False
        set_led(False)
        log.debug("[Status] Fertig geantwortet")


def mute_mic(muted: bool):
    state = "mute" if muted else "unmute"
    try:
        subprocess.run(
            ["amixer", "-c", str(MIC_ALSA_CARD), "sset", MIC_ALSA_CONTROL, state],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        log.warning(f"Mikrofon {state} fehlgeschlagen: {e}")


def say_interruptible(text: str, recognizer: sr.Recognizer, microphone: sr.Microphone) -> str | None:
    """
    Spricht 'text'. Das Mikrofon wird waehrend der Wiedergabe hardware-seitig
    stummgeschaltet, um zu verhindern, dass die eigene Stimme (ohne Echo-Cancellation)
    faelschlich als Unterbrechung erkannt wird - Schwellwert-basierte Erkennung allein
    war dafuer nicht zuverlaessig genug.

    Direkt NACH Ende der Wiedergabe wird kurz auf eine sofortige Anschlussfrage gehoert,
    bevor in die normale Listen-Schleife zurueckgekehrt wird. Echtes Unterbrechen
    MITTEN im Satz ist damit nicht mehr moeglich, dafuer keine Fehltrigger mehr.
    """
    global is_speaking
    log.info(f"[Claude] Antwort: {text}")
    if not text:
        return None

    stop_background_listening()

    is_speaking = True
    log.debug("[Status] Antwortet (Mikrofon stumm) ...")
    set_led(True)
    mute_mic(True)

    try:
        subprocess.run(_build_espeak_command(text), check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        log.warning(f"TTS fehlgeschlagen: {e}")
    finally:
        mute_mic(False)
        is_speaking = False
        set_led(False)
        log.debug("[Status] Fertig geantwortet, Mikrofon wieder aktiv")

    # Kurzes Zeitfenster direkt nach der Antwort, um eine sofortige Anschlussfrage
    # ohne Wartezeit aufzunehmen
    result = None
    try:
        with microphone as source:
            audio = recognizer.listen(source, timeout=2, phrase_time_limit=8)
        heard = recognizer.recognize_google(audio, language="de-DE")
        if heard and heard.strip():
            log.info(f"[Mic] Direkte Anschlussfrage erkannt: '{heard}'")
            result = correct_command_words(heard)
    except (sr.WaitTimeoutError, sr.UnknownValueError, sr.RequestError):
        pass

    start_background_listening(recognizer, microphone)
    return result


# ---------------------------------------------------------------------------
# Claude-Anbindung
# ---------------------------------------------------------------------------

client = Anthropic()  # liest ANTHROPIC_API_KEY aus der Umgebung


def extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def fallback_parse(text: str) -> dict:
    text_lower = text.lower()
    for pattern, action in FALLBACK_PATTERNS:
        if re.search(pattern, text_lower):
            return {
                "reply": f"(offline) Befehl erkannt: {action}",
                "action": action,
                "speed": DEFAULT_SPEED if action != "STOP" else 0,
                "duration": DEFAULT_DURATION if action != "STOP" else 0,
            }
    return {
        "reply": "Verbindung zu Claude fehlgeschlagen und kein Bewegungsbefehl erkannt.",
        "action": "STOP",
        "speed": 0,
        "duration": 0,
    }


def ask_claude(user_text: str) -> dict:
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
        )
        raw = response.content[0].text
        data = extract_json(raw)

        if data.get("action") not in VALID_ACTIONS:
            data["action"] = "STOP"
            data["speed"] = 0
            data["duration"] = 0

        return data

    except (APIError, APIConnectionError) as e:
        log.error(f"Claude nicht erreichbar ({e}), nutze lokalen Fallback")
        return fallback_parse(user_text)
    except json.JSONDecodeError as e:
        log.error(f"Claude JSON Fehler: {e}")
        return fallback_parse(user_text)


# ---------------------------------------------------------------------------
# Spracherkennung
# ---------------------------------------------------------------------------

audio_queue = queue.Queue()
_stop_background_listening = None


def _recognition_callback(recognizer: sr.Recognizer, audio: sr.AudioData):
    """Wird vom Hintergrund-Listener für jede erfasste Phrase aufgerufen (eigener Thread)."""
    duration = len(audio.frame_data) / (audio.sample_rate * audio.sample_width)
    log.debug(f"[Mic] Aufnahme erfasst: {duration:.2f}s, {audio.sample_rate}Hz")

    try:
        text = recognizer.recognize_google(audio, language="de-DE")
        log.info(f"[Mic] Erkannt: '{text}'")
        text = correct_command_words(text)
        audio_queue.put(text)
    except sr.UnknownValueError:
        try:
            alt = recognizer.recognize_google(audio, language="de-DE", show_all=True)
            log.debug(f"[Mic] Nichts eindeutig verstanden. Rohe Alternativen: {alt!r}")
        except sr.UnknownValueError:
            log.debug("[Mic] Nichts verstanden (auch keine Alternativen verfügbar - Audio wohl zu unklar/leise)")
        except sr.RequestError as e:
            log.debug(f"[Mic] Diagnose-Anfrage fehlgeschlagen: {e!r}")
    except sr.RequestError as e:
        log.error(f"Google Speech Recognition nicht erreichbar: {e}")


def start_background_listening(recognizer: sr.Recognizer, microphone: sr.Microphone):
    """Startet kontinuierliches Zuhören in einem Hintergrund-Thread. Der Mikrofon-Stream
    bleibt dabei durchgehend offen, statt bei jedem Aufruf neu geöffnet zu werden - das
    verhindert, dass sehr kurze/schnell gesprochene Wörter am Anfang abgeschnitten werden."""
    global _stop_background_listening
    _stop_background_listening = recognizer.listen_in_background(
        microphone, _recognition_callback, phrase_time_limit=8
    )
    log.debug("[Mic] Hintergrund-Listening gestartet")


def stop_background_listening():
    """Pausiert das Hintergrund-Listening - nötig, bevor woanders im Code direkt auf das
    Mikrofon zugegriffen wird (z.B. während TTS-Wiedergabe oder der Inaktivitäts-Prüfung),
    da sonst zwei gleichzeitige Zugriffe auf denselben Mikrofon-Stream kollidieren würden."""
    global _stop_background_listening
    if _stop_background_listening is not None:
        _stop_background_listening(wait_for_stop=True)
        _stop_background_listening = None
        log.debug("[Mic] Hintergrund-Listening pausiert")


def listen(recognizer: sr.Recognizer, microphone: sr.Microphone) -> str | None:
    """Einzelner, synchroner Höraufruf - wird nur noch verwendet, wenn kein Hintergrund-
    Listening aktiv ist (z.B. während der Inaktivitäts-Gnadenfrist)."""
    with microphone as source:
        log.debug("[Mic] Höre zu ...")
        try:
            audio = recognizer.listen(source, timeout=6, phrase_time_limit=8)
        except sr.WaitTimeoutError:
            log.debug("[Mic] Timeout - keine Sprache erkannt (Energie nie über Schwellwert)")
            return None

    duration = len(audio.frame_data) / (audio.sample_rate * audio.sample_width)
    log.debug(f"[Mic] Aufnahme erfasst: {duration:.2f}s, {audio.sample_rate}Hz")

    try:
        text = recognizer.recognize_google(audio, language="de-DE")
        log.info(f"[Mic] Erkannt: '{text}'")
        text = correct_command_words(text)
        return text
    except sr.UnknownValueError:
        # Diagnose: war überhaupt Audio da, das Google nicht verstanden hat,
        # oder kam gar nichts Sinnvolles an?
        try:
            alt = recognizer.recognize_google(audio, language="de-DE", show_all=True)
            log.debug(f"[Mic] Nichts eindeutig verstanden. Rohe Alternativen: {alt!r}")
        except sr.UnknownValueError:
            log.debug("[Mic] Nichts verstanden (auch keine Alternativen verfügbar - Audio wohl zu unklar/leise)")
        except sr.RequestError as e:
            log.debug(f"[Mic] Diagnose-Anfrage fehlgeschlagen: {e!r}")
        return None
    except sr.RequestError as e:
        log.error(f"Google Speech Recognition nicht erreichbar: {e}")
        return None


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def shutdown_pi():
    """Fährt den Raspberry Pi komplett herunter. Erfordert passwortlose sudo-Berechtigung
    für 'shutdown' (siehe SKILL/Setup-Hinweis in der Doku dieser Datei)."""
    log.info("[Shutdown] Fahre Raspberry Pi jetzt herunter ...")
    try:
        subprocess.run(["sudo", "shutdown", "-h", "now"], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        log.error(f"[Shutdown] Herunterfahren fehlgeschlagen: {e}")


def main():
    setup_gpio()
    setup_tts()

    recognizer = sr.Recognizer()
    recognizer.energy_threshold = ENERGY_THRESHOLD
    recognizer.dynamic_energy_threshold = DYNAMIC_ENERGY_THRESHOLD
    recognizer.pause_threshold = PAUSE_THRESHOLD
    recognizer.phrase_threshold = PHRASE_THRESHOLD
    recognizer.non_speaking_duration = NON_SPEAKING_DURATION
    microphone = sr.Microphone(device_index=MIC_DEVICE_INDEX)

    with microphone as source:
        recognizer.adjust_for_ambient_noise(source, duration=1)
    # Automatische Kalibrierung nie über unserer Sensitivitäts-Obergrenze zulassen
    recognizer.energy_threshold = min(recognizer.energy_threshold, ENERGY_THRESHOLD)
    log.info(f"[Mic] Kalibriert, energy_threshold={recognizer.energy_threshold:.0f}")

    say(GREETING)

    start_background_listening(recognizer, microphone)
    log.info("Roboter bereit. Sprich einen Befehl oder eine Frage (Strg+C zum Beenden).")

    try:
        pending_text = None
        last_activity = time.time()

        while True:
            if pending_text is not None:
                spoken_text = pending_text
                pending_text = None
            else:
                try:
                    spoken_text = audio_queue.get(timeout=1)
                except queue.Empty:
                    spoken_text = None

            if not spoken_text:
                # Keine Sprache erkannt - Inaktivitätsdauer prüfen
                if time.time() - last_activity >= INACTIVITY_TIMEOUT:
                    log.info(f"[Inaktivität] {INACTIVITY_TIMEOUT}s ohne Aktivität - Warnung ausgeben")
                    stop_background_listening()
                    say(INACTIVITY_WARNING)

                    response = None
                    grace_start = time.time()
                    while time.time() - grace_start < INACTIVITY_GRACE_PERIOD:
                        try:
                            response = listen(recognizer, microphone)
                        except Exception:
                            response = None
                        if response:
                            break

                    if response:
                        log.info(f"[Inaktivität] Reaktion erhalten: '{response}' - bleibe an")
                        last_activity = time.time()
                        pending_text = response
                        start_background_listening(recognizer, microphone)
                        continue
                    else:
                        log.info("[Inaktivität] Keine Reaktion - Pi wird heruntergefahren")
                        say(INACTIVITY_GOODBYE)
                        shutdown_pi()
                        break
                continue

            last_activity = time.time()

            log.info(f"[Claude] Sende: '{spoken_text}'")
            result = ask_claude(spoken_text)

            interrupt_text = say_interruptible(result.get("reply", ""), recognizer, microphone)

            action = result.get("action", "STOP")
            speed = result.get("speed", 0)
            duration = result.get("duration", 0)

            if interrupt_text:
                # Nutzer hat unterbrochen - Motoren aus Sicherheitsgründen stoppen,
                # neue Eingabe direkt in der nächsten Runde verarbeiten
                stop_motors()
                pending_text = interrupt_text
                last_activity = time.time()
            elif action == "SHUTDOWN":
                log.info("[Shutdown] Per Sprachbefehl ausgelöst")
                stop_motors()
                shutdown_pi()
                break
            elif action != "STOP" and speed > 0:
                drive(action, speed, duration)
            else:
                stop_motors()

    except KeyboardInterrupt:
        log.info("Beende Roboter ...")
    except Exception as e:
        log.exception(f"Unerwarteter Fehler: {e!r}")
        raise
    finally:
        stop_background_listening()
        stop_motors()
        if GPIO_AVAILABLE:
            GPIO.cleanup()


if __name__ == "__main__":
    main()
