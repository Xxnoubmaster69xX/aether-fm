# AetherFM // Autonomous Real-Time DJ Broadcast Engine

An autonomous, 24/7 personal radio station and broadcast engine powered by real-time audio DSP, club DJ mixing algorithms, and multi-platform streaming.

---

## Features

- **Club DJ Transition Engine**:
  - **Phrase & Downbeat Grid Alignment**: Synchronizes musical phrases (4-bar / 16-beat for EDM/J-Core/J-Pop, 2-bar / 8-beat for Rock/Metal) with sample-level phase locking on kick drums.
  - **Native C++ Time-Stretch Beat-Matching**: Matches incoming tempos ($\pm 7\%$) via `pedalboard.time_stretch` with zero pitch shifting.
  - **3-Band Isolator Bass Swap**: Hard isolates sub-bass (<280Hz) on Deck B during intro buildup and executes an instant bass swap on the Bar 5 downbeat drop.
  - **Pioneer DJM Resonant Filter Sweeps & Echo Freeze**: Outgoing tracks sweep out with resonant HPF (320Hz $\to$ 3,600Hz) and beat-synced echo diffusion. Manual skips trigger a dub delay tail.

- **6 Dedicated Genre Stations**:
  - `CH-01 // ROCK & METAL`: Alternative, Post-Hardcore, Metalcore, J-Rock & Punk (4s punchy cuts).
  - `CH-02 // COUNTRY & HEARTLAND`: Modern Country, Americana, Southern Rock & Folk (5s warm acoustic fades).
  - `CH-03 // J-POP & TOKYO GROOVE`: Modern J-Pop, Anime Pop, Late-Night Tokyo Groove & Vocaloid (6.5s harmonic mixes).
  - `CH-04 // TOKYO HYPER J-CORE`: Fast-paced J-Core, Happy Hardcore, Speedcore & Artcore (7.5s isolator sweeps).
  - `CH-05 // FESTIVAL BASS & CLUB EDM`: Mainstage EDM, Bass House, Melodic Bass & Future Bass (9s club isolator drops).
  - `CH-06 // CINEMATIC GAME OST`: Orchestral, Boss Themes, Cyberpunk & Battle Soundtracks (8s cinematic crossfades).
  - `CH-00 // FULL SPECTRUM FLOW`: Unrestricted algorithmic curation across all genres.

- **Smart Skip Penalties & Mood Steering**:
  - Skipping before 50% triggers a 24-hour day-ban.
  - Repeated early skips trigger a 14-day vault lock.
  - Real-time rolling session mood vector dynamically shifts away from skipped musical clusters.

- **Multi-Listener Streaming Architecture**:
  - Zero-latency broadcast engine with 6.0-second instant pre-burst buffer for stutter-free listening over mobile, Wi-Fi, and Starlink.
  - Universal MP3 (`/stream.mp3`) and Opus (`/stream.opus`) streaming endpoints.

- **Industrial Studio Console UI**:
  - Live VU meters, deck telemetry, Camelot key & BPM displays, 50% skip countdown, and one-click station switching.

---

## Architecture Overview

```
radio/
├── aether-fm/
│   ├── backend/
│   │   ├── audio_engine/
│   │   │   ├── beat_analyzer.py      # Librosa BPM, downbeat grid & Camelot key analysis
│   │   │   ├── dsp_effects.py        # 3-band isolator, bass-swap, filter sweeps & limiter
│   │   │   ├── mixer.py              # 2-deck real-time club DJ mixer engine
│   │   │   └── listening_profiles.py # Acoustic profiles (car, headphone, flat)
│   │   ├── database/
│   │   │   ├── db_manager.py         # Async SQLite manager, stations & candidate selector
│   │   │   └── schema.sql            # Catalog schema, telemetry, stations & memory
│   │   ├── ingestion/
│   │   │   ├── ytdlp_client.py       # High-fidelity audio cache resolver
│   │   │   ├── canonical_lookup.py   # iTunes & Deezer metadata enrichment
│   │   │   ├── apple_music_sync.py   # Apple Music library import
│   │   │   └── ytmusic_playlist.py   # YouTube Music playlist sync
│   │   ├── config.py                 # Hardware budgets, paths, and sample rate settings
│   │   ├── main.py                   # FastAPI server, WebSocket hub & streaming endpoints
│   │   ├── studio_ui.html            # Web-based industrial broadcast studio console
│   │   └── requirements.txt
│   └── mobile_app/                   # Flutter mobile client for Android
└── Start_FM_Station.bat              # One-click Windows startup script
```

---

## Quick Start (Windows)

1. **Prerequisites**:
   - Python 3.10+ (Python 3.12 recommended)
   - FFmpeg installed and available on PATH

2. **Setup & Dependencies**:
   ```powershell
   cd aether-fm/backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. **Launch Broadcast Station**:
   Double-click `Start_FM_Station.bat` or run:
   ```powershell
   cd aether-fm/backend
   .\.venv\Scripts\python.exe main.py
   ```

4. **Access the Studio Console**:
   Open your browser to: [http://localhost:8000/studio](http://localhost:8000/studio)

---

## License

MIT License. Built for personal broadcasting and high-fidelity algorithmic curation.
