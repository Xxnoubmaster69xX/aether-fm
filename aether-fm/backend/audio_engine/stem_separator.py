r"""
GPU/CPU Stem Separation Engine (audio_engine/stem_separator.py)
Powered by Meta Demucs (htdemucs) & PyTorch:
- Separates audio into 4 stems: Vocals, Drums, Bass, Other
- Caches stems in Audio Vault (E:\FM_Audio_Vault\stems\<track_id>\)
- Optimized for NVIDIA RTX 5060 (8GB VRAM) with automatic CPU fallback
- Enables zero-vocal-collision mashup transitions and dynamic bass swaps
"""

from pathlib import Path
from typing import Any, Dict, Optional
import logging
import numpy as np
import soundfile as sf

from config import AUDIO_CACHE_DIR, SAMPLE_RATE
from ingestion.ytdlp_client import decode_audio_fast_48k

logger = logging.getLogger("aether_fm.stems")

STEMS_CACHE_DIR = AUDIO_CACHE_DIR / "stems"
STEMS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_DEMUCS_MODEL = None
_MODEL_LOAD_ATTEMPTED = False


def _get_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            # Test simple tensor to ensure sm_120 kernel execution
            try:
                t = torch.randn(2, 2, device="cuda")
                _ = t @ t
                return "cuda"
            except Exception:
                return "cpu"
    except Exception:
        pass
    return "cpu"


def _load_demucs_model():
    global _DEMUCS_MODEL, _MODEL_LOAD_ATTEMPTED
    if _DEMUCS_MODEL is not None or _MODEL_LOAD_ATTEMPTED:
        return _DEMUCS_MODEL

    _MODEL_LOAD_ATTEMPTED = True
    try:
        import torch
        from demucs.pretrained import get_model
        device = _get_device()
        logger.info(f"Loading Demucs htdemucs on {device.upper()}...")
        model = get_model("htdemucs")
        model.to(device)
        model.eval()
        _DEMUCS_MODEL = model
        logger.info(f"Demucs htdemucs loaded successfully on {device.upper()}")
        return _DEMUCS_MODEL
    except Exception as e:
        logger.warning(f"Demucs model unavailable ({e}). Stem mixing will fall back to 3-band isolators.")
        _DEMUCS_MODEL = None
        return None


def has_stems(track_id: str) -> bool:
    """Checks if pre-separated stems exist in the vault for a given track."""
    target_dir = STEMS_CACHE_DIR / track_id
    return (
        target_dir.exists()
        and (target_dir / "vocals.npy").exists()
        and (target_dir / "instrumental.npy").exists()
    )


def load_stems(track_id: str) -> Optional[Dict[str, np.ndarray]]:
    """Loads cached float32 stereo stem arrays from vault."""
    target_dir = STEMS_CACHE_DIR / track_id
    if not has_stems(track_id):
        return None
    try:
        vocals = np.load(target_dir / "vocals.npy")
        instrumental = np.load(target_dir / "instrumental.npy")
        bass = np.load(target_dir / "bass.npy") if (target_dir / "bass.npy").exists() else None
        return {
            "vocals": vocals,
            "instrumental": instrumental,
            "bass": bass,
        }
    except Exception as e:
        logger.warning(f"Failed to load stems for {track_id}: {e}")
        return None


def separate_and_cache_stems(audio_path: Path, track_id: str) -> Optional[Dict[str, Path]]:
    """
    Separates a track into Vocals, Instrumental, and Bass stems using Demucs.
    Caches results in E:\\FM_Audio_Vault\\stems\\<track_id>\\ as fast .npy arrays.
    Returns dictionary of stem file paths.
    """
    target_dir = STEMS_CACHE_DIR / track_id
    if has_stems(track_id):
        return {
            "vocals": target_dir / "vocals.npy",
            "instrumental": target_dir / "instrumental.npy",
            "bass": target_dir / "bass.npy",
        }

    model = _load_demucs_model()
    if model is None:
        return None

    try:
        import torch
        import torchaudio
        from demucs.apply import apply_model

        device = _get_device()
        pcm_48k = decode_audio_fast_48k(audio_path)
        if len(pcm_48k) == 0:
            logger.warning(f"Failed to decode audio for stem separation: {audio_path}")
            return None

        # pcm_48k is (samples, channels) -> convert to (channels, samples)
        wav = torch.from_numpy(pcm_48k.T).float()

        # Resample to model native sample rate if needed
        model_sr = getattr(model, "samplerate", 44100)
        if SAMPLE_RATE != model_sr:
            wav_model = torchaudio.functional.resample(wav, SAMPLE_RATE, model_sr)
        else:
            wav_model = wav

        # Convert mono to stereo if needed
        if wav_model.shape[0] == 1:
            wav_model = wav_model.repeat(2, 1)
        elif wav_model.shape[0] > 2:
            wav_model = wav_model[:2]

        wav_model = wav_model.unsqueeze(0).to(device)

        with torch.no_grad():
            # Sources order for htdemucs: [drums, bass, other, vocals]
            sources = apply_model(model, wav_model, split=True, overlap=0.25)[0].cpu()

        # Resample stems back to broadcast sample rate (48,000 Hz)
        if model_sr != SAMPLE_RATE:
            sources = torchaudio.functional.resample(sources, model_sr, SAMPLE_RATE)

        drums = sources[0].numpy().T.astype(np.float32)
        bass = sources[1].numpy().T.astype(np.float32)
        other = sources[2].numpy().T.astype(np.float32)
        vocals = sources[3].numpy().T.astype(np.float32)

        # Instrumental = Drums + Bass + Other
        instrumental = (drums + bass + other).astype(np.float32)

        target_dir.mkdir(parents=True, exist_ok=True)
        np.save(target_dir / "vocals.npy", vocals)
        np.save(target_dir / "instrumental.npy", instrumental)
        np.save(target_dir / "bass.npy", bass)

        logger.info(f"Stems successfully separated and cached for {track_id}")
        return {
            "vocals": target_dir / "vocals.npy",
            "instrumental": target_dir / "instrumental.npy",
            "bass": target_dir / "bass.npy",
        }
    except Exception as e:
        logger.warning(f"Stem separation failed for {track_id}: {e}")
        return None
