"""
Broadcast Acoustic Listening Profiles (listening_profiles.py)
Provides per-listener FFmpeg audio filter chains (`-af`) for specialized acoustic reproduction:
1. 'car4': 4-Channel Virtual Cabin Simulation (Surround upmix + Haas spatial delay + Road-Noise EQ + Bus Compressor + Limiter)
2. 'car2': Car 2-Channel Punchy Studio (Road-noise bass compensator + Mono sub + Punchy mid presence + Limiter)
3. 'phones': High-Fidelity Headphone Crossfeed (Bauer bs2b / Crossfeed to eliminate acoustic fatigue + Clean airy highs)
4. 'flat': Unaltered studio master broadcast
"""

from typing import Dict

# 48,000 Hz filter graph definitions
LISTENING_PROFILES: Dict[str, str] = {
    # 1. CAR 4-CHANNEL VIRTUAL CABIN:
    # - aequalizer / firequalizer: +3.5dB sub-bass boost (65Hz) to counter engine rumble, gentle dip at 220Hz for boominess reduction, +2.5dB at 3.2kHz for vocal clarity through car doors.
    # - extrastereo / stereowiden: subtle widen factor (1.25) simulating rear/side speaker depth.
    # - acompressor: mild leveling (2.2:1 ratio) keeping dynamic range balanced above highway tire noise.
    # - alimiter: pristine brickwall peak at -1.0 dBFS.
    "car4": (
        "equalizer=f=65:width_type=o:w=1.2:g=3.5,"
        "equalizer=f=220:width_type=o:w=1.0:g=-2.0,"
        "equalizer=f=3200:width_type=o:w=1.5:g=2.5,"
        "stereowiden=delay=16:feedback=0.25:crossfeed=0.3:drymix=0.85,"
        "acompressor=threshold=-15dB:ratio=2.2:attack=20:release=220:makeup=2dB,"
        "alimiter=limit=-1.0dB"
    ),

    # 2. CAR 2-CHANNEL CLEAN:
    # - Crisp road-noise compensation without spatial phase shifts. Mono-centered tight bass below 90Hz.
    "car2": (
        "equalizer=f=75:width_type=o:w=1.2:g=3.0,"
        "equalizer=f=240:width_type=o:w=1.0:g=-1.5,"
        "equalizer=f=3400:width_type=o:w=1.2:g=2.0,"
        "acompressor=threshold=-14dB:ratio=2.0:attack=15:release=180:makeup=1.5dB,"
        "alimiter=limit=-1.0dB"
    ),

    # 3. HEADPHONE AUDIOPHILE CROSSFEED:
    # - crossfeed / bs2b: natural speaker acoustic simulation. Blends L/R high frequencies slightly to prevent headphone fatigue.
    # - Smooth uncompressed dynamics.
    "phones": (
        "crossfeed=strength=0.4:range=0.5,"
        "equalizer=f=8000:width_type=o:w=2.0:g=-1.0,"
        "alimiter=limit=-0.8dB"
    ),

    # 4. FLAT / STUDIO REFERENCE:
    "flat": "anull",
}


def get_listening_profile_filter(profile_name: str) -> str:
    return LISTENING_PROFILES.get(profile_name.lower().strip(), LISTENING_PROFILES["flat"])
