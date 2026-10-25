"""
Multi-Voice DJ Synthesis & Style-Blender Engine (tts_voice.py)
Supports:
1. Instant Voice Profile switching & custom 2-voice blending across all 54 Kokoro v1.0 voices.
2. Adjustable speed, pitch shift (semitones), and language ('en-us', 'en-gb', 'ja').
3. High-quality `soxr` resampling (24kHz -> 48kHz) with zero FFT ringing.
4. Non-blocking `asyncio.to_thread` execution so the audio mixer never stutters during TTS.
5. Optional GPT-SoVITS v2 Zero-Shot API backend.
"""

import asyncio
import io
import os
import re
from typing import Any, Dict, List, Optional
import httpx
import numpy as np
import soundfile as sf
import soxr

from config import (
    BASE_DIR,
    CHANNELS,
    GPT_SOVITS_API_URL,
    HERTA_REF_AUDIO_MAP,
    KOKORO_MODEL_PATH,
    KOKORO_VOICES_PATH,
    SAMPLE_RATE,
)
from ai_booth.persona_marketplace import BUILTIN_CHARACTER_CATALOG

# Phonetic pronunciation dictionary for stylized / acronym-like artist & music terms
# Prevents espeak-ng from spelling out ALL-CAPS or punctuated names letter-by-letter!
PHONETIC_REPLACEMENTS: List[tuple[str, str]] = [
    (r"\bYOASOBI\b", "Yo-ah-sobi"),
    (r"\byoasobi\b", "Yo-ah-sobi"),
    (r"\bZUTOMAYO\b", "Zuto-mayo"),
    (r"\bzutomayo\b", "Zuto-mayo"),
    (r"\bDENONBU\b", "Den-on-bu"),
    (r"\bdenonbu\b", "Den-on-bu"),
    (r"\bKOTONOHOUSE\b", "Kotono-house"),
    (r"\bSPYAIR\b", "Spy-air"),
    (r"\bspyair\b", "Spy-air"),
    (r"\bKANA-BOON\b", "Kana-Boon"),
    (r"\bONE OK ROCK\b", "One Okay Rock"),
    (r"\bm\.o\.v\.e\b", "Move"),
    (r"\bSrav3R\b", "Sraver"),
    (r"\bsrav3r\b", "Sraver"),
    (r"\bUSAO\b", "Oo-sao"),
    (r"\bP\*Light\b", "Pee-Light"),
    (r"\bt\+pazolite\b", "Tee-Pazolite"),
    (r"\br-906\b", "R nine-oh-six"),
    (r"\bHOYO-MiX\b", "Hoyo-Mix"),
    (r"\bSān-Z\b", "San-Zee"),
    (r"\bSawanoHiroyuki\[nZk\]\b", "Hiroyuki Sawano"),
    (r"\bDECO\*27\b", "Deco Twenty-Seven"),
    (r"\bHARDCORE TANO\*C\b", "Hardcore Tano-See"),
    (r"\bTANO\*C\b", "Tano-See"),
    (r"\bSTMPD RCRDS\b", "Stamped Records"),
    (r"\bSTMPD\b", "Stamped"),
    (r"\bISOxo\b", "Iso-exo"),
    (r"\bKnock2\b", "Knock Two"),
    (r"\bGo2\b", "Go Two"),
    (r"\b2B\b", "Two-Bee"),
    (r"\bYoRHa\b", "Yor-ha"),
    (r"\bLiSA\b", "Lisa"),
    (r"\bm-flo\b", "Em-Flo"),
    (r"\bBPM\b", "beats per minute"),
    (r"\bLUFS\b", "decibels"),
    (r"\bJ-Pop\b", "Jay-pop"),
    (r"\bJ-pop\b", "Jay-pop"),
    (r"\bJ-Rock\b", "Jay-rock"),
    (r"\bJ-rock\b", "Jay-rock"),
    (r"\bJ-Core\b", "Jay-core"),
    (r"\bJ-core\b", "Jay-core"),
    (r"\bOST\b", "soundtrack"),
    (r"\bOP\b", "opening"),
    (r"\bED\b", "ending"),
    (r"\bCV:\s*", "voiced by "),
    (r"\bfeat\.\s*", "featuring "),
    (r"\bft\.\s*", "featuring "),
    (r"\bVer\.\b", "Version"),
]


_KAKASI = None


def _romanize_cjk(text: str) -> str:
    """
    Converts any Japanese Kanji/Hiragana/Katakana or Chinese characters into clean
    spoken Romaji/Latin words so espeak-ng NEVER says 'chinese letter' or 'japanese letter'!
    """
    global _KAKASI
    if not re.search(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef]", text):
        return text
    try:
        if _KAKASI is None:
            import pykakasi

            _KAKASI = pykakasi.kakasi()
        # Replace contiguous CJK runs with their Hepburn Romaji pronunciation
        def _repl_cjk(match: re.Match) -> str:
            chunk = match.group(0)
            converted = _KAKASI.convert(chunk)
            romaji = " ".join(item.get("hepburn", "") for item in converted if item.get("hepburn"))
            return f" {romaji} "

        text = re.sub(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", _repl_cjk, text)
    except Exception:
        pass
    try:
        from anyascii import anyascii

        text = anyascii(text)
    except Exception:
        text = text.encode("ascii", errors="ignore").decode("ascii")
    return text


def normalize_text_for_tts(raw_text: str) -> str:
    """
    Cleans and phonetically normalizes DJ commentary before sending to Kokoro / espeak-ng so it
    NEVER spells out ALL-CAPS song titles, artist names, or symbols letter-by-letter, and NEVER
    says 'chinese letter' or 'japanese letter'.
    """
    text = raw_text or ""
    # 0. Strip parenthetical CV/feat/metadata tags first (often contain Japanese VA names like (CV: 日高零奈))
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(
        r"\((?:CV|Prod|feat|ft|Official|Anime|Game|MV|Video|Audio|Ver|Size).*?\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"（.*?）", "", text)

    # 1. Convert any Japanese/Chinese Kanji/Kana into clean spoken Romaji
    text = _romanize_cjk(text)

    # 2. Replace em-dashes, slashes, and asterisks with natural spoken pauses
    text = text.replace("—", ", ").replace("–", ", ").replace(" // ", ", ")
    text = text.replace("&", " and ").replace("*", " ").replace("=", " ")

    # 3. Apply explicit phonetic overrides for stylized artists & terms
    for pattern, replacement in PHONETIC_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE if "YOASOBI" in pattern else 0)

    # 4. Convert any remaining ALL-CAPS words (2+ letters, e.g. DEAD WEIGHT, LINKIN PARK, EVE, ADO)
    # into Title Case so espeak-ng pronounces them as words instead of spelling each letter!
    def _decap_word(match: re.Match) -> str:
        w = match.group(0)
        if w in ("FM", "DJ", "UK", "TV"):
            return w
        return w.capitalize()

    text = re.sub(r"\b[A-Z]{2,}\b", _decap_word, text)

    # 5. Clean up extra spaces and punctuation
    text = re.sub(r"\s+", " ", text).strip()
    return text

VOICE_PROFILES: Dict[str, Dict[str, Any]] = {
    c["id"]: {
        **c["voice_config"],
        "description": c["tagline"],
    }
    for c in BUILTIN_CHARACTER_CATALOG
}


class HertaVoiceSynthesizer:
    def __init__(self):
        self.kokoro = None
        self.available_voices: List[str] = []
        self.active_profile_id: str = "the_herta"
        self.custom_config: Dict[str, Any] = dict(VOICE_PROFILES["the_herta"])

    def _ensure_kokoro_loaded(self) -> None:
        if self.kokoro is None and KOKORO_MODEL_PATH.exists():
            import onnxruntime as ort

            os.environ.setdefault("ONNX_PROVIDER", "CUDAExecutionProvider")
            if hasattr(ort, "preload_dlls"):
                try:
                    ort.preload_dlls()
                except Exception:
                    pass
            from kokoro_onnx import Kokoro

            self.kokoro = Kokoro(str(KOKORO_MODEL_PATH), str(KOKORO_VOICES_PATH))
            try:
                voices_npz = np.load(str(KOKORO_VOICES_PATH))
                self.available_voices = sorted(list(voices_npz.files))
            except Exception:
                self.available_voices = ["af_heart", "af_bella", "af_nicole", "af_sky", "bf_emma"]

    def get_voice_state(self) -> Dict[str, Any]:
        self._ensure_kokoro_loaded()
        return {
            "active_profile_id": self.active_profile_id,
            "current": self.custom_config,
            "presets": list(VOICE_PROFILES.values()),
            "available_kokoro_voices": self.available_voices,
        }

    def update_voice_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        profile_id = payload.get("profile_id")
        if profile_id and profile_id in VOICE_PROFILES:
            self.active_profile_id = profile_id
            self.custom_config = dict(VOICE_PROFILES[profile_id])

        # Allow overriding individual blend/speed/pitch parameters on top of any preset
        for key in ("voice_a", "voice_b", "mix_a", "speed", "pitch_semitones", "lang", "engine", "name"):
            if key in payload and payload[key] is not None:
                self.custom_config[key] = payload[key]
                if not profile_id:
                    self.active_profile_id = "custom"

        return self.get_voice_state()

    async def synthesize_herta_line(
        self, text: str, emotion_tag: str = "aloof"
    ) -> np.ndarray:
        """
        Synthesizes the DJ script off the main event loop and returns a 48,000 Hz
        stereo float32 array ready for Deck C.
        Supports GPT-SoVITS v2 Zero-Shot Cloning, RVC v2, and Kokoro fallback.
        """
        if not text.strip():
            return np.zeros((0, CHANNELS), dtype=np.float32)

        # 1. Zero-Shot Authentic Character Cloning via GPT-SoVITS v2 (only if explicitly enabled)
        if self.custom_config.get("engine") == "gpt_sovits":
            audio_48k = await self._synthesize_via_gpt_sovits(text, emotion_tag)
            if audio_48k is not None:
                return audio_48k

        # 2. Base phonetic synthesis via Kokoro
        audio_48k = await asyncio.to_thread(self._synthesize_via_kokoro_sync, text, emotion_tag)

        # 3. RVC v2 Neural Voice Conversion (if model weights are installed)
        char_id = self.active_profile_id
        try:
            from ai_booth.rvc_bridge import rvc_bridge
            if rvc_bridge.is_model_available(char_id):
                converted = await rvc_bridge.convert_voice(audio_48k, char_id)
                if converted is not None:
                    return converted
        except Exception:
            pass

        return audio_48k

    def _get_persona_board(self, char_id: str):
        from pedalboard import Compressor, Gain, HighpassFilter, HighShelfFilter, Limiter, PeakFilter, Pedalboard

        plugins = [
            HighpassFilter(cutoff_frequency_hz=85.0),
        ]
        if char_id == "astra_yao":
            # Diva vocal sheen & presence for New Eridu superstar
            plugins.extend([
                PeakFilter(cutoff_frequency_hz=3400.0, gain_db=2.4, q=1.0),
                HighShelfFilter(cutoff_frequency_hz=8000.0, gain_db=1.8),
            ])
        elif char_id == "ellen_joe":
            # Sleepy, relaxed tone with slight low-mid warmth and soft highs
            plugins.extend([
                PeakFilter(cutoff_frequency_hz=280.0, gain_db=1.8, q=0.9),
                HighShelfFilter(cutoff_frequency_hz=6000.0, gain_db=-1.5),
            ])
        elif char_id == "nicole_demara":
            # Punchy vocal presence
            plugins.extend([
                PeakFilter(cutoff_frequency_hz=2100.0, gain_db=2.0, q=1.1),
            ])
        elif char_id == "sparkle":
            # Bright, playful theatrical overtone
            plugins.extend([
                PeakFilter(cutoff_frequency_hz=3800.0, gain_db=2.0, q=1.2),
            ])

        plugins.extend([
            Compressor(threshold_db=-18.0, ratio=3.5, attack_ms=4.0, release_ms=90.0),
            Gain(gain_db=4.0),
            Limiter(threshold_db=-0.8),
        ])
        return Pedalboard(plugins)

    async def synthesize_preview_wav_bytes(self, text: str) -> bytes:
        stereo_48k = await self.synthesize_herta_line(text, emotion_tag="smug")
        buf = io.BytesIO()
        sf.write(buf, stereo_48k, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    def _synthesize_via_kokoro_sync(self, text: str, emotion_tag: str) -> np.ndarray:
        self._ensure_kokoro_loaded()
        if self.kokoro is None:
            return np.zeros((int(SAMPLE_RATE * 0.5), CHANNELS), dtype=np.float32)

        voice_a = str(self.custom_config.get("voice_a", "af_heart"))
        voice_b = str(self.custom_config.get("voice_b", "af_bella"))
        mix_a = float(np.clip(float(self.custom_config.get("mix_a", 0.70)), 0.0, 1.0))
        base_speed = float(self.custom_config.get("speed", 0.96))
        pitch_semi = float(np.clip(float(self.custom_config.get("pitch_semitones", 0.0)), -4.0, 4.0))
        lang = str(self.custom_config.get("lang", "en-us"))

        if emotion_tag == "annoyed":
            speed = min(1.25, base_speed * 1.05)
        elif emotion_tag == "aloof":
            speed = max(0.80, base_speed * 0.98)
        else:
            speed = base_speed

        # Compensate Kokoro duration for soxr pitch shift so cadence stays exact with zero FFT phase-vocoder artifacts
        pitch_ratio = float(2.0 ** (pitch_semi / 12.0))
        kokoro_speed = float(np.clip(speed / pitch_ratio, 0.72, 1.35))

        try:
            style_a = self.kokoro.get_voice_style(voice_a)
            if voice_b and voice_b != voice_a and mix_a < 0.999:
                style_b = self.kokoro.get_voice_style(voice_b)
                voice_style = (mix_a * style_a) + ((1.0 - mix_a) * style_b)
            else:
                voice_style = style_a
        except Exception:
            voice_style = voice_a

        spoken_text = normalize_text_for_tts(text)
        if not spoken_text:
            return np.zeros((int(SAMPLE_RATE * 0.2), CHANNELS), dtype=np.float32)

        samples, sr = self.kokoro.create(
            spoken_text,
            voice=voice_style,
            speed=kokoro_speed,
            lang=lang,
        )

        samples_f32 = np.asarray(samples, dtype=np.float32)
        effective_sr = int(round(sr * pitch_ratio))
        if effective_sr != SAMPLE_RATE:
            samples_f32 = soxr.resample(samples_f32, effective_sr, SAMPLE_RATE, quality="HQ")

        # Enforce natural speaking duration ceiling (~14.5 seconds max) so the DJ never talks over the whole song!
        max_samples = int(14.5 * SAMPLE_RATE)
        if len(samples_f32) > max_samples:
            # Find the quietest breath/pause window between 10.5s and 14.5s for a natural cut
            search_start = int(10.5 * SAMPLE_RATE)
            window = np.abs(samples_f32[search_start:max_samples])
            if len(window) > SAMPLE_RATE:
                # 50ms moving average energy
                win_len = int(0.05 * SAMPLE_RATE)
                kernel = np.ones(win_len, dtype=np.float32) / win_len
                env = np.convolve(window, kernel, mode="valid")
                cut_idx = search_start + int(np.argmin(env))
                samples_f32 = samples_f32[:cut_idx]
            else:
                samples_f32 = samples_f32[:max_samples]

        # Gentle 25ms fade-in/out on voice clip so it never clicks entering or leaving Deck C
        fade_len = min(int(0.025 * SAMPLE_RATE), len(samples_f32) // 4)
        if fade_len > 0:
            samples_f32[:fade_len] *= np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
            samples_f32[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)

        stereo = np.column_stack([samples_f32, samples_f32]).astype(np.float32)
        # Apply character-tailored acoustic board & limiting off the main audio mixer thread!
        board = self._get_persona_board(self.active_profile_id)
        stereo = board(stereo, SAMPLE_RATE).astype(np.float32)
        return stereo

    async def _synthesize_via_gpt_sovits(
        self, text: str, emotion_tag: str
    ) -> Optional[np.ndarray]:
        char_id = self.active_profile_id
        ref_dir = BASE_DIR / "models" / "voice_refs" / char_id
        ref_wav_path = ref_dir / "ref_audio.wav"
        prompt_txt_path = ref_dir / "prompt_text.txt"

        if ref_wav_path.exists():
            ref_wav = str(ref_wav_path)
            prompt_text = prompt_txt_path.read_text(encoding="utf-8").strip() if prompt_txt_path.exists() else ""
        else:
            ref_wav = HERTA_REF_AUDIO_MAP.get(emotion_tag, HERTA_REF_AUDIO_MAP.get("aloof", ""))
            prompt_text = "I paused my research for a moment just to queue this up."

        if not ref_wav or not os.path.exists(ref_wav):
            return None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                payload = {
                    "text": text,
                    "text_lang": "en",
                    "ref_audio_path": ref_wav,
                    "prompt_text": prompt_text,
                    "prompt_lang": "en",
                    "speed_factor": 1.0,
                }
                resp = await client.post(f"{GPT_SOVITS_API_URL}/tts", json=payload)
                if resp.status_code != 200:
                    # Also try GET /tts query parameters supported by GPT-SoVITS-fast-inference
                    resp = await client.get(
                        f"{GPT_SOVITS_API_URL}/tts",
                        params={
                            "text": text,
                            "text_language": "en",
                            "ref_audio_path": ref_wav,
                            "prompt_text": prompt_text,
                            "prompt_language": "en",
                        },
                    )
                if resp.status_code != 200:
                    return None
                wav_data, sr = sf.read(io.BytesIO(resp.content), dtype="float32")
                if wav_data.ndim == 1:
                    wav_data = np.column_stack([wav_data, wav_data])
                if sr != SAMPLE_RATE:
                    wav_data = soxr.resample(wav_data, sr, SAMPLE_RATE, quality="HQ")
                return wav_data.astype(np.float32)
        except Exception:
            return None
