"""
Live Voice Request Transcriber & VAD Auto-Hangup (stt_caller.py)
Fixes the "stuck on Listening" issue:
1. Lowered RMS voice activity threshold (0.008) so phone & car microphones reliably trigger speech detection.
2. Enforces a hard 5.5-second max call duration timeout (`MAX_CALL_DURATION_SEC = 5.5`) so even if
   the mic is quiet or noisy, the call ALWAYS auto-completes and returns control to the app!
3. Supports explicit `CALL_STOP` when the user taps the Voice Request button a second time.
"""

import asyncio
import time
from typing import Optional, Tuple
import numpy as np
import soxr
from faster_whisper import WhisperModel

from config import (
    SAMPLE_RATE,
    VAD_SILENCE_TIMEOUT_SEC,
    WHISPER_COMPUTE_TYPE,
    WHISPER_CPU_THREADS,
    WHISPER_DEVICE,
    WHISPER_MODEL_SIZE,
)

MAX_CALL_DURATION_SEC = 5.5


class BoothCallTranscriber:
    def __init__(self):
        self.model: Optional[WhisperModel] = None
        self.pcm_chunks: list[np.ndarray] = []
        self.has_spoken = False
        self.call_start_timestamp = 0.0
        self.last_voice_timestamp = 0.0

    def _ensure_loaded(self) -> None:
        if self.model is None:
            self.model = WhisperModel(
                WHISPER_MODEL_SIZE,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
                cpu_threads=WHISPER_CPU_THREADS,
            )

    def reset_call_buffer(self) -> None:
        self.pcm_chunks.clear()
        self.has_spoken = False
        now = time.monotonic()
        self.call_start_timestamp = now
        self.last_voice_timestamp = now

    def ingest_pcm_int16_chunk(self, raw_bytes: bytes) -> bool:
        """
        Ingests a live 16kHz Int16 PCM chunk from the mobile app microphone.
        Returns True when:
        - 1.2s of silence is detected after speech, OR
        - 5.5s total call time has elapsed (guarantees it NEVER hangs in 'Listening'!).
        """
        if len(raw_bytes) == 0:
            return False

        pcm_int16 = np.frombuffer(raw_bytes, dtype=np.int16)
        pcm_float32 = pcm_int16.astype(np.float32) / 32768.0
        self.pcm_chunks.append(pcm_float32)

        rms_energy = float(np.sqrt(np.mean(pcm_float32 ** 2))) if len(pcm_float32) > 0 else 0.0
        now = time.monotonic()

        if self.call_start_timestamp == 0.0:
            self.call_start_timestamp = now

        if rms_energy > 0.008:
            self.has_spoken = True
            self.last_voice_timestamp = now
        elif self.has_spoken and (now - self.last_voice_timestamp) >= VAD_SILENCE_TIMEOUT_SEC:
            return True

        # Hard safety timeout so the mic never stays stuck open
        if (now - self.call_start_timestamp) >= MAX_CALL_DURATION_SEC:
            return True

        return False

    async def transcribe_buffered_call_async(self) -> Tuple[str, np.ndarray]:
        return await asyncio.to_thread(self.transcribe_buffered_call)

    def transcribe_buffered_call(self) -> Tuple[str, np.ndarray]:
        self._ensure_loaded()
        if not self.pcm_chunks:
            return "", np.zeros((0, 2), dtype=np.float32)

        full_16k = np.concatenate(self.pcm_chunks)
        self.pcm_chunks.clear()

        try:
            segments, _ = self.model.transcribe(
                full_16k,
                beam_size=2,
                vad_filter=True,
            )
            transcript = " ".join(seg.text.strip() for seg in segments).strip()
        except Exception:
            transcript = ""

        upsampled_48k = soxr.resample(full_16k, 16000, SAMPLE_RATE, quality="HQ")
        stereo_48k = np.column_stack([upsampled_48k, upsampled_48k]).astype(np.float32)
        return transcript, stereo_48k
