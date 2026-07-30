## Robot Voice Control

Sprachgesteuerter Roboter für Raspberry Pi mit folgender Pipeline:

- Mikrofon → Google Speech Recognition (`de-DE`)
- Intent-Parsing über Claude
- Antwort über `espeak`
- Motorsteuerung über GPIO

## Architektur

Das Projekt ist in klar getrennte Module aufgeteilt:

- `robot.py` – schlanker Einstiegspunkt
- `app/app.py` – Orchestrierung des Laufzeitflusses
- `app/config.py` – zentrale Konfiguration und Konstanten
- `app/logging_setup.py` – Logging-Konfiguration (Konsole + rotierende Datei)
- `app/motor.py` – GPIO-/Motorsteuerung und Bewegungslogik
- `app/speech.py` – Spracherkennung, Queue, TTS und Mikrofon-Mute
- `app/assistant.py` – Claude-Integration + lokaler Regex-Fallback

## Voraussetzungen

- Python 3.10+
- Systempakete: `espeak`, `flac`
- Python-Pakete: `SpeechRecognition`, `anthropic`, `RPi.GPIO` (auf Raspberry Pi)

Beispiel:

```bash
sudo apt install -y espeak flac
pip install SpeechRecognition anthropic RPi.GPIO --break-system-packages
```

API-Key setzen:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Start

```bash
python3 robot.py
```

## Logs

- Datei: `~/robot/logs/robot.log`
- Rotation: 1 MB, 5 Backups
