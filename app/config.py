"""Central configuration for the robot application."""

import os

LOG_DIR = os.path.expanduser("~/robot/logs")
LOG_FILE = os.path.join(LOG_DIR, "robot.log")

TTS_VOICE = "de"
TTS_SPEED = 150
TTS_PITCH = 40
TTS_GAP = 8

GREETING = "Hallo, ich bin dein Roboter DERO. D, E, R, O. Du kannst mir Bewegungsbefehle geben oder mir Fragen stellen."

INACTIVITY_TIMEOUT = 60 * 60
INACTIVITY_WARNING = "Ich schalte ab. Sag etwas, wenn ich weiterlaufen soll."
INACTIVITY_GOODBYE = "Keine Reaktion erhalten. Ich schalte jetzt ab. Bis bald."
INACTIVITY_GRACE_PERIOD = 15

MIC_DEVICE_INDEX = 1
MIC_ALSA_CARD = 2
MIC_ALSA_CONTROL = "Mic"

ENERGY_THRESHOLD = 150
DYNAMIC_ENERGY_THRESHOLD = False
PAUSE_THRESHOLD = 0.5
PHRASE_THRESHOLD = 0.1
NON_SPEAKING_DURATION = 0.3

PIN_RPWM_L = 18
PIN_LPWM_L = 23
PIN_RPWM_R = 19
PIN_LPWM_R = 24
PIN_LED_BUSY = 25

PWM_FREQUENCY_HZ = 1000
DEFAULT_SPEED = 60
DEFAULT_DURATION = 5.0
MAX_DURATION = 30
CURVE_RATIO = 0.4
TURN_45_DURATION = 0.5

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
VALID_ACTIONS = {
    "FORWARD",
    "BACKWARD",
    "LEFT",
    "RIGHT",
    "CURVE_LEFT",
    "CURVE_RIGHT",
    "TURN_LEFT",
    "TURN_RIGHT",
    "STOP",
    "SHUTDOWN",
}

MOTOR_COMMANDS = {
    "FORWARD": ("HIGH", "HIGH"),
    "BACKWARD": ("LOW", "LOW"),
    "LEFT": ("LOW", "HIGH"),
    "RIGHT": ("HIGH", "LOW"),
}

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

COMMAND_VOCAB = [
    "links",
    "rechts",
    "vorwärts",
    "rückwärts",
    "geradeaus",
    "zurück",
    "stopp",
    "stop",
    "halt",
    "anhalten",
    "kurve",
    "bogen",
    "dreh",
    "drehen",
    "lenk",
    "lenken",
    "sekunde",
    "sekunden",
    "minute",
    "minuten",
    "fahre",
    "fahr",
]
