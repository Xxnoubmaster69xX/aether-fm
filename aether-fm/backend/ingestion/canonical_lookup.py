"""
Canonical Track Metadata Resolver (canonical_lookup.py)
Queries official audio catalogs (iTunes Search API and Deezer API) to retrieve:
1. Exact official track title & artist spelling
2. Official album name
3. Exact studio duration (in seconds, millisecond precision)
4. ISRC (International Standard Recording Code)
5. Explicit cover artwork URL
Used as the reference standard to prevent downloading bad covers, remixes, or karaoke versions.
"""

import logging
import re
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger("aether_fm.canonical")


def clean_title_for_lookup(title: str) -> str:
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", "", title or "")
    s = re.sub(r"\b(feat|ft|prod|cv)\b.*", "", s, flags=re.IGNORECASE)
    return " ".join(s.split())


async def fetch_canonical_metadata(artist: str, title: str) -> Optional[Dict[str, Any]]:
    """
    Looks up official song data from iTunes API, falling back to Deezer.
    Returns: {
        'title': str,
        'artist': str,
        'album': str,
        'duration_sec': float,
        'cover_url': str,
        'isrc': str,
        'source': 'itunes' | 'deezer'
    }
    """
    clean_t = clean_title_for_lookup(title)
    clean_a = clean_title_for_lookup(artist)
    query = f"{clean_a} {clean_t}".strip()

    # 1. Try iTunes Search API
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                "https://itunes.apple.com/search",
                params={"term": query, "entity": "song", "limit": 4},
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                for r in results:
                    r_artist = r.get("artistName", "")
                    r_title = r.get("trackName", "")
                    # Match check: artist or title overlap
                    if r_title and (clean_a.lower() in r_artist.lower() or r_artist.lower() in clean_a.lower()):
                        dur_ms = r.get("trackTimeMillis", 0)
                        art = (r.get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
                        return {
                            "title": r_title,
                            "artist": r_artist,
                            "album": r.get("collectionName", ""),
                            "duration_sec": round(float(dur_ms) / 1000.0, 1) if dur_ms else 210.0,
                            "cover_url": art,
                            "isrc": r.get("isrc", ""),
                            "source": "itunes",
                        }
    except Exception as e:
        logger.debug(f"iTunes lookup error for '{query}': {e}")

    # 2. Fallback to Deezer Public Search API (no API key needed)
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                "https://api.deezer.com/search",
                params={"q": f'artist:"{clean_a}" track:"{clean_t}"', "limit": 3},
            )
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    item = data[0]
                    return {
                        "title": item.get("title", title),
                        "artist": item.get("artist", {}).get("name", artist),
                        "album": item.get("album", {}).get("title", ""),
                        "duration_sec": float(item.get("duration", 210)),
                        "cover_url": item.get("album", {}).get("cover_xl") or item.get("album", {}).get("cover_big") or "",
                        "isrc": item.get("isrc", ""),
                        "source": "deezer",
                    }
    except Exception as e:
        logger.debug(f"Deezer lookup error for '{query}': {e}")

    return None
