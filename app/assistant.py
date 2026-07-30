"""Claude integration and local fallback parsing."""

import json
import logging
import re

from anthropic import APIConnectionError, APIError, Anthropic

from robot_app.config import (
    CLAUDE_MODEL,
    DEFAULT_DURATION,
    DEFAULT_SPEED,
    FALLBACK_PATTERNS,
    SYSTEM_PROMPT,
    VALID_ACTIONS,
)


class AssistantService:
    def __init__(self, logger: logging.Logger):
        self.log = logger
        self.client = Anthropic()

    @staticmethod
    def extract_json(text: str) -> dict:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        return json.loads(text)

    def fallback_parse(self, text: str) -> dict:
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

    def ask(self, user_text: str) -> dict:
        try:
            response = self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=200,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_text}],
            )
            raw = response.content[0].text
            data = self.extract_json(raw)

            if data.get("action") not in VALID_ACTIONS:
                data["action"] = "STOP"
                data["speed"] = 0
                data["duration"] = 0

            return data

        except (APIError, APIConnectionError) as error:
            self.log.error(f"Claude nicht erreichbar ({error}), nutze lokalen Fallback")
            return self.fallback_parse(user_text)
        except json.JSONDecodeError as error:
            self.log.error(f"Claude JSON Fehler: {error}")
            return self.fallback_parse(user_text)
