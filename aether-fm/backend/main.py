"""
FM Broadcast Server & Industrial Studio Console (main.py)
Includes:
1. Singleton Real-Time Broadcast Engine with multi-listener fan-out.
2. Full-Catalog Track Rotation (`recent_track_ids` exclusion + automatic `play_history` logging
   on every natural track transition so the same songs never loop).
3. Fail-Safe Voice & Text Song Request Pipeline:
   - Server-side 5.5s watchdog timer + explicit `CALL_STOP` handling so the Android app NEVER
     gets stuck on "LISTENING...".
   - Searches local SQLite catalog first, and falls back to live YouTube Studio resolution if you
     request any song outside your local library.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Set
import aiosqlite
from fastapi import FastAPI, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
import numpy as np
import pedalboard
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="librosa")
from pydantic import BaseModel
import uvicorn

from ai_booth.dj_brain import HertaDJBrain
from ai_booth.stt_caller import BoothCallTranscriber
from ai_booth.tts_voice import HertaVoiceSynthesizer, VOICE_PROFILES
from audio_engine.beat_analyzer import analyze_track_cues
from audio_engine.mixer import ThreeDeckRadioMixer
from config import AUTH_TOKEN, BASE_DIR, PORT, TAILSCALE_IP
from database.db_manager import DatabaseManager, compute_track_mood
from ingestion.apple_music_sync import sync_apple_music_playlist_url, sync_apple_music_txt
from ingestion.canonical_lookup import fetch_canonical_metadata
from ingestion.ytmusic_playlist import sync_ytmusic_playlist_url
from settings_store import settings_store
from ingestion.ytdlp_client import (
    YtDlpCacheResolver,
    decode_audio_fast_48k,
    fetch_itunes_cover_url,
)
from remote_tunnel import AutoHandshakeTunnelManager

import logging

logger = logging.getLogger("aether_fm.broadcast")

# Audio and Database singletons
db = DatabaseManager()
mixer = ThreeDeckRadioMixer()
call_stt = BoothCallTranscriber()
yt_resolver = YtDlpCacheResolver()
remote_tunnel = AutoHandshakeTunnelManager(local_port=PORT)

STUDIO_HTML_PATH = BASE_DIR / "studio_ui.html"


class AppleMusicSyncRequest(BaseModel):
    playlist_url: str


class YtMusicPlaylistSyncRequest(BaseModel):
    playlist_url: str


class VoiceUpdateRequest(BaseModel):
    profile_id: Optional[str] = None
    voice_a: Optional[str] = None
    voice_b: Optional[str] = None
    mix_a: Optional[float] = None
    speed: Optional[float] = None
    pitch_semitones: Optional[float] = None
    lang: Optional[str] = None
    engine: Optional[str] = None


class StudioSayRequest(BaseModel):
    text: str
    emotion: str = "smug"


class StudioScriptUpdateRequest(BaseModel):
    script: str


class StudioDjToggleRequest(BaseModel):
    enabled: bool


class StudioQueueTrackRequest(BaseModel):
    track_id: str
    play_now: bool = False


class SongSuggestionRequest(BaseModel):
    query: str
    play_now: bool = False


class EquipPersonaRequest(BaseModel):
    character_id: str


class CreatePersonaRequest(BaseModel):
    name: str
    universe: str = "CUSTOM MARKETPLACE"
    title: str = "CUSTOM BROADCAST HOST"
    tagline: str = ""
    catchphrase: str = ""
    voice_a: str = "af_heart"
    voice_b: str = "af_bella"
    mix_a: float = 0.70
    speed: float = 0.96
    pitch_semitones: float = 0.0
    lang: str = "en-us"


class ConfigureAiEngineRequest(BaseModel):
    llm_mode: Optional[str] = None
    llm_model: Optional[str] = None


class RadioSessionState:
    def __init__(self):
        self.client_sockets: Set[WebSocket] = set()
        self.studio_sockets: Set[WebSocket] = set()
        self.active_card_id: str = "card_herta_overdrive"
        self.active_card_title: str = "CH-01 // OVERDRIVE PROTOCOL"
        self.recent_tracks: list[str] = []
        self.recent_track_ids: list[str] = []
        self.recent_artists: list[str] = []
        self.tracks_scheduled_count: int = 0
        self.telemetry: Dict[str, Any] = {}
        self.last_transcript: str = "SIGNAL LOCKED // 48KHZ MASTER CLOCK ONLINE"
        self.prefetch_lock = asyncio.Lock()
        self.prefetch_task: Optional[asyncio.Task] = None
        self.debounce_prefetch_task: Optional[asyncio.Task] = None
        self.call_watchdog_task: Optional[asyncio.Task] = None
        self.call_active: bool = False
        self.preferred_mood: Optional[str] = None
        self.disliked_moods: list[str] = []
        self.recalculated_for_track_id: str = ""
        self.last_skip_time: float = 0.0


session = RadioSessionState()


async def broadcast_to_all_clients(payload: Dict[str, Any]) -> None:
    msg = json.dumps(payload)
    for ws in list(session.client_sockets):
        try:
            await ws.send_text(msg)
        except Exception:
            session.client_sockets.discard(ws)
    for ws in list(session.studio_sockets):
        try:
            await ws.send_text(msg)
        except Exception:
            session.studio_sockets.discard(ws)


def build_state_snapshot(event_name: str = "STATE") -> Dict[str, Any]:
    now_track = mixer.deck_a_track or {}
    next_track = mixer.deck_b_track
    now_mood = now_track.get("mood") or (compute_track_mood(now_track) if now_track else "STANDBY")
    return {
        "event": event_name,
        "title": now_track.get("title", "INITIALIZING DECK A"),
        "artist": now_track.get("artist", "FM BROADCAST"),
        "album": now_track.get("album", ""),
        "cover_url": now_track.get("cover_url", ""),
        "bpm": now_track.get("bpm", 128.0),
        "track_mood": now_mood,
        "standby_paused": mixer.standby_paused,
        "elapsed_sec": mixer.deck_a_elapsed_sec,
        "duration_sec": mixer.deck_a_duration_sec,
        "card": session.active_card_title,
        "card_id": session.active_card_id,
        "in_transition": mixer.in_transition,
        "transition_progress": round(mixer.transition_progress, 2),
        "up_next": (
            {
                "track_id": next_track.get("track_id", ""),
                "title": next_track.get("title", ""),
                "artist": next_track.get("artist", ""),
                "cover_url": next_track.get("cover_url", ""),
                "bpm": next_track.get("bpm", 128.0),
                "musical_key": next_track.get("musical_key", "8A"),
                "mood": next_track.get("mood") or compute_track_mood(next_track),
            }
            if next_track
            else None
        ),
        "up_next_ready": len(mixer.deck_b_buffer) > 0,
        "speaking": False,
        "last_transcript": "",
        "next_script": "",
        "active_voice_id": "dj_pure",
        "active_voice_name": "CLUB DJ MIXER",
        "active_character_id": "pure_dj",
        "llm_status": "PURE DJ ENGINE ACTIVE",
        "listeners": mixer.active_listener_count,
        "vu_left": round(mixer.vu_left, 3),
        "vu_right": round(mixer.vu_right, 3),
        "remote_url": remote_tunnel.remote_url,
        "remote_host": remote_tunnel.remote_host,
        "remote_status": remote_tunnel.status,
        "lan_ip": remote_tunnel.lan_ip,
        "now_playing": {
            "track_id": now_track.get("track_id", ""),
            "title": now_track.get("title", "INITIALIZING DECK A"),
            "artist": now_track.get("artist", "FM BROADCAST"),
            "cover_url": now_track.get("cover_url", ""),
            "bpm": now_track.get("bpm", 128.0),
            "musical_key": now_track.get("musical_key", "8A"),
            "lufs": now_track.get("integrated_lufs", -14.0),
            "mood": now_mood,
            "elapsed_sec": mixer.deck_a_elapsed_sec,
            "duration_sec": mixer.deck_a_duration_sec,
        },
        "active_card_title": session.active_card_title,
    }


async def _on_deck_promoted_callback(
    promoted_track: Dict[str, Any]
) -> None:
    """
    Invoked by the real-time mixer at the exact moment Deck B becomes Deck A.
    """
    if promoted_track and promoted_track.get("track_id"):
        tid = promoted_track["track_id"]
        if tid not in session.recent_track_ids:
            session.recent_track_ids.append(tid)
            if len(session.recent_track_ids) > 40:
                session.recent_track_ids.pop(0)
        # Log completed on natural transitions
        if time.monotonic() - session.last_skip_time > 5.0:
            try:
                await db.log_play_event(
                    tid,
                    session.active_card_id,
                    float(promoted_track.get("duration_sec", 180.0)),
                    1.0,
                    "completed",
                )
            except Exception:
                pass

    await broadcast_to_all_clients(build_state_snapshot("NOW_PLAYING"))
    if time.monotonic() - session.last_skip_time < 4.0:
        schedule_debounced_prefetch(delay_sec=1.8)
    else:
        trigger_background_prefetch()


async def _on_voice_state_callback(
    speaking: bool, script: str, emotion: str
) -> None:
    if script:
        session.last_transcript = script
    await broadcast_to_all_clients(
        {
            "event": "DJ_STATE",
            "speaking": speaking,
            "transcript": script or session.last_transcript,
            "emotion": emotion,
        }
    )


mixer.on_deck_promoted = _on_deck_promoted_callback
mixer.on_voice_state_change = _on_voice_state_callback


async def _prepare_track_assets(
    chosen_track: Dict[str, Any],
    target_bpm: Optional[float] = None,
) -> Optional[Any]:
    # Look up canonical official metadata (iTunes / Deezer) to obtain exact duration and pristine cover
    try:
        canonical = await fetch_canonical_metadata(
            chosen_track.get("artist", ""), chosen_track.get("title", "")
        )
        if canonical:
            if not chosen_track.get("cover_url") and canonical.get("cover_url"):
                chosen_track["cover_url"] = canonical["cover_url"]
            if canonical.get("duration_sec"):
                chosen_track["duration_sec"] = canonical["duration_sec"]
    except Exception:
        pass

    if not chosen_track.get("cover_url"):
        cover = await fetch_itunes_cover_url(
            chosen_track["artist"], chosen_track["title"]
        )
        if cover:
            chosen_track["cover_url"] = cover

    audio_path = await asyncio.to_thread(
        yt_resolver.resolve_and_cache,
        chosen_track["track_id"],
        chosen_track["source_id"],
        chosen_track.get("duration_sec"),
        chosen_track.get("artist"),
        chosen_track.get("title"),
    )
    if not audio_path or not audio_path.exists():
        return None

    # 1. Fast Librosa Cue & Beat Analysis on background worker thread
    cue_data = None
    try:
        cue_data = await asyncio.to_thread(analyze_track_cues, audio_path)
        chosen_track["bpm"] = cue_data["bpm"]
        chosen_track["energy_score"] = cue_data["energy_score"]
        chosen_track["intro_vocal_start_sec"] = cue_data["intro_vocal_start_sec"]
        chosen_track["outro_start_sec"] = cue_data["outro_start_sec"]
        chosen_track["musical_key"] = cue_data["musical_key"]
        chosen_track["first_downbeat_sec"] = cue_data["first_downbeat_sec"]
        chosen_track["beat_interval_sec"] = cue_data["beat_interval_sec"]
    except Exception as e:
        logger.warning(f"Cue analysis skipped for {chosen_track.get('title')}: {e}")

    stereo_pcm = await asyncio.to_thread(decode_audio_fast_48k, audio_path)
    if len(stereo_pcm) == 0:
        return None

    # 2. Normalize loudness (-14.0 LUFS) on the worker thread
    normalized_pcm, lufs = await asyncio.to_thread(
        mixer.dsp.normalize_track_pcm, stereo_pcm
    )
    chosen_track["integrated_lufs"] = lufs
    chosen_track["duration_sec"] = round(len(normalized_pcm) / 48000.0, 1)

    # 3. Seamless BPM Beat-Matching Time-Stretch for transition window
    if target_bpm and cue_data and cue_data["bpm"] > 40 and target_bpm > 40:
        ratio = float(target_bpm) / float(cue_data["bpm"])
        # If tempos are within +-7%, time-stretch intro window so beat drop aligns with 0 tempo drift
        if 0.93 <= ratio <= 1.07 and abs(ratio - 1.0) >= 0.008:
            try:
                stretch_sec = min(24.0, (len(normalized_pcm) / 48000.0) * 0.35)
                stretch_samples = int(stretch_sec * 48000)
                intro_chunk = normalized_pcm[:stretch_samples]
                stretched_intro = await asyncio.to_thread(
                    pedalboard.time_stretch, intro_chunk, 48000, ratio
                )
                fade_len = int(48000 * 0.005)
                stretched_intro[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)[:, None]
                remainder = normalized_pcm[stretch_samples:].copy()
                remainder[:fade_len] *= np.linspace(0.0, 1.0, fade_len, dtype=np.float32)[:, None]
                normalized_pcm = np.vstack([stretched_intro, remainder])
                chosen_track["stretched_for_mix"] = True
            except Exception as e:
                logger.warning(f"Time-stretch beat matching skipped: {e}")

    await db.upsert_track(chosen_track)
    return normalized_pcm


async def _prefetch_next_track(
    forced_track: Optional[Dict[str, Any]] = None,
    play_immediately: bool = False,
) -> None:
    async with session.prefetch_lock:
        cards = await db.get_pinned_mood_cards()
        active_card = next(
            (c for c in cards if c["card_id"] == session.active_card_id),
            cards[0]
            if cards
            else {
                "card_id": "card_herta_overdrive",
                "title": "Curio: High-Voltage Protocol",
            },
        )
        session.active_card_title = active_card.get(
            "title", "Curio: High-Voltage Protocol"
        )

        current_bpm = mixer.deck_a_track["bpm"] if mixer.deck_a_track else 132.0
        in_bulk_skip = play_immediately or (time.monotonic() - session.last_skip_time < 6.0)

        # Build exclusion list of currently playing, queued, and last 35 played tracks + last 4 artists
        exclude_ids = list(session.recent_track_ids)
        exclude_artists = list(session.recent_artists)
        if mixer.deck_a_track:
            if mixer.deck_a_track.get("track_id"):
                exclude_ids.append(mixer.deck_a_track["track_id"])
            if mixer.deck_a_track.get("artist"):
                exclude_artists.append(mixer.deck_a_track["artist"])
        if mixer.deck_b_track:
            if mixer.deck_b_track.get("track_id"):
                exclude_ids.append(mixer.deck_b_track["track_id"])
            if mixer.deck_b_track.get("artist"):
                exclude_artists.append(mixer.deck_b_track["artist"])

        # Stage 1 + Stage 2 pure algorithmic & mood-reading candidate selection
        candidates = await db.get_top_candidates(
            session.active_card_id,
            current_bpm=current_bpm,
            limit=20,
            exclude_ids=exclude_ids,
            exclude_artists=exclude_artists,
            preferred_mood=session.preferred_mood,
            disliked_moods=session.disliked_moods,
        )

        need_instant_vault = (
            len(mixer.deck_a_buffer) == 0
            or play_immediately
            or in_bulk_skip
            or mixer.deck_a_remaining_sec < 22.0
        ) and not forced_track

        if need_instant_vault:
            cached_first = [c for c in candidates if yt_resolver.is_cached(c["track_id"])]
            if not cached_first:
                all_tracks = await db.get_all_tracks(limit=1000)
                cached_first = [t for t in all_tracks if t["track_id"] not in exclude_ids and yt_resolver.is_cached(t["track_id"])]
            if cached_first:
                candidates = cached_first

        chosen_track = forced_track or (candidates[0] if candidates else None)
        if not chosen_track:
            return

        tid = chosen_track["track_id"]
        if tid not in session.recent_track_ids:
            session.recent_track_ids.append(tid)
            if len(session.recent_track_ids) > 160:
                session.recent_track_ids.pop(0)
        try:
            await db.record_persistent_rotation(tid, max_history=250)
        except Exception:
            pass
        artist_name = (chosen_track.get("artist") or "").strip()
        if artist_name:
            session.recent_artists.append(artist_name)
            if len(session.recent_artists) > 8:
                session.recent_artists.pop(0)

        target_bpm_for_mix = current_bpm if (len(mixer.deck_a_buffer) > 0 and not play_immediately) else None
        stereo_pcm = await _prepare_track_assets(chosen_track, target_bpm=target_bpm_for_mix)

        # Fallback if download failed
        if stereo_pcm is None or len(stereo_pcm) == 0:
            all_tracks = await db.get_all_tracks(limit=1000)
            fallback_pool = [
                t for t in all_tracks
                if t["track_id"] not in exclude_ids and yt_resolver.find_cached_file(t["track_id"]) is not None
            ]
            if not fallback_pool:
                return
            chosen_track = random.choice(fallback_pool[: min(12, len(fallback_pool))])
            stereo_pcm = await _prepare_track_assets(chosen_track, target_bpm=target_bpm_for_mix)
            if stereo_pcm is None or len(stereo_pcm) == 0:
                return

        session.recent_tracks.append(f"{chosen_track['artist']} - {chosen_track['title']}")

        # Fetch transition style from active station
        crossfade_style = active_card.get("crossfade_style", "club_eq_crossover_9s") if active_card else "club_eq_crossover_9s"

        if len(mixer.deck_a_buffer) == 0 or play_immediately:
            mixer.deck_a_buffer = stereo_pcm
            mixer.deck_a_total_samples = len(stereo_pcm)
            mixer.deck_a_played_samples = 0
            mixer.deck_a_track = chosen_track
            await broadcast_to_all_clients(build_state_snapshot("NOW_PLAYING"))
            if play_immediately:
                schedule_debounced_prefetch(delay_sec=1.8)
        else:
            mixer.load_deck_b(
                stereo_pcm,
                track_meta=chosen_track,
                crossfade_style=crossfade_style,
            )
            await broadcast_to_all_clients(build_state_snapshot("UP_NEXT"))


def schedule_debounced_prefetch(delay_sec: float = 2.2) -> None:
    """
    Waits `delay_sec` after a manual skip before prefetching Deck B + running AI TTS.
    If the user presses SKIP again within `delay_sec`, the timer resets so zero resources
    are wasted during bulk skipping!
    """
    if (
        session.debounce_prefetch_task is not None
        and not session.debounce_prefetch_task.done()
    ):
        session.debounce_prefetch_task.cancel()

    async def _delayed():
        try:
            await asyncio.sleep(delay_sec)
            if len(mixer.deck_b_buffer) == 0:
                trigger_background_prefetch()
        except asyncio.CancelledError:
            pass

    session.debounce_prefetch_task = asyncio.create_task(_delayed())


def trigger_background_prefetch(
    forced_track: Optional[Dict[str, Any]] = None,
    play_immediately: bool = False,
) -> None:
    if (play_immediately or forced_track is not None) and (
        session.prefetch_task is not None and not session.prefetch_task.done()
    ):
        session.prefetch_task.cancel()
    if (
        session.prefetch_task is None
        or session.prefetch_task.done()
        or forced_track is not None
        or play_immediately
    ):
        session.prefetch_task = asyncio.create_task(
            _prefetch_next_track(
                forced_track=forced_track, play_immediately=play_immediately
            )
        )


async def _resolve_and_queue_user_request(
    raw_query: str, play_now: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Matches a spoken or typed song/artist request against the local SQLite catalog first,
    or dynamically creates a YouTube Studio track entry if not in the local library.
    """
    clean_q = raw_query.strip()
    if not clean_q:
        return None

    # 1. Search local catalog
    matches = await db.get_all_tracks(search=clean_q, limit=5)
    if matches:
        chosen = matches[0]
        trigger_background_prefetch(forced_track=chosen, play_immediately=play_now)
        return chosen

    # 2. Try matching individual words (e.g. "play some Avicii" -> search "Avicii")
    cleaned_words = (
        clean_q.lower()
        .replace("play", "")
        .replace("put on", "")
        .replace("queue", "")
        .replace("some", "")
        .replace("by", "")
        .strip()
    )
    if cleaned_words:
        matches = await db.get_all_tracks(search=cleaned_words, limit=5)
        if matches:
            chosen = matches[0]
            trigger_background_prefetch(forced_track=chosen, play_immediately=play_now)
            return chosen

    # 3. Dynamic YouTube Music lookup for any song outside the local 121-track library
    digest = hashlib.md5(cleaned_words.encode("utf-8")).hexdigest()[:10]
    dynamic_track = {
        "track_id": f"req_{digest}",
        "title": cleaned_words.title() or clean_q,
        "artist": "Requested Track",
        "album": "Live Request",
        "source_type": "youtube",
        "source_id": cleaned_words or clean_q,
        "cover_url": "",
        "duration_sec": 210.0,
        "bpm": 128.0,
        "energy_score": 0.75,
        "genres": ["electronic", "pop", "rock"],
    }
    await db.upsert_track(dynamic_track)
    trigger_background_prefetch(forced_track=dynamic_track, play_immediately=play_now)
    return dynamic_track


async def _complete_active_voice_call() -> None:
    """
    Finalizes an active voice request (triggered by VAD silence, `CALL_STOP` button tap,
    or the 5.5s safety watchdog timer) so the Android app NEVER stays stuck on Listening!
    """
    if not session.call_active:
        return
    session.call_active = False
    if session.call_watchdog_task and not session.call_watchdog_task.done():
        session.call_watchdog_task.cancel()

    transcript, caller_stereo_48k = await call_stt.transcribe_buffered_call_async()

    await broadcast_to_all_clients(
        {
            "event": "CALL_MIC_CLOSE",
            "status": "processing",
            "user_said": transcript,
        }
    )

    if not transcript.strip():
        return

    # Direct song search and queue
    cards = await db.get_pinned_mood_cards()
    search_q = transcript.strip()
    await _resolve_and_queue_user_request(search_q, play_now=False)


async def _handle_user_skip_action(elapsed_override: Optional[float] = None) -> bool:
    """
    Smart Club DJ Skip:
    1. Enforces the <50% early skip penalty in SQLite:
       - 1st early skip: 24h ban for the day.
       - 2nd early skip: 14-day vault lock.
    2. Immediately triggers a punchy DJ power-cut into Deck B without audio clicks.
    """
    session.last_skip_time = time.monotonic()
    prev_track = mixer.deck_a_track
    elapsed = (
        elapsed_override
        if elapsed_override is not None
        else mixer.deck_a_elapsed_sec
    )

    if prev_track:
        dur = float(prev_track.get("duration_sec", 200.0))
        ratio = min(1.0, elapsed / max(1.0, dur))
        skipped_mood = (
            prev_track.get("mood") or compute_track_mood(prev_track)
        ).upper()

        if ratio < 0.50 and skipped_mood:
            if session.preferred_mood and session.preferred_mood.upper() == skipped_mood:
                session.preferred_mood = None
            if skipped_mood not in session.disliked_moods:
                session.disliked_moods.append(skipped_mood)
                if len(session.disliked_moods) > 4:
                    session.disliked_moods.pop(0)

            # If Deck B queued the exact same unwanted mood, clear it
            if mixer.deck_b_track:
                deck_b_mood = (
                    mixer.deck_b_track.get("mood")
                    or compute_track_mood(mixer.deck_b_track)
                ).upper()
                if deck_b_mood == skipped_mood:
                    mixer.deck_b_buffer = np.zeros((0, 2), dtype=np.float32)
                    mixer.deck_b_track = None

        action = "skipped_early" if ratio < 0.50 else "skipped_mid"
        try:
            await db.log_play_event(
                prev_track["track_id"],
                session.active_card_id,
                elapsed,
                ratio,
                action,
            )
        except Exception:
            pass

    skipped = mixer.trigger_skip(duration_sec=0.8)
    if not skipped:
        trigger_background_prefetch(play_immediately=True)
    return skipped


async def _studio_telemetry_loop() -> None:
    while True:
        await asyncio.sleep(1.0)
        if session.studio_sockets or session.client_sockets:
            snap = build_state_snapshot("STUDIO_TELEMETRY")
            msg = json.dumps(snap)
            for ws in list(session.studio_sockets):
                try:
                    await ws.send_text(msg)
                except Exception:
                    session.studio_sockets.discard(ws)
            for ws in list(session.client_sockets):
                try:
                    await ws.send_text(msg)
                except Exception:
                    session.client_sockets.discard(ws)

        # 80% Listen-Through Adaptive Mood Learning & Deck B Recalculator:
        # When the listener stays for >=78% of the current song on Deck A, lock onto this song's mood
        # and recalculate Deck B if Deck B was holding a different mood!
        now_track = mixer.deck_a_track
        if (
            now_track
            and not mixer.standby_paused
            and mixer.deck_a_duration_sec > 30.0
        ):
            tid = now_track.get("track_id", "")
            ratio = mixer.deck_a_elapsed_sec / max(1.0, mixer.deck_a_duration_sec)
            if (
                tid
                and ratio >= 0.78
                and mixer.deck_a_remaining_sec > 9.5
                and session.recalculated_for_track_id != tid
            ):
                session.recalculated_for_track_id = tid
                liked_mood = (
                    now_track.get("mood") or compute_track_mood(now_track)
                ).upper()
                session.preferred_mood = liked_mood
                if liked_mood in session.disliked_moods:
                    session.disliked_moods.remove(liked_mood)

                # Boost this track/genre in SQLite immediately at 80% listen-through
                try:
                    await db.log_play_event(
                        tid,
                        session.active_card_id,
                        mixer.deck_a_elapsed_sec,
                        ratio,
                        "completed",
                    )
                except Exception:
                    pass

                # Check if Deck B has a DIFFERENT mood than the one the user is enjoying:
                # if so, recalculate Deck B right now so the next song stays in the liked mood!
                next_track = mixer.deck_b_track
                next_mood = (
                    (next_track.get("mood") or compute_track_mood(next_track)).upper()
                    if next_track
                    else ""
                )
                if next_mood != liked_mood:
                    mixer.deck_b_buffer = np.zeros((0, 2), dtype=np.float32)
                    mixer.deck_b_track = None
                    trigger_background_prefetch(play_immediately=False)

        if (
            len(mixer.deck_a_buffer) > 0
            and len(mixer.deck_b_buffer) == 0
            and (session.prefetch_task is None or session.prefetch_task.done())
            and (
                session.debounce_prefetch_task is None
                or session.debounce_prefetch_task.done()
            )
        ):
            trigger_background_prefetch()


async def _gentle_background_cache_warmer() -> None:
    """
    Quietly pre-caches uncached tracks from the 768-track catalog into E:\\FM_Audio_Vault
    only while active listeners are connected (pauses completely during 0-listener standby).
    """
    await asyncio.sleep(15.0)
    while True:
        try:
            if (
                not mixer.standby_paused
                and len(mixer.deck_a_buffer) > 0
                and len(mixer.deck_b_buffer) > 0
            ):
                tracks = await db.get_all_tracks(limit=1000)
                for t in tracks:
                    # Skip tracks that are banned, vaulted, or already failed resolution
                    if t.get("is_banned") or t.get("is_vaulted") or t.get("match_score", 0) < 0:
                        continue
                    if yt_resolver.find_cached_file(t["track_id"]) is None:
                        res = await asyncio.to_thread(
                            yt_resolver.resolve_and_cache,
                            t["track_id"],
                            f"{t.get('artist', '')} - {t.get('title', '')}",
                            t.get("duration_sec"),
                            t.get("artist"),
                            t.get("title"),
                        )
                        if not res:
                            # Mark negative match score so we don't spam query the same failed track every 15s
                            async with aiosqlite.connect(db.db_path) as conn:
                                await conn.execute("UPDATE tracks SET match_score = -1.0 WHERE track_id = ?", (t["track_id"],))
                                await conn.commit()
                        elif not t.get("cover_url"):
                            art = await fetch_itunes_cover_url(t["artist"], t["title"])
                            if art:
                                t["cover_url"] = art
                                await db.upsert_track(t)
                        break
        except Exception:
            pass
        await asyncio.sleep(20.0)


async def _startup_boot_sequence() -> None:
    mixer.ensure_master_loop_running()
    await _prefetch_next_track(play_immediately=True)
    trigger_background_prefetch()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.initialize()
    # Restore persistent station
    try:
        saved_card = settings_store.get("active_card_id")
        if saved_card:
            session.active_card_id = saved_card
    except Exception as e:
        logger.error(f"Failed to restore saved station settings: {e}")

    # Pre-populate session recent_track_ids from persistent rotation so songs don't replay across restarts
    try:
        past_rotation = await db.get_persistent_rotation(limit=150)
        session.recent_track_ids.extend([tid for tid in past_rotation if tid not in session.recent_track_ids])
    except Exception:
        pass
    apple_lib_txt = BASE_DIR.parent.parent / "My Apple Music Library.txt"
    music_txt = BASE_DIR.parent.parent / "Music.txt"
    if apple_lib_txt.exists():
        await sync_apple_music_txt(apple_lib_txt, db)
    elif music_txt.exists():
        await sync_apple_music_txt(music_txt, db)

    async def _on_tunnel(url: str) -> None:
        await broadcast_to_all_clients(build_state_snapshot("TUNNEL_UPDATED"))

    remote_tunnel.on_tunnel_updated = _on_tunnel
    remote_tunnel.start(asyncio.get_running_loop())

    boot_task = asyncio.create_task(_startup_boot_sequence())
    telem_task = asyncio.create_task(_studio_telemetry_loop())
    warmer_task = asyncio.create_task(_gentle_background_cache_warmer())
    yield
    remote_tunnel.stop()
    boot_task.cancel()
    telem_task.cancel()
    warmer_task.cancel()


app = FastAPI(title="FM Broadcast Console", lifespan=lifespan)


# ---------------------------------------------------------------------------
# 1. BROADCASTER STUDIO WEB UI & REST ENDPOINTS
# ---------------------------------------------------------------------------
@app.get("/studio", response_class=HTMLResponse)
async def serve_studio_ui():
    if STUDIO_HTML_PATH.exists():
        return HTMLResponse(STUDIO_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Studio UI file not found</h1>", status_code=404)


@app.get("/api/state")
async def get_station_state():
    return build_state_snapshot("STATE")


@app.get("/api/voices")
async def get_voices():
    return {
        "active_profile_id": "dj_pure",
        "current": {"name": "CLUB DJ MIXER", "engine": "pure_dj"},
        "presets": [{"id": "dj_pure", "name": "CLUB DJ MIXER", "tagline": "Pure DJ Mixer with 3-Band Isolator EQ"}],
        "available_kokoro_voices": [],
    }


@app.post("/api/voices")
async def set_voices(req: VoiceUpdateRequest):
    return await get_voices()


@app.get("/api/studio/personas")
async def studio_get_personas():
    return {
        "active_character_id": "pure_dj",
        "active_character": {"id": "pure_dj", "name": "CLUB DJ MIXER"},
        "characters": [{"id": "pure_dj", "name": "CLUB DJ MIXER", "tagline": "Real-Time 2-Deck Club DJ Mixer"}],
        "llm_mode": "algorithmic_mood",
        "llm_model": "mood_selector_v2",
        "llm_status": "PURE DJ ENGINE ACTIVE",
        "ollama_online": False,
        "installed_models": [],
        "recommended_models": [],
    }


@app.get("/api/studio/rvc/models")
async def studio_get_rvc_models():
    return {
        "models": [],
        "api_alive": False,
    }


@app.post("/api/studio/personas/equip")
async def studio_equip_persona(req: EquipPersonaRequest):
    return await studio_get_personas()


@app.post("/api/studio/personas/create")
async def studio_create_persona(req: CreatePersonaRequest):
    return await studio_get_personas()


@app.post("/api/studio/ai-engine")
async def studio_configure_ai_engine(req: ConfigureAiEngineRequest):
    return await studio_get_personas()


@app.get("/api/voices/preview.wav")
async def preview_voice():
    # Return empty 0.5s stereo WAV for UI compatibility
    return Response(content=b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x02\x00\x80\xbb\x00\x00\x00\xee\x02\x00\x04\x00\x10\x00data\x00\x00\x00\x00", media_type="audio/wav")


@app.post("/api/studio/skip")
async def studio_skip():
    skipped = await _handle_user_skip_action()
    return {"status": "ok", "instant_crossfade": skipped}


@app.post("/api/studio/flag-wrong-song")
async def studio_flag_wrong_song():
    """
    Flags the currently playing track as a bad/unofficial version or cover:
    1. Unlinks and bans the cached file on disk.
    2. Resets the track's yt_video_id & match_score in SQLite.
    3. Triggers immediate crossfade to the next track.
    """
    now_track = mixer.deck_a_track
    if now_track and now_track.get("track_id"):
        tid = now_track["track_id"]
        cached = yt_resolver.find_cached_file(tid)
        if cached and cached.exists():
            try:
                cached.unlink(missing_ok=True)
                yt_resolver._cached_track_ids.discard(tid)
            except Exception:
                pass
        try:
            async with aiosqlite.connect(db.db_path) as conn:
                await conn.execute(
                    "UPDATE tracks SET yt_video_id = NULL, resolved_title = NULL, match_score = 0.0, is_verified = 0 WHERE track_id = ?",
                    (tid,),
                )
                await conn.commit()
        except Exception:
            pass

    skipped = await _handle_user_skip_action()
    return {"status": "ok", "flagged_track": now_track.get("title") if now_track else None}


class SelectStationRequest(BaseModel):
    station_id: str


@app.get("/api/studio/stations")
async def studio_get_stations():
    stations = await db.get_pinned_mood_cards()
    return {
        "active_station_id": session.active_card_id,
        "stations": stations,
    }


@app.post("/api/studio/select-station")
async def studio_select_station(req: SelectStationRequest):
    stations = await db.get_pinned_mood_cards()
    target = next((s for s in stations if s["card_id"] == req.station_id), None)
    if target:
        session.active_card_id = target["card_id"]
        session.active_card_title = target["title"]
        settings_store.set("active_card_id", target["card_id"])
        trigger_background_prefetch(play_immediately=False)
        await broadcast_to_all_clients(build_state_snapshot("MOOD_CHANGED"))
        return {"status": "ok", "active_station_id": session.active_card_id}
    return {"status": "not_found"}


@app.get("/api/studio/vault-banned")
async def studio_get_vault_banned():
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """
            SELECT track_id, title, artist, early_skip_count, is_vaulted, cooldown_until
            FROM tracks
            WHERE is_vaulted = 1 OR (cooldown_until IS NOT NULL AND cooldown_until > datetime('now'))
            ORDER BY cooldown_until DESC
            """
        ) as cur:
            rows = await cur.fetchall()
            return {"banned_tracks": [dict(r) for r in rows]}


@app.post("/api/studio/unban-track")
async def studio_unban_track(req: StudioQueueTrackRequest):
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute(
            """
            UPDATE tracks
            SET is_vaulted = 0, early_skip_count = 0, cooldown_until = NULL
            WHERE track_id = ?
            """,
            (req.track_id,),
        )
        await conn.commit()
    return {"status": "ok", "unbanned_track_id": req.track_id}


# In-memory rolling log buffer for studio diagnostics
DIAGNOSTIC_LOG_BUFFER: deque[str] = deque(maxlen=250)


class InMemoryDiagnosticsHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            DIAGNOSTIC_LOG_BUFFER.append(msg)
        except Exception:
            pass


_diag_handler = InMemoryDiagnosticsHandler()
_diag_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(_diag_handler)
logging.getLogger("aether_fm.ytdlp").addHandler(_diag_handler)
logging.getLogger("aether_fm.broadcast").addHandler(_diag_handler)


@app.get("/api/studio/diagnostics")
async def studio_get_diagnostics():
    """Diagnostic backdoor endpoint: inspect recent logs, mixer buffers, and cache telemetry."""
    return {
        "status": "online",
        "recent_logs": list(DIAGNOSTIC_LOG_BUFFER),
        "deck_a": {
            "track": mixer.deck_a_track,
            "elapsed_sec": mixer.deck_a_elapsed_sec,
            "duration_sec": mixer.deck_a_duration_sec,
            "remaining_sec": mixer.deck_a_remaining_sec,
            "samples_in_buffer": len(mixer.deck_a_buffer),
        },
        "deck_b": {
            "track": mixer.deck_b_track,
            "ready": len(mixer.deck_b_buffer) > 0,
            "samples_in_buffer": len(mixer.deck_b_buffer),
            "transition_style": mixer.transition_style,
        },
        "stream": {
            "in_transition": mixer.in_transition,
            "transition_progress": round(mixer.transition_progress, 3),
            "active_listeners": mixer.active_listener_count,
            "standby_paused": mixer.standby_paused,
            "cached_vault_files": len(yt_resolver._cached_track_ids),
        },
    }


@app.post("/api/studio/cycle-mood")
async def studio_cycle_mood():
    cards = await db.get_pinned_mood_cards()
    if cards:
        idx = next(
            (i for i, c in enumerate(cards) if c["card_id"] == session.active_card_id),
            0,
        )
        next_card = cards[(idx + 1) % len(cards)]
        session.active_card_id = next_card["card_id"]
        session.active_card_title = next_card["title"]
        settings_store.set("active_card_id", next_card["card_id"])
        trigger_background_prefetch(play_immediately=False)
        await broadcast_to_all_clients(build_state_snapshot("MOOD_CHANGED"))
    return {"status": "ok", "active_card_id": session.active_card_id}


@app.post("/api/studio/dj-toggle")
async def studio_dj_toggle(req: StudioDjToggleRequest):
    return {"status": "ok", "dj_enabled": req.enabled}


@app.post("/api/studio/update-next-script")
async def studio_update_next_script(req: StudioScriptUpdateRequest):
    return {"status": "ok"}


@app.post("/api/studio/say")
async def studio_say_on_air(req: StudioSayRequest):
    return {"status": "ok"}


@app.post("/api/studio/announce-track")
async def studio_announce_current_track():
    return {"status": "ok"}


@app.post("/api/studio/discover-related")
async def studio_discover_related(play_now: bool = False):
    return {"status": "ok"}


@app.post("/api/studio/request-song")
async def studio_request_song(req: SongSuggestionRequest):
    matched = await _resolve_and_queue_user_request(req.query, play_now=req.play_now)
    return {"status": "ok", "track": matched}


@app.get("/api/studio/tracks")
async def studio_list_tracks(q: str = Query("")):
    tracks = await db.get_all_tracks(search=q, limit=1000)
    for t in tracks:
        t["cached"] = yt_resolver.find_cached_file(t["track_id"]) is not None
    return {"tracks": tracks}


@app.post("/api/studio/queue-track")
async def studio_queue_track(req: StudioQueueTrackRequest):
    track = await db.get_track_by_id(req.track_id)
    if not track:
        return {"status": "not_found"}
    trigger_background_prefetch(forced_track=track, play_immediately=req.play_now)
    return {"status": "ok"}


@app.post("/api/studio/precache")
async def studio_batch_precache():
    async def _run_batch():
        tracks = await db.get_all_tracks(limit=15)
        for t in tracks:
            if yt_resolver.find_cached_file(t["track_id"]) is None:
                await asyncio.to_thread(
                    yt_resolver.resolve_and_cache,
                    t["track_id"],
                    f"{t.get('artist', '')} - {t.get('title', '')}",
                    t.get("duration_sec"),
                    t.get("artist"),
                    t.get("title"),
                )
            if not t.get("cover_url"):
                art = await fetch_itunes_cover_url(t["artist"], t["title"])
                if art:
                    t["cover_url"] = art
                    await db.upsert_track(t)

    asyncio.create_task(_run_batch())
    return {"status": "caching_started"}


@app.post("/api/sync-apple-music")
async def sync_apple_playlist(req: AppleMusicSyncRequest):
    imported_count = await sync_apple_music_playlist_url(req.playlist_url, db)
    return {"status": "ok", "imported_tracks": imported_count}


@app.post("/api/sync-ytmusic")
async def sync_ytmusic_playlist(req: YtMusicPlaylistSyncRequest):
    imported_count = await sync_ytmusic_playlist_url(req.playlist_url, db)
    return {"status": "ok", "imported_tracks": imported_count}


# ---------------------------------------------------------------------------
# 2. LIVE OPUS & MP3 AUDIO STREAM & WEBSOCKETS
# ---------------------------------------------------------------------------
@app.get("/stream.opus")
async def stream_opus(
    tier: str = Query("mobile_saver"),
    profile: str = Query("flat"),
):
    return StreamingResponse(
        mixer.opus_stream_generator(tier=tier, profile=profile),
        media_type="audio/ogg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/stream.mp3")
async def stream_mp3(
    bitrate: str = Query("192k"),
):
    return StreamingResponse(
        mixer.mp3_stream_generator(bitrate=bitrate),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
        },
    )


@app.websocket("/ws/studio")
async def websocket_studio(ws: WebSocket):
    await ws.accept()
    session.studio_sockets.add(ws)
    await ws.send_text(json.dumps(build_state_snapshot("STATE")))
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
    except Exception:
        pass
    finally:
        session.studio_sockets.discard(ws)


@app.websocket("/ws/session")
async def websocket_session(ws: WebSocket, token: str = Query("")):
    if token and token != AUTH_TOKEN:
        await ws.close(code=1008)
        return

    await ws.accept()
    session.client_sockets.add(ws)
    mixer.ensure_master_loop_running()

    await ws.send_text(json.dumps(build_state_snapshot("NOW_PLAYING")))

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break

            # 1. Binary Frame: Live 16kHz Int16 PCM microphone chunk during Voice Request
            if "bytes" in message and message["bytes"] is not None:
                if session.call_active:
                    silence_reached = call_stt.ingest_pcm_int16_chunk(message["bytes"])
                    if silence_reached:
                        await _complete_active_voice_call()
                continue

            # 2. JSON Control Frame
            if "text" in message and message["text"]:
                payload = json.loads(message["text"])
                event = payload.get("event")

                if event == "SESSION_START":
                    session.telemetry = payload
                    await ws.send_text(json.dumps(build_state_snapshot("NOW_PLAYING")))
                    if len(mixer.deck_a_buffer) == 0:
                        trigger_background_prefetch(play_immediately=True)

                elif event == "TELEMETRY_PING":
                    session.telemetry = payload

                elif event == "SKIP_TRACK":
                    elapsed = float(
                        payload.get("elapsed_sec", mixer.deck_a_elapsed_sec)
                    )
                    await _handle_user_skip_action(elapsed_override=elapsed)

                elif event == "STAR_TRACK":
                    if mixer.deck_a_track:
                        liked_mood = (
                            mixer.deck_a_track.get("mood")
                            or compute_track_mood(mixer.deck_a_track)
                        ).upper()
                        session.preferred_mood = liked_mood
                        if liked_mood in session.disliked_moods:
                            session.disliked_moods.remove(liked_mood)
                        await db.log_play_event(
                            mixer.deck_a_track["track_id"],
                            session.active_card_id,
                            180.0,
                            1.0,
                            "starred",
                        )

                elif event == "ACTIVATE_CARD":
                    session.active_card_id = payload.get(
                        "card_id", session.active_card_id
                    )
                    cards = await db.get_pinned_mood_cards()
                    for c in cards:
                        if c["card_id"] == session.active_card_id:
                            session.active_card_title = c["title"]
                    trigger_background_prefetch(play_immediately=False)
                    await broadcast_to_all_clients(
                        build_state_snapshot("MOOD_CHANGED")
                    )

                elif event == "CYCLE_MOOD_CARD":
                    await studio_cycle_mood()

                elif event == "SET_VOICE_PROFILE":
                    pass

                elif event == "ANNOUNCE_NOW":
                    pass

                elif event == "DISCOVER_RELATED":
                    pass

                elif event == "REQUEST_SONG":
                    query = str(payload.get("query", "")).strip()
                    play_now = bool(payload.get("play_now", False))
                    if query:
                        await _resolve_and_queue_user_request(query, play_now=play_now)

                elif event == "CALL_START":
                    if session.call_active:
                        # Tapping a second time finishes the call immediately!
                        await _complete_active_voice_call()
                    else:
                        call_stt.reset_call_buffer()
                        session.call_active = True
                        await ws.send_text(
                            json.dumps({"event": "CALL_MIC_OPEN", "status": "listening"})
                        )

                        async def _call_watchdog():
                            await asyncio.sleep(5.5)
                            if session.call_active:
                                await _complete_active_voice_call()

                        if (
                            session.call_watchdog_task
                            and not session.call_watchdog_task.done()
                        ):
                            session.call_watchdog_task.cancel()
                        session.call_watchdog_task = asyncio.create_task(
                            _call_watchdog()
                        )

                elif event == "CALL_STOP":
                    await _complete_active_voice_call()

                elif event == "SESSION_END":
                    session.client_sockets.discard(ws)
                    await ws.close()
                    return

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        session.client_sockets.discard(ws)


if __name__ == "__main__":
    uvicorn.run("main:app", host=TAILSCALE_IP, port=PORT, reload=False)
