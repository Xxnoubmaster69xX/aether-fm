"""
Persistent Broadcaster Settings Store (settings_store.py)
Atomically persists user settings (voice matrix, active persona, LLM engine/mode,
DJ commentary toggle, active mood card/channel, listening profile) so they survive
server restarts, crashes, and reboots.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from config import BASE_DIR

logger = logging.getLogger("aether_fm.settings")

SETTINGS_FILE = BASE_DIR / "data" / "station_settings.json"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "active_character_id": "the_herta",
    "voice_profile_id": "the_herta",
    "voice_config": {
        "id": "the_herta",
        "name": "THE HERTA // GENIUS #83",
        "voice_a": "af_heart",
        "voice_b": "af_bella",
        "mix_a": 0.72,
        "speed": 0.97,
        "pitch_semitones": 0.3,
        "lang": "en-us",
        "engine": "kokoro",
    },
    "llm_mode": "hybrid_deepseek",
    "llm_model": "deepseek-r1:1.5b",
    "dj_enabled": True,
    "active_card_id": "card_herta_overdrive",
    "listening_profile": "flat",  # 'car4', 'car2', 'phones', 'flat'
}


class StationSettingsStore:
    def __init__(self, file_path: Path = SETTINGS_FILE):
        self.file_path = file_path
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.file_path.exists():
            try:
                content = self.file_path.read_text(encoding="utf-8")
                loaded = json.loads(content)
                merged = dict(DEFAULT_SETTINGS)
                merged.update(loaded)
                return merged
            except Exception as e:
                logger.error(f"Failed to load station settings from {self.file_path}: {e}")
        return dict(DEFAULT_SETTINGS)

    def save(self) -> None:
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.file_path.with_suffix(".tmp")
            temp_file.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            temp_file.replace(self.file_path)
        except Exception as e:
            logger.error(f"Failed to persist station settings: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any, auto_save: bool = True) -> None:
        self._data[key] = value
        if auto_save:
            self.save()

    def update(self, mapping: Dict[str, Any], auto_save: bool = True) -> None:
        self._data.update(mapping)
        if auto_save:
            self.save()

    def get_all(self) -> Dict[str, Any]:
        return dict(self._data)


settings_store = StationSettingsStore()
