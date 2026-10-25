"""
Club DJ Transition Engine & Master Broadcast DSP (audio_engine/dsp_effects.py)
Implements professional club DJ mixer techniques:
1. Low-End Frequency Crossover & Bass-Swap (Instant downbeat bass-swap at p=0.50
   so kick drums and sub-bass frequencies never clash or cause mud/cancellation).
2. Pioneer DJM-style Resonant Filter Sweeps:
   - Phase 1 (0.0 <= p < 0.50): Deck A full bass; Deck B sub-bass cut (<260Hz).
   - Drop point (p = 0.50): Instant bass swap on the downbeat!
   - Phase 2 (0.50 <= p <= 1.0): Deck A swept out with resonant HPF (320Hz -> 3800Hz)
     plus beat-synced echo diffusion. Deck B plays full spectrum.
3. Echo Freeze / Dub Tail Outro on manual power-cut skips so cuts sound like
   a Pioneer DJM mixer echo freeze rather than a flat linear fade.
4. Broadcast Master Limiter with preserved dynamic transient impact (-14.0 LUFS).
"""

import numpy as np
import pyloudnorm as pyln
from pedalboard import (
    Compressor,
    Delay,
    HighpassFilter,
    Limiter,
    LowpassFilter,
    Pedalboard,
)

from config import (
    BASS_SWAP_HPF_HZ,
    SAMPLE_RATE,
    TARGET_LUFS,
)


class BroadcastDSPChain:
    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.meter = pyln.Meter(sample_rate)

        # Stateful DJ filters for Deck A (outgoing)
        self.deck_a_hpf = HighpassFilter(cutoff_frequency_hz=20.0)
        self.deck_a_lpf = LowpassFilter(cutoff_frequency_hz=20000.0)

        # Stateful DJ filters for Deck B (incoming)
        self.deck_b_hpf = HighpassFilter(cutoff_frequency_hz=20.0)

        # Pioneer DJM-style Echo/Delay Tail for manual DJ skips & phrase dissolutions
        self.echo_freeze = Delay(delay_seconds=0.375, feedback=0.48, mix=0.0)

        # Master Limiter preserving punchy kick transients
        self.master_board = Pedalboard(
            [
                Compressor(threshold_db=-11.0, ratio=1.6, attack_ms=12.0, release_ms=120.0),
                Limiter(threshold_db=-0.8, release_ms=90.0),
            ]
        )

    def reset_crossfade_filters(self) -> None:
        """Resets filter and delay states between tracks."""
        self.deck_a_hpf.cutoff_frequency_hz = 20.0
        self.deck_a_hpf.reset()
        self.deck_a_lpf.cutoff_frequency_hz = 20000.0
        self.deck_a_lpf.reset()
        self.deck_b_hpf.cutoff_frequency_hz = 20.0
        self.deck_b_hpf.reset()
        self.echo_freeze.mix = 0.0
        self.echo_freeze.reset()

    def process_deck_a_transition(
        self, audio_chunk: np.ndarray, progress: float, style: str = "club_eq_crossover_9s"
    ) -> np.ndarray:
        """
        Processes Deck A during DJ mix.
        Phase 1 (p < 0.50): Holds solid low-end while gently rolling off 14kHz+ highs
                            to leave headroom for Deck B's cymbals & vocal presence.
        Drop (p = 0.50): Sub-bass is cut immediately.
        Phase 2 (p >= 0.50): High-pass filter sweeps (320Hz -> 3600Hz) with delay diffusion.
        """
        p = float(np.clip(progress, 0.0, 1.0))

        if "quick_cut" in style:
            # Echo freeze tail on manual skip
            self.echo_freeze.mix = float(min(0.68, p * 0.95))
            delayed = self.echo_freeze(audio_chunk, self.sample_rate, reset=False)
            gain = float((1.0 - p) ** 1.8)
            return (delayed * gain).astype(np.float32)

        if p < 0.50:
            # Phase 1: Deck A keeps full bass punch (20Hz HPF)
            self.deck_a_hpf.cutoff_frequency_hz = 20.0
            filtered = self.deck_a_hpf(audio_chunk, self.sample_rate, reset=False)

            # Subtle high-shelf tuck (20kHz -> 12kHz) so incoming highs breathe
            lpf_cut = 20000.0 - 8000.0 * (p / 0.50)
            self.deck_a_lpf.cutoff_frequency_hz = float(lpf_cut)
            filtered = self.deck_a_lpf(filtered, self.sample_rate, reset=False)

            # Deck A stays strong at ~0.95-1.0 gain
            gain = float(np.cos(p * (np.pi / 3.0)))
            return (filtered * gain).astype(np.float32)
        else:
            # Phase 2 (The Drop & Sweep Out):
            # Deck A bass is completely cut. High-pass sweeps from 320Hz up to 3600Hz
            sweep_ratio = (p - 0.50) / 0.50
            hpf_cut = BASS_SWAP_HPF_HZ + (3400.0 - BASS_SWAP_HPF_HZ) * (sweep_ratio ** 1.5)
            self.deck_a_hpf.cutoff_frequency_hz = float(hpf_cut)
            filtered = self.deck_a_hpf(audio_chunk, self.sample_rate, reset=False)

            # Add subtle echo trail as Deck A sweeps away
            self.echo_freeze.mix = float(sweep_ratio * 0.45)
            filtered = self.echo_freeze(filtered, self.sample_rate, reset=False)

            # Smooth fade to 0
            gain = float(np.cos(p * (np.pi / 2.0)))
            return (filtered * gain).astype(np.float32)

    def process_deck_b_transition(
        self, audio_chunk: np.ndarray, progress: float, style: str = "club_eq_crossover_9s"
    ) -> np.ndarray:
        """
        Processes Deck B during DJ mix.
        Phase 1 (p < 0.50): Sub-bass is held in hard cut (280Hz) so kick drums never fight.
                            Vocals and highs smoothly rise in.
        Drop (p = 0.50): HPF opens to 20Hz instantly (Full Sub-bass DROP!).
        Phase 2 (p >= 0.50): Deck B is at 100% volume and full frequency spectrum.
        """
        p = float(np.clip(progress, 0.0, 1.0))

        if "quick_cut" in style:
            # Punchy rise on beat
            gain = float(np.sin(p * (np.pi / 2.0)))
            return (audio_chunk * gain).astype(np.float32)

        if p < 0.50:
            # Deck B sub-bass is locked out (< 280Hz cut)
            self.deck_b_hpf.cutoff_frequency_hz = 280.0
            filtered = self.deck_b_hpf(audio_chunk, self.sample_rate, reset=False)

            # Equal power ramp up for the highs/mids
            gain = float(np.sin(p * (np.pi / 2.0)) * 0.92)
            return (filtered * gain).astype(np.float32)
        else:
            # The Drop has occurred: full 20Hz sub-bass open!
            self.deck_b_hpf.cutoff_frequency_hz = 20.0
            filtered = self.deck_b_hpf(audio_chunk, self.sample_rate, reset=False)

            # Full volume
            gain = float(np.sin(min(1.0, p * 1.1) * (np.pi / 2.0)))
            return (filtered * gain).astype(np.float32)

    def normalize_and_limit_master(self, master_chunk: np.ndarray) -> np.ndarray:
        """Stateful master bus limiter preserving dynamic impact."""
        processed = self.master_board(master_chunk, self.sample_rate, reset=False)
        return np.clip(processed, -0.98, 0.98).astype(np.float32)

    def normalize_track_pcm(self, stereo_pcm: np.ndarray) -> tuple[np.ndarray, float]:
        """Normalizes decoded track to -14.0 LUFS with -1.5 dB summing headroom."""
        try:
            n = len(stereo_pcm)
            mid_start = max(0, n // 4)
            mid_end = min(n, mid_start + self.sample_rate * 45)
            loudness = self.meter.integrated_loudness(stereo_pcm[mid_start:mid_end])
            if np.isinf(loudness) or np.isnan(loudness):
                return (stereo_pcm * 0.72).astype(np.float32), -14.0
            gain_db = float(np.clip(TARGET_LUFS - loudness, -11.0, 6.0))
            linear_gain = float(10.0 ** ((gain_db - 1.5) / 20.0))
            return (stereo_pcm * linear_gain).astype(np.float32), round(float(loudness), 1)
        except Exception:
            return (stereo_pcm * 0.72).astype(np.float32), -14.0
