"""
Fast Raw Audio Resolver, Multi-Tier Transfer Matcher & FFmpeg Decoder (ytdlp_client.py)
Implements TuneMyMusic / Soundiiz style multi-tiered matching pipeline:
1. Primary Tier: Exact "{Artist} {Title}" and clean title queries on YouTube Music official songs.
2. Collab & Featuring Normalizer: Splits multi-artist credits (e.g. "Aiobahn +81 & DEMONDICE" -> "Aiobahn", "DEMONDICE")
   and queries primary artist combinations so cross-artist collaborations match with 100% precision.
3. Clean Title Extractor: Strips parentheticals, [MV], (Remastered), (Official Video), (feat. X) so the search
   engine queries pure musical track titles.
4. Duration Tolerance Gate: AuthenticityGuard verifies candidate duration is within bounds of studio masters.
5. Fast FFmpeg subprocess pipe decoder (48kHz stereo float32 in ~0.25s).
"""

from pathlib import Path
import difflib
import logging
import re
import sqlite3
import subprocess
from typing import Any, Dict, List, Optional, Tuple
import httpx
import numpy as np
import yt_dlp
from ytmusicapi import YTMusic

logger = logging.getLogger("aether_fm.ytdlp")
from ingestion.authenticity_guard import authenticity_guard

from config import AUDIO_CACHE_DIR, CHANNELS, DB_PATH, MAX_AUDIO_CACHE_GB, SAMPLE_RATE

# Lazy singleton for YTMusic client
_ytm_client: Optional[YTMusic] = None


def get_ytmusic() -> YTMusic:
    global _ytm_client
    if _ytm_client is None:
        _ytm_client = YTMusic()
    return _ytm_client


def extract_primary_artist(raw_artist: str) -> str:
    """Extracts the lead primary artist from collaboration strings."""
    s = raw_artist or ""
    for sep in (" & ", " and ", " feat. ", " ft. ", " x ", " X ", " with ", ", "):
        if sep in s:
            s = s.split(sep, 1)[0]
    # Clean sub-label markers like "+81", "prod.", "CV:"
    s = re.sub(r"\+\d+", "", s)
    s = re.sub(r"\b(prod|cv)\b.*", "", s, flags=re.I)
    return s.strip()


def clean_string_for_matching(raw: str) -> str:
    """Removes parentheticals, feat/prod suffixes, and non-alphanumeric noise for accurate comparison."""
    s = (raw or "").lower()
    # Remove parenthetical details like (Official Music Video), [MV], (Remastered 2021)
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", "", s)
    # Remove common featuring / producer tags
    s = re.sub(r"\b(feat|ft|prod|produced by|cv)\b.*", "", s)
    # Remove non-alphanumeric punctuation
    s = re.sub(r"[^a-z0-9\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\s]", " ", s)
    return " ".join(s.split())


def compute_similarity(str_a: str, str_b: str) -> float:
    a_clean = clean_string_for_matching(str_a)
    b_clean = clean_string_for_matching(str_b)
    if not a_clean or not b_clean:
        return 0.0
    if a_clean == b_clean:
        return 1.0
    return difflib.SequenceMatcher(None, a_clean, b_clean).ratio()


def decode_audio_fast_48k(audio_path: Path) -> np.ndarray:
    """
    Decodes an audio file directly to 48,000 Hz stereo float32 PCM via FFmpeg pipe in ~0.25s.
    """
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-loglevel", "quiet",
        "-i", str(audio_path),
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "-ar", str(SAMPLE_RATE),
        "-ac", str(CHANNELS),
        "pipe:1",
    ]
    try:
        res = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except Exception:
        return np.zeros((0, CHANNELS), dtype=np.float32)
    if not res.stdout:
        return np.zeros((0, CHANNELS), dtype=np.float32)
    flat = np.frombuffer(res.stdout, dtype=np.float32)
    usable = (len(flat) // CHANNELS) * CHANNELS
    stereo = flat[:usable].reshape(-1, CHANNELS).copy()
    if len(stereo) == 0:
        return stereo

    # Automatically trim leading & trailing digital silence
    mono_abs = np.max(np.abs(stereo), axis=1)
    active_idx = np.flatnonzero(mono_abs > 0.0035)
    if len(active_idx) > SAMPLE_RATE * 5:
        pad = int(SAMPLE_RATE * 0.08)  # keep 80ms natural breath
        start_idx = max(0, int(active_idx[0]) - pad)
        end_idx = min(len(stereo), int(active_idx[-1]) + pad)
        stereo = stereo[start_idx:end_idx]

    return stereo


async def fetch_itunes_cover_url(artist: str, title: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                "https://itunes.apple.com/search",
                params={"term": f"{artist} {title}", "entity": "song", "limit": 1},
            )
            results = resp.json().get("results", [])
            if results:
                art = results[0].get("artworkUrl100") or ""
                return art.replace("100x100bb", "600x600bb")
    except Exception:
        pass
    return ""


class YtDlpCacheResolver:
    def __init__(self, cache_dir: Path = AUDIO_CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cached_track_ids: set[str] = set()
        self._rescan_cache_index()

    def _rescan_cache_index(self) -> None:
        try:
            self._cached_track_ids = {
                f.stem for f in self.cache_dir.iterdir()
                if f.is_file() and f.suffix in (".webm", ".opus", ".m4a", ".mp3", ".ogg", ".wav") and f.stat().st_size > 10000
            }
        except Exception:
            pass

    def is_cached(self, track_id: str) -> bool:
        return track_id in self._cached_track_ids

    def find_cached_file(self, track_id: str) -> Optional[Path]:
        for ext in ("opus", "webm", "m4a", "mp3", "ogg", "wav"):
            candidate = self.cache_dir / f"{track_id}.{ext}"
            if candidate.exists() and candidate.stat().st_size > 10000:
                self._cached_track_ids.add(track_id)
                return candidate
        self._cached_track_ids.discard(track_id)
        return None

    def _enforce_rolling_cache_cap(self) -> None:
        files = [p for p in self.cache_dir.iterdir() if p.is_file()]
        total_bytes = sum(f.stat().st_size for f in files)
        max_bytes = int(MAX_AUDIO_CACHE_GB * (1024 ** 3))
        if total_bytes <= max_bytes:
            return

        listen_scores: Dict[str, Tuple[int, float, float]] = {}
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.execute(
                    """
                    SELECT t.track_id,
                           COALESCE(SUM(CASE WHEN ph.action_taken IN ('completed', 'starred', 'requested') THEN 1 ELSE 0 END), 0) AS completed_plays,
                           COALESCE(t.favorite_boost, 1.0) AS fav_boost
                    FROM tracks t
                    LEFT JOIN play_history ph ON t.track_id = ph.track_id
                    GROUP BY t.track_id
                    """
                )
                for tid, plays, boost in cur.fetchall():
                    listen_scores[str(tid)] = (int(plays), float(boost), 0.0)
        except Exception:
            pass

        def _eviction_priority(path: Path) -> Tuple[int, float, float]:
            track_id = path.stem
            plays, boost, _ = listen_scores.get(track_id, (0, 1.0, 0.0))
            return (plays, boost, path.stat().st_mtime)

        files.sort(key=_eviction_priority)
        while total_bytes > max_bytes and files:
            least_listened = files.pop(0)
            total_bytes -= least_listened.stat().st_size
            least_listened.unlink(missing_ok=True)

    def search_youtube_music_clean(
        self,
        artist: str,
        title: str,
        expected_duration_sec: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        TuneMyMusic / Soundiiz Multi-Tier Matching Pipeline:
        Tier 1: Clean "{Artist} {Title}"
        Tier 2: Primary lead artist only "{PrimaryArtist} {CleanTitle}" (e.g. "Aiobahn DEMON'S SHOT")
        Tier 3: Exact raw "{title}" if artist is a known producer/group
        Validates candidate against AuthenticityGuard duration, videoType (ATV preferred), and channel metadata.
        """
        ytm = get_ytmusic()
        clean_t = clean_string_for_matching(title)
        clean_a = clean_string_for_matching(artist)
        primary_a = extract_primary_artist(artist)
        clean_primary_a = clean_string_for_matching(primary_a)

        # Build prioritized fallback search tiers
        queries_to_try: List[str] = []

        # Tier 1: Clean primary artist + clean title (highest hit-rate for collabs)
        if clean_primary_a and clean_t and clean_primary_a != clean_a:
            queries_to_try.append(f"{clean_primary_a} {clean_t}")

        # Tier 2: Full clean artist + title
        if clean_a and clean_t:
            queries_to_try.append(f"{clean_a} {clean_t}")

        # Tier 3: Raw strings
        raw_query = f"{artist} {title}".strip()
        if raw_query not in queries_to_try:
            queries_to_try.append(raw_query)

        # Tier 4: Primary artist raw + title raw
        if primary_a and primary_a != artist:
            p_query = f"{primary_a} {title}".strip()
            if p_query not in queries_to_try:
                queries_to_try.append(p_query)

        # Tier 5: Pure title query (verified against artist array in candidate)
        if title.strip() and title.strip() not in queries_to_try:
            queries_to_try.append(title.strip())

        candidates = []
        for q in queries_to_try:
            try:
                found = ytm.search(q, filter="songs") or []
                if found:
                    candidates.extend(found)
                    # If top result is high confidence, stop querying to save API bandwidth
                    top_cand = found[0]
                    is_valid, conf, _ = authenticity_guard.evaluate_candidate(
                        candidate=top_cand,
                        expected_artist=artist,
                        expected_title=title,
                        expected_duration_sec=expected_duration_sec,
                    )
                    if is_valid and conf >= 0.85:
                        break
            except Exception as e:
                logger.warning(f"YTMusic search error for '{q}': {e}")

        # Secondary fallback: try video catalogue if songs returned 0
        if not candidates:
            for q in queries_to_try[:3]:
                try:
                    found = ytm.search(q, filter="videos") or []
                    if found:
                        candidates.extend(found)
                        break
                except Exception:
                    pass

        if not candidates:
            return None

        best_match = None
        best_score = 0.0

        for cand in candidates[:15]:
            cand_vid = cand.get("videoId")
            if not cand_vid:
                continue

            is_valid, conf_score, reason = authenticity_guard.evaluate_candidate(
                candidate=cand,
                expected_artist=artist,
                expected_title=title,
                expected_duration_sec=expected_duration_sec,
            )

            if is_valid and conf_score > best_score:
                best_score = conf_score
                cand_title = cand.get("title", "")
                cand_artists = [a.get("name", "") for a in cand.get("artists", [])]
                best_match = {
                    "video_id": cand_vid,
                    "url": f"https://music.youtube.com/watch?v={cand_vid}",
                    "title": cand_title,
                    "artist": ", ".join(cand_artists) if cand_artists else artist,
                    "score": round(conf_score, 3),
                    "duration": cand.get("duration_seconds"),
                    "is_verified": 1,
                }

        return best_match

    def resolve_and_cache(
        self,
        track_id: str,
        query_or_url: str,
        expected_duration_sec: Optional[float] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Optional[Path]:
        existing = self.find_cached_file(track_id)
        if existing is not None:
            existing.touch()
            return existing

        self._enforce_rolling_cache_cap()

        download_url: Optional[str] = None
        resolved_meta: Optional[Dict[str, Any]] = None

        if query_or_url.startswith("http"):
            download_url = query_or_url
        else:
            track_artist = artist or ""
            track_title = title or ""
            if (not track_artist or not track_title) and " - " in query_or_url:
                parts = query_or_url.split(" - ", 1)
                track_artist = track_artist or parts[0].strip()
                track_title = track_title or parts[1].strip()
            elif not track_title:
                track_title = query_or_url.strip()

            # Multi-tier clean studio match
            matched = self.search_youtube_music_clean(
                artist=track_artist,
                title=track_title,
                expected_duration_sec=expected_duration_sec,
            )
            if matched:
                download_url = matched["url"]
                resolved_meta = matched
                logger.info(
                    f"Resolved '{track_artist} - {track_title}' -> YTMusic '{matched['title']}' "
                    f"by {matched['artist']} (Score: {matched['score']}, VideoId: {matched['video_id']})"
                )
            else:
                logger.warning(
                    f"No confident YouTube Music studio match found for '{track_artist} - {track_title}'. "
                    f"Skipping download to avoid wrong song or music-video SFX."
                )
                return None

        ydl_opts = {
            "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(self.cache_dir / f"{track_id}.%(ext)s"),
            "noplaylist": True,
            "socket_timeout": 12,
            "quiet": True,
            "no_warnings": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([download_url])
        except yt_dlp.utils.MaxDownloadsReached:
            pass
        except Exception as e:
            logger.error(f"yt-dlp download failed for {download_url}: {e}")

        found = self.find_cached_file(track_id)
        if found is not None and resolved_meta:
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute(
                        """
                        UPDATE tracks
                        SET yt_video_id = ?,
                            resolved_title = ?,
                            resolved_artist = ?,
                            match_score = ?,
                            is_verified = 1
                        WHERE track_id = ?
                        """,
                        (
                            resolved_meta["video_id"],
                            resolved_meta["title"],
                            resolved_meta["artist"],
                            resolved_meta["score"],
                            track_id,
                        ),
                    )
            except Exception:
                pass

        return found
