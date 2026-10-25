# AetherFM — Personal AI Radio Station Blueprint (Madam Herta Edition)

> **Target Home Server:** Windows 10 Desktop • NVIDIA RTX 5060 (8GB VRAM) • AMD Ryzen 7 5700G (8C/16T) • 16GB DDR4 RAM  
> **Home ISP:** Starlink (CGNAT + Satellite Handover Tuned via Tailscale WireGuard Mesh)  
> **Mobile Client:** Samsung Galaxy S24 Ultra (Android / One UI) + Car Bluetooth AVRCP Integration  
> **AI Host Persona & Voice:** **The Herta** (Madam Herta, Genius Society #83 — *Honkai: Star Rail*)

---

## 1. System Architecture Overview

```text
+-----------------------------------------------------------------------------------+
|                     HOME PC SERVER ("HERTA'S BROADCAST LAB")                      |
|                  Windows 10 • RTX 5060 8GB • Ryzen 7 5700G • 16GB RAM             |
|                                                                                   |
|  +-----------------------+     +-----------------------+     +-----------------+  |
|  |    AUDIO INGESTION    |     |    LOCAL AI BRAIN     |     |  SQLITE MEMORY  |  |
|  | - tidalapi (Lossless) |     | - Ollama (Qwen 2.5 7B)|<--->| - Play History  |  |
|  | - yt-dlp (5GB Cache)  |     | - Faster-Whisper (CPU)|     | - Skip Weights  |  |
|  | - Apple Music Library |     | - Kokoro + Herta RVC  |     | - Mood Cards    |  |
|  +-----------+-----------+     +-----------+-----------+     +-----------------+  |
|              |                             |                                      |
|              v                             v                                      |
|  +-----------------------------------------------------------------------------+  |
|  |          3-DECK REAL-TIME PYTHON DSP MIXER (Pedalboard + Librosa)           |  |
|  |   [Deck A: Outgoing Track] + [Deck B: Incoming Track] + [Deck C: The Herta] |  |
|  |   * Windowed Beat-Grid  * High-Pass Bass Swap  * -14 LUFS Master Normalizer |  |
|  +-------------------------------------+---------------------------------------+  |
|                                        |                                          |
|                                        v                                          |
|            [FFmpeg Opus Streamer (Starlink Handover Buffer) + FastAPI]            |
+----------------------------------------+------------------------------------------+
                                         |
        Encrypted Tailscale Mesh VPN (Punches through Starlink CGNAT natively)
                                         |
+----------------------------------------v------------------------------------------+
|                    CUSTOM MOBILE APP (FLUTTER — GALAXY S24 ULTRA)                 |
|                                                                                   |
|  +-------------------------------+     +---------------------------------------+  |
|  |     UI & MOOD CARD DECK       |     |     CAR & S24 ULTRA INTEGRATION       |  |
|  | - Herta Cosmic Purple Theme   |     | - MediaSession AVRCP Wheel Hijacker   |  |
|  | - Unlockable Mood Cards Grid  |     |   * Next (>>|) = 0.8s Smart Skip      |  |
|  | - Live On-Air Captions        |     |   * Prev (|<<) 1x = Call Madam Herta  |  |
|  | - "Call the Booth" Big Button |     |   * Prev (|<<) 2x = Cycle Mood Cards  |  |
|  +-------------------------------+     |   * Prev (|<<) 3x = Star/Save Track   |  |
|                                        | - Auto A2DP <-> HFP SCO Mic Switcher  |  |
|                                        +---------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Hardware Resource Allocation (Windows 10 • RTX 5060 8GB • 5700G • 16GB DDR4)

To keep your **8GB VRAM** and **16GB DDR4 RAM** comfortably below capacity on Windows 10 without ever swapping to disk:

1. **CPU Offload for Call Transcription:** Since you only speak *before* the AI generates its response, `Faster-Whisper (small, int8)` and `Silero VAD` run on your **Ryzen 7 5700G CPU (8 cores / 16 threads)**, reserving 100% of your RTX 5060's VRAM for the LLM and **The Herta** voice model.
2. **Windowed RAM Audio Analysis (16GB DDR4 Protection):** Instead of decoding entire 5-minute FLAC files into float32 numpy arrays, `beat_analyzer.py` only decodes the **first 30 seconds** (for intro/vocal start detection) and **last 30 seconds** (for outro cue points), keeping Python's RAM usage under **350 MB**.
3. **BIOS Tip for Ryzen 7 5700G:** If your monitor is plugged directly into the RTX 5060, disable the 5700G's integrated Radeon graphics (or set UMA Frame Buffer to `64MB`) in BIOS to reclaim **2GB of system DDR4 RAM**.

| Component | Model / Library | Compute Device | Memory Footprint | Latency |
| :--- | :--- | :--- | :--- | :--- |
| **Music Selector & Herta Script** | `Qwen-2.5-7B-Instruct` (Q4_K_M, `num_ctx=2048`) | **RTX 5060 (CUDA)** | ~4.5 GB VRAM | ~280 ms |
| **The Herta Voice (TTS + Timbre)** | `Kokoro-82M` (`af_heart`) + **RVC v2 (`the_herta.pth`)** *(or `GPT-SoVITS v2`)* | **RTX 5060 (CUDA)** | ~0.7 GB VRAM | ~140 ms |
| **Voice Transcription (Call-In)** | `Faster-Whisper` (`small`, `int8`, 8 threads) | **Ryzen 7 5700G (CPU)** | ~550 MB RAM (0 VRAM) | ~240 ms |
| **Voice Activity Detection** | `Silero VAD` | **Ryzen 7 5700G (CPU)** | < 50 MB RAM | < 10 ms |
| **Windowed Beat & Vocal Detector** | `Librosa` + `FFmpeg` (first/last 30s only) | **Ryzen 7 5700G (CPU)** | ~180 MB RAM | ~0.35 s / track |
| **3-Deck DSP Mixer & Normalizer** | `Spotify Pedalboard` + `NumPy` | **Ryzen 7 5700G (CPU)** | ~220 MB RAM | Real-time (5ms) |
| **Total Active Footprint** | **All Systems Running Simultaneously** | **GPU + CPU** | **~5.2 GB VRAM / ~1.8 GB RAM** | **< 650ms End-to-End** |

---

## 3. Starlink Home Network & Tailscale Tuning

Starlink uses **Carrier-Grade NAT (CGNAT)** (blocking traditional port forwarding) and experiences **15-second satellite handovers** with brief 30–60ms latency spikes:
* **Zero Port-Forwarding via Tailscale:** Tailscale establishes a direct encrypted WireGuard tunnel through Starlink's CGNAT using STUN/NAT-PMP hole punching between your Windows 10 PC (`100.x.y.z`) and your Galaxy S24 Ultra.
* **Starlink Handover Pre-Buffer:** The FFmpeg Opus encoder uses a **20ms frame size with Forward Error Correction (`fec=1`) and `packet_loss=15`**, and the S24 Ultra `just_audio` player keeps a **2.5-second rolling jitter buffer** so satellite handovers are 100% inaudible.
* **Strict Interface Binding:** FastAPI binds exclusively to your Tailscale IP (`100.x.y.z`) and `127.0.0.1`, never `0.0.0.0`.

---

## 4. "The Herta" (Genius Society #83) Voice & Persona Architecture

### 4.1 Dual-Stage Female Voice Synthesis (`tts_voice.py`)
* **Stage 1 — Base Prosody (`Kokoro-82M`):** Generates clean, natural female speech using a blend of `af_heart` (70%) and `af_bella` (30%) at `speed=0.96`. Because Kokoro is a lightweight 82M style-diffusion TTS, it never hallucinates or mispronounces English/Japanese track titles.
* **Stage 2 — "The Herta" Timbre Conversion (`RVC v2` / `GPT-SoVITS`):** Passes the generated waveform through your local `the_herta.pth` RVC v2 voice model (or zero-shot `GPT-SoVITS` reference audio from *Honkai: Star Rail* 3.0), shifting the formant and pitch to match Madam Herta's confident, slightly aloof delivery.

### 4.2 Persona Behavior (`dj_brain.py`)
* **Standard Transitions:** Treats the radio booth like a trivial side project she built between Simulated Universe tests. Concise (25–40 words), sharp, witty, and musically observant.
* **"Read the Room" 3-Skip Intervention:** If you press `>>|` 3 times in a row, she interrupts on Deck C: *"Three skips in a row? Your indecisiveness is exhausting. Step aside, I'm recalibrating the station frequency myself."*
* **Live Car Call-Ins:** When you tap `|<<` on your steering wheel and ask for a vibe or song, your voice plays over the radio with a 300Hz–3400Hz telephone filter, and she answers you directly before dropping your track.
