"""
Windowed Beat-Grid, Cue-Points & Camelot Harmonic Key Analyzer (beat_analyzer.py)
Optimized for 16GB DDR4 RAM + Ryzen 7 5700G (8 Cores / 16 Threads):
Instead of loading an entire uncompressed track into memory, decodes:
- First 30s: detect first clean musical onset (cue_in_sec), tempo/BPM, downbeats, RMS energy, and Camelot Key
- Last 30s: detect outro energy drop & phrase boundary (outro_start_sec)
"""

from pathlib import Path
from typing import Any, Dict
import warnings
import numpy as np
import scipy.signal

# Suppress harmless PySoundFile fallback warning on webm/opus files
warnings.filterwarnings("ignore", category=UserWarning, module="librosa")

# SciPy 1.13+ compatibility: ensure librosa can access hann window
if not hasattr(scipy.signal, "hann"):
    import scipy.signal.windows
    scipy.signal.hann = scipy.signal.windows.hann

import librosa
from config import ANALYZER_WINDOW_SEC

# Krumhansl-Schmuckler key profile correlation map to Camelot codes
CAMELOT_KEY_MAP = {
    ("C", "major"): "8B", ("C", "minor"): "5A",
    ("C#", "major"): "3B", ("C#", "minor"): "12A",
    ("Db", "major"): "3B", ("Db", "minor"): "12A",
    ("D", "major"): "10B", ("D", "minor"): "7A",
    ("D#", "major"): "5B", ("D#", "minor"): "2A",
    ("Eb", "major"): "5B", ("Eb", "minor"): "2A",
    ("E", "major"): "12B", ("E", "minor"): "9A",
    ("F", "major"): "7B", ("F", "minor"): "4A",
    ("F#", "major"): "2B", ("F#", "minor"): "11A",
    ("Gb", "major"): "2B", ("Gb", "minor"): "11A",
    ("G", "major"): "9B", ("G", "minor"): "6A",
    ("G#", "major"): "4B", ("G#", "minor"): "1A",
    ("Ab", "major"): "4B", ("Ab", "minor"): "1A",
    ("A", "major"): "11B", ("A", "minor"): "8A",
    ("A#", "major"): "6B", ("A#", "minor"): "3A",
    ("Bb", "major"): "6B", ("Bb", "minor"): "3A",
    ("B", "major"): "1B", ("B", "minor"): "10A",
}

MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def estimate_camelot_key(chroma_mean: np.ndarray) -> str:
    """Estimates musical key using chroma correlation with Krumhansl-Schmuckler profiles."""
    if len(chroma_mean) < 12 or np.all(chroma_mean == 0):
        return "8A"
    
    best_corr = -1.0
    best_key = "8A"
    norm_chroma = chroma_mean / (np.linalg.norm(chroma_mean) + 1e-6)

    for i in range(12):
        maj_roll = np.roll(MAJOR_PROFILE, i)
        maj_corr = float(np.corrcoef(norm_chroma, maj_roll / np.linalg.norm(maj_roll))[0, 1])
        if maj_corr > best_corr:
            best_corr = maj_corr
            best_key = CAMELOT_KEY_MAP.get((PITCH_NAMES[i], "major"), "8B")

        min_roll = np.roll(MINOR_PROFILE, i)
        min_corr = float(np.corrcoef(norm_chroma, min_roll / np.linalg.norm(min_roll))[0, 1])
        if min_corr > best_corr:
            best_corr = min_corr
            best_key = CAMELOT_KEY_MAP.get((PITCH_NAMES[i], "minor"), "8A")

    return best_key


def are_keys_compatible(key_a: str, key_b: str) -> bool:
    """True if keys are identical, adjacent (+-1 on Camelot wheel), or relative major/minor."""
    if not key_a or not key_b or len(key_a) < 2 or len(key_b) < 2:
        return True
    try:
        num_a, mode_a = int(key_a[:-1]), key_a[-1]
        num_b, mode_b = int(key_b[:-1]), key_b[-1]
        if key_a == key_b:
            return True
        if num_a == num_b and mode_a != mode_b:
            return True
        if mode_a == mode_b and (abs(num_a - num_b) in (1, 11)):
            return True
    except Exception:
        pass
    return False


def get_audio_duration(audio_path: Path) -> float:
    """Safely extracts audio file duration across Librosa versions."""
    try:
        return float(librosa.get_duration(filename=str(audio_path)))
    except TypeError:
        return float(librosa.get_duration(path=str(audio_path)))


def analyze_track_cues(audio_path: Path) -> Dict[str, Any]:
    """
    Performs fast, accurate DJ cue and beat analysis on audio file:
    - Accurate duration
    - Exact BPM
    - Downbeat timestamps (first_downbeat_sec, beat_interval_sec)
    - 16-beat & 32-beat phrase durations
    - Camelot harmonic key
    - First musical onset (cue-in, skips dead air)
    - Outro phrase drop point
    """
    total_duration = get_audio_duration(audio_path)
    window = min(ANALYZER_WINDOW_SEC, total_duration * 0.45)

    # 1. Decode first window at 22050 Hz mono (~2.5 MB RAM)
    y_intro, sr = librosa.load(
        str(audio_path), sr=22050, mono=True, offset=0.0, duration=window
    )

    # Accurate onset/cue-in detection (trim dead digital intro silence)
    rms_intro = librosa.feature.rms(y=y_intro, hop_length=512)[0]
    peak_rms = np.max(rms_intro) if len(rms_intro) > 0 else 0.1
    active_frames = np.where(rms_intro > peak_rms * 0.08)[0]
    if len(active_frames) > 0:
        cue_in_sec = float(librosa.frames_to_time(active_frames[0], sr=sr, hop_length=512))
        cue_in_sec = float(np.clip(cue_in_sec, 0.0, 8.0))
    else:
        cue_in_sec = 0.0

    # BPM & Beat Tracking with Downbeat Extraction
    tempo, beats = librosa.beat.beat_track(y=y_intro, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0]) if tempo is not None else 128.0
    if bpm < 65.0:
        bpm *= 2.0
    elif bpm > 220.0:
        bpm /= 2.0

    if len(beats) > 0:
        beat_times = librosa.frames_to_time(beats, sr=sr)
        first_downbeat_sec = float(beat_times[0])
    else:
        first_downbeat_sec = cue_in_sec

    beat_interval_sec = 60.0 / bpm
    phrase_16_sec = beat_interval_sec * 16.0
    phrase_32_sec = beat_interval_sec * 32.0

    # Camelot Key estimation from chroma
    chroma = librosa.feature.chroma_cqt(y=y_intro, sr=sr, hop_length=1024)
    chroma_mean = np.mean(chroma, axis=1)
    musical_key = estimate_camelot_key(chroma_mean)

    # Midrange energy score (0.0 -> 1.0)
    energy_score = float(np.clip(np.mean(rms_intro) * 4.5, 0.1, 1.0))

    # 2. Decode last window to find Cue_Out / outro drop
    outro_offset = max(0.0, total_duration - window)
    y_outro, _ = librosa.load(
        str(audio_path), sr=22050, mono=True, offset=outro_offset, duration=window
    )
    rms_outro = librosa.feature.rms(y=y_outro, hop_length=512)[0]
    outro_threshold = np.percentile(rms_outro, 35)
    low_energy_frames = np.where(rms_outro < outro_threshold)[0]

    if len(low_energy_frames) > 0:
        rel_outro_sec = float(
            librosa.frames_to_time(low_energy_frames[0], sr=sr, hop_length=512)
        )
        outro_start_sec = outro_offset + rel_outro_sec
        # Snap within the last 6 to 28 seconds of the song
        outro_start_sec = float(np.clip(outro_start_sec, total_duration - 28.0, total_duration - 6.0))
    else:
        # Default to a 16-beat phrase before the end
        outro_start_sec = max(0.0, total_duration - max(8.0, phrase_16_sec))

    return {
        "duration_sec": round(total_duration, 2),
        "bpm": round(bpm, 1),
        "energy_score": round(energy_score, 3),
        "intro_vocal_start_sec": round(cue_in_sec, 2),
        "first_downbeat_sec": round(first_downbeat_sec, 3),
        "beat_interval_sec": round(beat_interval_sec, 3),
        "phrase_16_sec": round(phrase_16_sec, 2),
        "phrase_32_sec": round(phrase_32_sec, 2),
        "outro_start_sec": round(outro_start_sec, 2),
        "musical_key": musical_key,
    }
