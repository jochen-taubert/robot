#!/usr/bin/env python3
"""
measure_echo_energy.py

Misst die Mikrofon-Energie waehrend espeak spielt, um einen realistischen
INTERRUPT_ENERGY_THRESHOLD fuer robot.py zu bestimmen.

Ausfuehren waehrend absolute Stille im Raum (niemand spricht):
  python3 measure_echo_energy.py

Gibt den Maximalwert der Energie aus, die NUR vom Echo (Lautsprecher->Mikrofon)
stammt. INTERRUPT_ENERGY_THRESHOLD in robot.py sollte klar darueber liegen
(z.B. das 1.5-2-fache des gemessenen Maximums).
"""

import subprocess
import threading
import time

import speech_recognition as sr

MIC_DEVICE_INDEX = 1  # ggf. anpassen, wie in robot.py

recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = False
microphone = sr.Microphone(device_index=MIC_DEVICE_INDEX)

max_energy = 0
stop = False


def monitor():
    global max_energy, stop
    with microphone as source:
        while not stop:
            try:
                audio = recognizer.listen(source, timeout=1, phrase_time_limit=1)
                energy = audioop_rms(audio)
                if energy > max_energy:
                    max_energy = energy
                    print(f"  Aktuelle Spitze: {max_energy}")
            except sr.WaitTimeoutError:
                continue


def audioop_rms(audio: sr.AudioData) -> int:
    import audioop
    return audioop.rms(audio.get_raw_data(), audio.sample_width)


print("Starte Messung - espeak spricht jetzt einen Testsatz, bitte SELBST NICHTS SAGEN ...")
t = threading.Thread(target=monitor, daemon=True)
t.start()

time.sleep(1)
subprocess.run([
    "espeak", "-v", "de", "-s", "150", "-p", "40", "-g", "8",
    "Dies ist ein laengerer Testsatz, um die Echo-Energie zu messen, "
    "waehrend ich spreche und niemand im Raum antwortet."
])
time.sleep(1.5)

stop = True
t.join(timeout=2)

print()
print(f"Gemessene maximale Echo-Energie: {max_energy}")
print(f"Empfohlener INTERRUPT_ENERGY_THRESHOLD (2x Sicherheitsfaktor): {int(max_energy * 2)}")
