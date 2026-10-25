"""
AetherFM Neural Voice Conversion & RVC v2 Bridge (rvc_bridge.py)
----------------------------------------------------------------
Provides:
1. Auto-discovery of RVC v2 model weights (.pth) and feature indexes (.index)
   in `backend/models/rvc_weights/` and `backend/models/herta_voice/`.
2. Seamless integration with local RVC v2 / Applio inference servers
   (default: http://127.0.0.1:65432 or http://127.0.0.1:7865) for zero-latency,
   1:1 authentic character voice cloning.
3. Graceful fallback to fine-tuned Kokoro phonetics + studio vocal mastering
   so the station runs flawlessly even before weights are placed.
"""

import asyncio
import io
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
import numpy as np
import soundfile as sf
import soxr

from config import BASE_DIR, SAMPLE_RATE

logger = logging.getLogger("aether.rvc_bridge")

RVC_WEIGHTS_DIR = BASE_DIR / "models" / "rvc_weights"
RVC_LOCAL_API_URL = os.getenv("RVC_API_URL", "http://127.0.0.1:65432")
APPLIO_API_URL = os.getenv("APPLIO_API_URL", "http://127.0.0.1:7865")


class RVCBridge:
    def __init__(self):
        RVC_WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_readme()

    def _ensure_readme(self) -> None:
        readme_path = RVC_WEIGHTS_DIR / "README.md"
        if not readme_path.exists():
            readme_path.write_text(
                "# AetherFM Character Voice Weights (RVC v2)\n\n"
                "Drop your trained RVC v2 model weights (`.pth`) and feature retrieval index (`.index`) here.\n\n"
                "### File Naming Convention:\n"
                "- Astra Yao (Zenless Zone Zero): `astra_yao.pth` (and optional `astra_yao.index`)\n"
                "- Ellen Joe (Zenless Zone Zero): `ellen_joe.pth` (and optional `ellen_joe.index`)\n"
                "- Nicole Demara (Zenless Zone Zero): `nicole_demara.pth` (and optional `nicole_demara.index`)\n"
                "- The Herta (Honkai: Star Rail): `the_herta.pth` (and optional `the_herta.index`)\n"
                "- Sparkle (Honkai: Star Rail): `sparkle.pth` (and optional `sparkle.index`)\n"
                "- Firefly (Honkai: Star Rail): `firefly.pth` (and optional `firefly.index`)\n"
                "- Furina (Genshin Impact): `furina.pth` (and optional `furina.index`)\n"
                "- Raiden Shogun (Genshin Impact): `raiden_shogun.pth` (and optional `raiden_shogun.index`)\n\n"
                "### How Character AI / Voice Cloning Works:\n"
                "1. Game audio rips provide 5-10 minutes of clean, isolated dialogue.\n"
                "2. RVC v2 trains a pitch-matching timbre generator from that dialogue.\n"
                "3. AetherFM generates natural speech with Kokoro, then applies RVC conversion.\n",
                encoding="utf-8",
            )

    def scan_models(self) -> Dict[str, Dict[str, Any]]:
        """
        Scans both rvc_weights and herta_voice directories for available .pth models.
        """
        discovered: Dict[str, Dict[str, Any]] = {}
        search_dirs = [RVC_WEIGHTS_DIR, BASE_DIR / "models" / "herta_voice"]

        for d in search_dirs:
            if not d.exists():
                continue
            for pth_file in d.glob("*.pth"):
                stem = pth_file.stem.lower()
                # Check for matching .index file
                index_candidates = list(d.glob(f"{stem}*.index")) + list(d.glob("*.index"))
                matching_index = index_candidates[0] if index_candidates else None

                discovered[stem] = {
                    "character_id": stem,
                    "model_path": str(pth_file),
                    "index_path": str(matching_index) if matching_index else None,
                    "size_mb": round(pth_file.stat().st_size / (1024 * 1024), 2),
                    "has_index": matching_index is not None,
                }
        return discovered

    def is_model_available(self, character_id: str) -> bool:
        discovered = self.scan_models()
        return character_id in discovered or f"the_{character_id}" in discovered

    async def check_rvc_api_alive(self) -> bool:
        """Checks if a local RVC or Applio server is responding, or if internal engine is available."""
        rmvpe_path = BASE_DIR / "models" / "rvc_weights" / "base" / "rmvpe.pt"
        if rmvpe_path.exists():
            return True
        for url in [RVC_LOCAL_API_URL, APPLIO_API_URL]:
            try:
                async with httpx.AsyncClient(timeout=1.0) as client:
                    resp = await client.get(url)
                    if resp.status_code < 500:
                        return True
            except Exception:
                pass
        return False

    async def _convert_via_subprocess(
        self,
        audio_f32: np.ndarray,
        model_info: Dict[str, Any],
        pitch_shift: int = 0,
    ) -> Optional[np.ndarray]:
        """
        Executes RVC conversion in a standalone worker process so CUDA/cuDNN DLLs
        never conflict with onnxruntime-gpu in the main broadcast server.
        """
        tag = model_info["character_id"]
        model_path = model_info["model_path"]
        index_path = model_info.get("index_path") or ""

        # Mono 16kHz for clean feature extraction
        mono = audio_f32[:, 0] if audio_f32.ndim > 1 else audio_f32
        mono_16k = soxr.resample(mono, SAMPLE_RATE, 16000, quality="HQ")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f_in:
            in_path = f_in.name
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f_out:
            out_path = f_out.name

        sf.write(in_path, mono_16k, 16000)

        cmd = [
            sys.executable,
            str(BASE_DIR / "ai_booth" / "rvc_worker.py"),
            "--input", in_path,
            "--output", out_path,
            "--model", model_path,
            "--index", index_path,
            "--tag", tag,
            "--pitch", str(pitch_shift),
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=35.0)

            if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                out_wav, sr = sf.read(out_path, dtype="float32")
                if out_wav.ndim == 1:
                    out_wav = np.column_stack([out_wav, out_wav])
                if sr != SAMPLE_RATE:
                    out_wav = soxr.resample(out_wav, sr, SAMPLE_RATE, quality="HQ")
                return out_wav.astype(np.float32)
            else:
                logger.warning(f"RVC worker exited with code {proc.returncode}: {stderr.decode(errors='ignore')}")
                return None
        except Exception as e:
            logger.error(f"[RVC Subprocess Error]: {e}")
            return None
        finally:
            for p in [in_path, out_path]:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    async def convert_voice(
        self,
        audio_f32: np.ndarray,
        character_id: str,
        pitch_shift: int = 0,
        f0_method: str = "rmvpe",
    ) -> Optional[np.ndarray]:
        """
        Converts input audio using embedded RVC v2 engine (or falls back to external Applio API).
        Returns converted float32 array or None if conversion fails.
        """
        discovered = self.scan_models()
        model_info = discovered.get(character_id) or discovered.get(f"the_{character_id}")
        if not model_info:
            return None

        # 1. Isolated Out-of-Process RVC v2 Worker (100% DLL collision free!)
        try:
            converted = await self._convert_via_subprocess(audio_f32, model_info, pitch_shift)
            if converted is not None:
                return converted
        except Exception as e:
            logger.error(f"Embedded RVC conversion failed: {e}")

        # 2. Fall back to external Applio / RVC HTTP API if running
        mono = audio_f32[:, 0] if audio_f32.ndim > 1 else audio_f32
        wav_buf = io.BytesIO()
        sf.write(wav_buf, mono, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        wav_bytes = wav_buf.getvalue()

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                files = {"audio_file": ("input.wav", wav_bytes, "audio/wav")}
                data = {
                    "model_name": model_info["character_id"],
                    "pitch": pitch_shift,
                    "f0_method": f0_method,
                    "index_path": model_info.get("index_path") or "",
                }
                resp = await client.post(f"{RVC_LOCAL_API_URL}/voice2voice", files=files, data=data)
                if resp.status_code == 200:
                    out_wav, sr = sf.read(io.BytesIO(resp.content), dtype="float32")
                    if out_wav.ndim == 1:
                        out_wav = np.column_stack([out_wav, out_wav])
                    if sr != SAMPLE_RATE:
                        out_wav = soxr.resample(out_wav, sr, SAMPLE_RATE, quality="HQ")
                    return out_wav.astype(np.float32)
        except Exception:
            pass

        return None




rvc_bridge = RVCBridge()
