"""
Primary High-Res Pipeline (tidalapi):
Authenticates with your Tidal subscription via OAuth device login and resolves
direct unencrypted FLAC / Hi-Res AAC stream URLs.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import tidalapi

from config import BASE_DIR

SESSION_FILE = BASE_DIR / ".cache" / "tidal_session.json"


class TidalIngestor:
    def __init__(self):
        self.session = tidalapi.Session()

    def authenticate_or_restore(self) -> bool:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        if SESSION_FILE.exists():
            try:
                self.session.load_session_from_file(SESSION_FILE)
                if self.session.check_login():
                    return True
            except Exception:
                pass

        # Interactive OAuth device login on Windows 10 setup
        self.session.login_oauth_simple()
        if self.session.check_login():
            self.session.save_session_to_file(SESSION_FILE)
            return True
        return False

    def get_stream_url(self, tidal_track_id: str) -> Optional[str]:
        try:
            track = self.session.track(int(tidal_track_id))
            return track.get_url()
        except Exception:
            return None

    def fetch_user_favorites(self) -> List[Dict[str, Any]]:
        if not self.session.check_login():
            return []
        fav_tracks = self.session.user.favorites.tracks()
        results = []
        for t in fav_tracks:
            results.append(
                {
                    "track_id": f"tidal_{t.id}",
                    "title": t.name,
                    "artist": t.artist.name if t.artist else "Unknown",
                    "album": t.album.name if t.album else "",
                    "source_type": "tidal",
                    "source_id": str(t.id),
                    "cover_url": t.album.image(640) if t.album else "",
                    "duration_sec": float(t.duration or 210.0),
                    "bpm": 120.0,
                }
            )
        return results
