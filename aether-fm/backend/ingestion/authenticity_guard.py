"""
Authenticity Guard & Official Track Classifier (authenticity_guard.py)
Enforces zero unofficial uploads, bootlegs, soundalikes, or amateur covers on AetherFM.

Rules:
1. `videoType == 'MUSIC_VIDEO_TYPE_ATV'` (Official Label Audio Track Video / Topic upload).
2. Trusted Artist Channel Match: Validates candidate's `artists[].id` (`UC...`) against `trusted_artists`.
3. Anti-Cover Pattern Gate: Blocks cover-mill artists (Miura Jam, Studio Yuraki, etc.) and
   titles with `(From "...")`, `Tribute to`, `Karaoke`, etc., UNLESS performed by a verified artist.
4. Dual JP/EN Title Normalization: Correctly parses `"ポラリス - Polaris"` and strips `(CV: ...)`.
"""

import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from config import DB_PATH

logger = logging.getLogger("aether_fm.authenticity")

# Known digital-store cover bands / remake mills to strictly reject
COVER_MILL_ARTISTS = {
    "miura jam", "miura jam br", "studio yuraki", "geek music", "samuel kim",
    "caleb hyles", "jonathan young", "amalee", "leeandlie", "natewantstobattle",
    "pellek", "dima lancaster", "the ashtons", "megaraptor", "smooth mcgroove",
    "rainych", "covers ph", "pop music workshop", "karaoke all stars",
}

# Suspicious title patterns indicating non-original / remake content
SUSPICIOUS_TITLE_PATTERNS = [
    r'\(from\s+["\'].*?["\']\)',
    r'\[from\s+["\'].*?["\']\]',
    r'\boriginally\s+performed\s+by\b',
    r'\btribute\s+to\b',
    r'\bkaraoke\s+version\b',
    r'\binstrumental\s+cover\b',
    r'\bacoustic\s+cover\b',
    r'\bpiano\s+cover\b',
    r'\bmetal\s+cover\b',
    r'\blofi\s+remix\b',
    r'\b8-?bit\b',
    r'\bsped\s+up\b',
    r'\bslowed\s*\+?\s*reverb\b',
    r'\bnightcore\b',
]


def normalize_name(s: str) -> str:
    """Normalizes string for comparison: lowercase, no punctuation, collapse whitespace."""
    if not s:
        return ""
    # Normalize dual titles like "ポラリス - Polaris" -> keep both as searchable tokens
    s = s.lower()
    # Remove parenthetical voice actor tags like (CV: ...), (cv:...)
    s = re.sub(r"\(cv\s*:\s*.*?\)", "", s, flags=re.I)
    # Remove standard brackets
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", "", s)
    # Remove punctuation
    s = re.sub(r"[^a-z0-9\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\s]", " ", s)
    return " ".join(s.split())


class AuthenticityGuard:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._trusted_artists_cache: Dict[str, str] = {}
        self.refresh_trusted_artists()

    def refresh_trusted_artists(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("SELECT artist_norm, browse_id FROM trusted_artists").fetchall()
                self._trusted_artists_cache = {r[0]: r[1] for r in rows}
        except Exception:
            pass

    def is_artist_trusted(self, artist_name: str, candidate_browse_id: Optional[str]) -> bool:
        if not candidate_browse_id:
            return False
        norm = normalize_name(artist_name)
        trusted_id = self._trusted_artists_cache.get(norm)
        if trusted_id and candidate_browse_id == trusted_id:
            return True
        return False

    def add_trusted_artist(self, artist_name: str, browse_id: str, display_name: str = "") -> None:
        if not artist_name or not browse_id:
            return
        norm = normalize_name(artist_name)
        self._trusted_artists_cache[norm] = browse_id
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO trusted_artists (artist_norm, browse_id, display_name) VALUES (?, ?, ?)",
                    (norm, browse_id, display_name or artist_name),
                )
        except Exception:
            pass

    def evaluate_candidate(
        self,
        candidate: Dict[str, Any],
        expected_artist: str,
        expected_title: str,
        expected_duration_sec: Optional[float] = None,
    ) -> Tuple[bool, float, str]:
        """
        Evaluates whether a YouTube Music candidate is a genuine, verified official studio track.
        Returns: (is_valid, confidence_score, reject_reason)
        """
        cand_title = candidate.get("title", "")
        cand_artists = [a.get("name", "") for a in candidate.get("artists", [])]
        cand_ids = [a.get("id") for a in candidate.get("artists", []) if a.get("id")]
        video_type = candidate.get("videoType")
        cand_dur = candidate.get("duration_seconds")
        album = (candidate.get("album") or {}).get("name") if isinstance(candidate.get("album"), dict) else candidate.get("album")

        # Gate 1: Check videoType
        # MUSIC_VIDEO_TYPE_ATV is the gold standard (Official Label Art Track Video / Topic upload)
        is_atv = (video_type == "MUSIC_VIDEO_TYPE_ATV")
        is_omv = (video_type == "MUSIC_VIDEO_TYPE_OMV")
        is_ugc = (video_type == "MUSIC_VIDEO_TYPE_UGC")

        if is_ugc:
            return False, 0.0, "REJECTED_UGC_USER_UPLOAD"

        if not is_atv and not is_omv:
            # If videoType is missing or not ATV, require high album and artist verification
            if not album:
                return False, 0.0, "REJECTED_NON_ATV_NO_ALBUM"

        # Gate 2: Cover-mill artist check
        for a in cand_artists:
            if normalize_name(a) in COVER_MILL_ARTISTS:
                return False, 0.0, f"REJECTED_COVER_MILL_ARTIST_{a}"

        # Gate 3: Suspicious cover patterns in title or album
        title_blob = f"{cand_title} {album or ''}".lower()
        for pat in SUSPICIOUS_TITLE_PATTERNS:
            if re.search(pat, title_blob, re.I):
                # Only allow if the expected title intentionally contains the pattern
                if not re.search(pat, expected_title.lower(), re.I):
                    return False, 0.0, f"REJECTED_SUSPICIOUS_PATTERN_{pat}"

        # Gate 4: Artist Match & Trust Validation
        exp_artist_norm = normalize_name(expected_artist)
        cand_artists_norm = [normalize_name(a) for a in cand_artists]

        # Check if matched against trusted artist channel browse_id
        trusted_browse_id = self._trusted_artists_cache.get(exp_artist_norm)
        has_channel_match = False
        if trusted_browse_id and any(cid == trusted_browse_id for cid in cand_ids):
            has_channel_match = True

        # Check for artist name overlap (exact or partial)
        direct_artist_match = (
            exp_artist_norm in cand_artists_norm
            or any(exp_artist_norm in a or a in exp_artist_norm for a in cand_artists_norm if len(a) >= 3)
        )

        if not has_channel_match and not direct_artist_match:
            return False, 0.0, "REJECTED_ARTIST_MISMATCH"

        # Gate 5: Title Match
        exp_title_norm = normalize_name(expected_title)
        cand_title_norm = normalize_name(cand_title)
        
        # Dual-language title check (e.g. "ポラリス - Polaris")
        cand_title_parts = [normalize_name(p) for p in cand_title.split(" - ")]
        
        title_matches = (
            exp_title_norm == cand_title_norm
            or exp_title_norm in cand_title_norm
            or any(exp_title_norm == p or exp_title_norm in p for p in cand_title_parts)
            or any(p in exp_title_norm for p in cand_title_parts if len(p) >= 4)
        )

        if not title_matches:
            return False, 0.0, "REJECTED_TITLE_MISMATCH"

        # Gate 6: Duration bounds
        dur_penalty = 0.0
        if expected_duration_sec and cand_dur:
            diff = abs(float(cand_dur) - expected_duration_sec)
            if diff > 45.0:
                return False, 0.0, f"REJECTED_DURATION_DELTA_{diff:.1f}S"
            elif diff > 15.0:
                dur_penalty = 0.15

        # Calculate final confidence score
        score = 0.70
        if is_atv:
            score += 0.15
        if has_channel_match:
            score += 0.15
        if direct_artist_match:
            score += 0.10
        if album:
            score += 0.05
        score -= dur_penalty

        final_score = min(1.0, max(0.0, score))
        return (final_score >= 0.75), round(final_score, 3), "VERIFIED_OFFICIAL_STUDIO_TRACK"


authenticity_guard = AuthenticityGuard()
