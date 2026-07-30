"""Speech recognition and TTS I/O."""

import difflib
import logging
import queue
import subprocess

import speech_recognition as sr

from app.config import (
    COMMAND_VOCAB,
    ENERGY_THRESHOLD,
    MIC_ALSA_CARD,
    MIC_ALSA_CONTROL,
    MIC_DEVICE_INDEX,
    NON_SPEAKING_DURATION,
    PAUSE_THRESHOLD,
    PHRASE_THRESHOLD,
    TTS_GAP,
    TTS_PITCH,
    TTS_SPEED,
    TTS_VOICE,
    DYNAMIC_ENERGY_THRESHOLD,
)


class SpeechIO:
    def __init__(self, logger: logging.Logger, set_busy_led):
        self.log = logger
        self.set_busy_led = set_busy_led
        self.audio_queue = queue.Queue()
        self._stop_background_listening = None
        self.is_speaking = False
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = ENERGY_THRESHOLD
        self.recognizer.dynamic_energy_threshold = DYNAMIC_ENERGY_THRESHOLD
        self.recognizer.pause_threshold = PAUSE_THRESHOLD
        self.recognizer.phrase_threshold = PHRASE_THRESHOLD
        self.recognizer.non_speaking_duration = NON_SPEAKING_DURATION
        self.microphone = sr.Microphone(device_index=MIC_DEVICE_INDEX)

    def calibrate(self):
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
        self.recognizer.energy_threshold = min(self.recognizer.energy_threshold, ENERGY_THRESHOLD)
        self.log.info(f"[Mic] Kalibriert, energy_threshold={self.recognizer.energy_threshold:.0f}")

    @staticmethod
    def _build_espeak_command(text: str) -> list[str]:
        return [
            "espeak",
            "-v",
            TTS_VOICE,
            "-s",
            str(TTS_SPEED),
            "-p",
            str(TTS_PITCH),
            "-g",
            str(TTS_GAP),
            text,
        ]

    def correct_command_words(self, text: str) -> str:
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
            self.log.debug(f"[Mic] Fuzzy-Korrektur: '{text}' -> '{corrected}'")
        return corrected

    def mute_mic(self, muted: bool):
        state = "mute" if muted else "unmute"
        try:
            subprocess.run(
                ["amixer", "-c", str(MIC_ALSA_CARD), "sset", MIC_ALSA_CONTROL, state],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            self.log.warning(f"Mikrofon {state} fehlgeschlagen: {error}")

    def say(self, text: str):
        self.log.info(f"[Claude] Antwort: {text}")
        if not text:
            return
        self.is_speaking = True
        self.log.debug("[Status] Antwortet ...")
        self.set_busy_led(True)
        try:
            subprocess.run(self._build_espeak_command(text), check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            self.log.warning(f"TTS fehlgeschlagen: {error}")
        finally:
            self.is_speaking = False
            self.set_busy_led(False)
            self.log.debug("[Status] Fertig geantwortet")

    def say_interruptible(self, text: str) -> str | None:
        self.log.info(f"[Claude] Antwort: {text}")
        if not text:
            return None

        self.stop_background_listening()
        self.is_speaking = True
        self.log.debug("[Status] Antwortet (Mikrofon stumm) ...")
        self.set_busy_led(True)
        self.mute_mic(True)

        try:
            subprocess.run(self._build_espeak_command(text), check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            self.log.warning(f"TTS fehlgeschlagen: {error}")
        finally:
            self.mute_mic(False)
            self.is_speaking = False
            self.set_busy_led(False)
            self.log.debug("[Status] Fertig geantwortet, Mikrofon wieder aktiv")

        result = None
        try:
            with self.microphone as source:
                audio = self.recognizer.listen(source, timeout=2, phrase_time_limit=8)
            heard = self.recognizer.recognize_google(audio, language="de-DE")
            if heard and heard.strip():
                self.log.info(f"[Mic] Direkte Anschlussfrage erkannt: '{heard}'")
                result = self.correct_command_words(heard)
        except (sr.WaitTimeoutError, sr.UnknownValueError, sr.RequestError):
            pass

        self.start_background_listening()
        return result

    def _recognition_callback(self, recognizer: sr.Recognizer, audio: sr.AudioData):
        duration = len(audio.frame_data) / (audio.sample_rate * audio.sample_width)
        self.log.debug(f"[Mic] Aufnahme erfasst: {duration:.2f}s, {audio.sample_rate}Hz")
        try:
            text = recognizer.recognize_google(audio, language="de-DE")
            self.log.info(f"[Mic] Erkannt: '{text}'")
            self.audio_queue.put(self.correct_command_words(text))
        except sr.UnknownValueError:
            try:
                alt = recognizer.recognize_google(audio, language="de-DE", show_all=True)
                self.log.debug(f"[Mic] Nichts eindeutig verstanden. Rohe Alternativen: {alt!r}")
            except sr.UnknownValueError:
                self.log.debug("[Mic] Nichts verstanden (auch keine Alternativen verfügbar - Audio wohl zu unklar/leise)")
            except sr.RequestError as error:
                self.log.debug(f"[Mic] Diagnose-Anfrage fehlgeschlagen: {error!r}")
        except sr.RequestError as error:
            self.log.error(f"Google Speech Recognition nicht erreichbar: {error}")

    def start_background_listening(self):
        self._stop_background_listening = self.recognizer.listen_in_background(
            self.microphone, self._recognition_callback, phrase_time_limit=8
        )
        self.log.debug("[Mic] Hintergrund-Listening gestartet")

    def stop_background_listening(self):
        if self._stop_background_listening is not None:
            self._stop_background_listening(wait_for_stop=True)
            self._stop_background_listening = None
            self.log.debug("[Mic] Hintergrund-Listening pausiert")

    def listen_once(self) -> str | None:
        with self.microphone as source:
            self.log.debug("[Mic] Höre zu ...")
            try:
                audio = self.recognizer.listen(source, timeout=6, phrase_time_limit=8)
            except sr.WaitTimeoutError:
                self.log.debug("[Mic] Timeout - keine Sprache erkannt (Energie nie über Schwellwert)")
                return None

        duration = len(audio.frame_data) / (audio.sample_rate * audio.sample_width)
        self.log.debug(f"[Mic] Aufnahme erfasst: {duration:.2f}s, {audio.sample_rate}Hz")

        try:
            text = self.recognizer.recognize_google(audio, language="de-DE")
            self.log.info(f"[Mic] Erkannt: '{text}'")
            return self.correct_command_words(text)
        except sr.UnknownValueError:
            try:
                alt = self.recognizer.recognize_google(audio, language="de-DE", show_all=True)
                self.log.debug(f"[Mic] Nichts eindeutig verstanden. Rohe Alternativen: {alt!r}")
            except sr.UnknownValueError:
                self.log.debug("[Mic] Nichts verstanden (auch keine Alternativen verfügbar - Audio wohl zu unklar/leise)")
            except sr.RequestError as error:
                self.log.debug(f"[Mic] Diagnose-Anfrage fehlgeschlagen: {error!r}")
            return None
        except sr.RequestError as error:
            self.log.error(f"Google Speech Recognition nicht erreichbar: {error}")
            return None
