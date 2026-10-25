"""
AetherFM Configuration — Tuned for Windows 10 Home PC:
- GPU: NVIDIA RTX 5060 (8GB VRAM)
- CPU: AMD Ryzen 7 5700G (8 Cores / 16 Threads)
- RAM: 16GB DDR4 (Windowed audio buffers to keep RAM < 2GB)
- ISP: Starlink (CGNAT via Tailscale + Clean Opus VBR streaming)
- Host Persona: The Herta (Madam Herta, Genius Society #83 — Honkai: Star Rail)
"""

import os
from pathlib import Path

# Ensure WinGet-installed FFmpeg/FFprobe, Deno, and Ollama are on PATH even in existing shells
_winget_links = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"
if _winget_links.exists():
    os.environ["PATH"] = f"{_winget_links}{os.pathsep}{os.environ.get('PATH', '')}"

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database" / "aether_radio.db"
SCHEMA_PATH = BASE_DIR / "database" / "schema.sql"
_default_vault = Path("E:/FM_Audio_Vault") if Path("E:/").exists() else (BASE_DIR / ".cache" / "audio")
AUDIO_CACHE_DIR = Path(os.getenv("FM_AUDIO_VAULT_DIR", str(_default_vault)))
VOICE_MODELS_DIR = BASE_DIR / "models" / "herta_voice"

# ---------------------------------------------------------------------------
# 1. NETWORK & SECURITY (Tailscale + Local Wi-Fi)
# ---------------------------------------------------------------------------
TAILSCALE_IP = os.getenv("AETHER_TAILSCALE_IP", "0.0.0.0")
PORT = int(os.getenv("AETHER_PORT", "8000"))
AUTH_TOKEN = os.getenv("AETHER_AUTH_TOKEN", "herta-genius-83-secret-token")

# Opus Bitrate Tiers (FEC disabled over HTTP/TCP so 100% of bits go to audio fidelity!)
OPUS_BITRATE_TIERS = {
    "wifi_hi_res": "192k",
    "mobile_64k": "64k",        # Crisp stereo VBR (~28.8 MB/hr -> ~3.4 GB/mo at 4h/day)
    "mobile_saver": "64k",      # Upgraded default mobile saver for clean highs without artifacts
    "mobile_56k": "56k",        # ~25.2 MB/hr -> ~3.0 GB/mo
    "ultra_saver": "40k",       # ~18.0 MB/hr -> ~2.1 GB/mo
}
OPUS_BITRATE_HIGH = OPUS_BITRATE_TIERS["wifi_hi_res"]
OPUS_BITRATE_LOW = OPUS_BITRATE_TIERS["mobile_saver"]
MONTHLY_CELLULAR_BUDGET_MB = 4000.0
OPUS_FRAME_DURATION_MS = 20
STARLINK_PACKET_LOSS_PCT = 0    # 0 over HTTP/TCP so no bitrate is wasted on redundant FEC frames

# ---------------------------------------------------------------------------
# 2. HARDWARE BUDGETING (RTX 5060 8GB VRAM + Ryzen 7 5700G + 16GB DDR4 RAM)
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")
OLLAMA_NUM_CTX = 2048
OLLAMA_KEEP_ALIVE_ACTIVE = "30m"
OLLAMA_KEEP_ALIVE_SLEEP = "0s"

WHISPER_MODEL_SIZE = "small"
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE_TYPE = "int8"
WHISPER_CPU_THREADS = 8
VAD_SILENCE_TIMEOUT_SEC = 1.2

ANALYZER_WINDOW_SEC = 30.0
MAX_AUDIO_CACHE_GB = 500.0

# ---------------------------------------------------------------------------
# 3. "THE HERTA" & MULTI-VOICE SYNTHESIS CONFIGURATION
# ---------------------------------------------------------------------------
TTS_BACKEND = os.getenv("AETHER_TTS_BACKEND", "kokoro_rvc")
KOKORO_MODEL_PATH = VOICE_MODELS_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = VOICE_MODELS_DIR / "voices-v1.0.bin"
KOKORO_BASE_VOICE = "af_heart"
KOKORO_SPEED = 0.96

HERTA_RVC_MODEL_PATH = VOICE_MODELS_DIR / "the_herta_hsr30.pth"
HERTA_RVC_INDEX_PATH = VOICE_MODELS_DIR / "the_herta_hsr30.index"
GPT_SOVITS_API_URL = os.getenv("GPT_SOVITS_URL", "http://127.0.0.1:9880")
HERTA_REF_AUDIO_MAP = {
    "aloof": str(VOICE_MODELS_DIR / "refs" / "herta_aloof_6s.wav"),
    "smug": str(VOICE_MODELS_DIR / "refs" / "herta_smug_6s.wav"),
    "annoyed": str(VOICE_MODELS_DIR / "refs" / "herta_annoyed_6s.wav"),
}

# ---------------------------------------------------------------------------
# 4. 3-DECK DSP MIXER & BROADCAST LOUDNESS SETTINGS
# ---------------------------------------------------------------------------
SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK_SIZE = 2048               # ~42.67ms DSP frame
TARGET_LUFS = -14.0             # Broadcast loudness normalization across Tidal & YouTube
VOICE_DUCKING_DB = -10.5        # Smooth sidechain ducking underneath DJ voice
BASS_SWAP_HPF_HZ = 320.0        # High-pass cutoff on outgoing Deck A during crossfade
CALLER_BANDPASS_LOW_HZ = 300.0
CALLER_BANDPASS_HIGH_HZ = 3400.0
