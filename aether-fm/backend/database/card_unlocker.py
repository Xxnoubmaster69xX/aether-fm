"""
Procedural Mood Card Discovery Job (card_unlocker.py)
Runs when a listening session ends. Identifies clusters of high-completion tracks
(completion_ratio > 0.85 over the last 14 days) and prompts Madam Herta (Ollama)
to mint a brand-new Unlockable Mood Card.
"""

import json
import uuid
from typing import Any, Dict, Optional
import aiosqlite
import httpx

from config import DB_PATH, OLLAMA_BASE_URL, OLLAMA_MODEL
from database.db_manager import get_hour_bucket


async def check_and_unlock_mood_card() -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT ph.hour_of_day,
                   t.genres,
                   AVG(t.bpm) AS avg_bpm,
                   AVG(t.energy_score) AS avg_energy,
                   COUNT(*) AS completed_count
            FROM play_history ph
            JOIN tracks t ON ph.track_id = t.track_id
            WHERE ph.completion_ratio >= 0.85
              AND ph.played_at >= datetime('now', '-14 days')
            GROUP BY ph.hour_of_day, t.genres
            HAVING completed_count >= 15
            ORDER BY completed_count DESC
            LIMIT 1
        """
        async with db.execute(query) as cur:
            cluster = await cur.fetchone()

        if not cluster:
            return None

        genres_list = json.loads(cluster["genres"] or "[]")
        hour_bucket = get_hour_bucket(cluster["hour_of_day"])
        avg_bpm = round(float(cluster["avg_bpm"]), 1)
        avg_energy = round(float(cluster["avg_energy"]), 2)

        # Check if a similar card already exists
        async with db.execute(
            "SELECT card_id FROM mood_cards WHERE target_genres = ?",
            (json.dumps(genres_list),),
        ) as cur:
            if await cur.fetchone():
                return None

        prompt = f"""
You are Madam Herta (The Herta, Genius Society #83 from Honkai: Star Rail).
The user has completed {cluster['completed_count']} tracks in the '{hour_bucket}' window
featuring genres {genres_list} with average BPM {avg_bpm} and average energy {avg_energy}.

Generate a new procedural radio Mood Card JSON object with this exact schema:
{{
  "title": "Creative Herta/Curio/Cosmic-themed station title (max 4 words)",
  "subtitle": "{int(avg_bpm - 15)}-{int(avg_bpm + 15)} BPM • Short vibe description",
  "color_hex_primary": "#HEXCODE",
  "color_hex_secondary": "#HEXCODE",
  "icon_name": "auto_awesome",
  "crossfade_style": "beat_blend_8s",
  "dj_persona_prompt": "1-sentence instruction for how Madam Herta should act when this card is active"
}}
Respond ONLY with valid JSON.
"""
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                },
            )
            data = json.loads(resp.json()["response"])

        card_id = f"card_{uuid.uuid4().hex[:8]}"
        new_card = {
            "card_id": card_id,
            "title": data.get("title", "Curio: Uncharted Frequency"),
            "subtitle": data.get("subtitle", f"{int(avg_bpm)} BPM • Custom Cluster"),
            "color_hex_primary": data.get("color_hex_primary", "#9D4EDD"),
            "color_hex_secondary": data.get("color_hex_secondary", "#240046"),
            "icon_name": data.get("icon_name", "auto_awesome"),
            "target_genres": genres_list,
            "min_bpm": max(60.0, avg_bpm - 20.0),
            "max_bpm": min(200.0, avg_bpm + 20.0),
            "min_energy": max(0.0, avg_energy - 0.25),
            "max_energy": min(1.0, avg_energy + 0.25),
            "crossfade_style": data.get("crossfade_style", "beat_blend_8s"),
            "dj_persona_prompt": data.get(
                "dj_persona_prompt",
                "Speak with amused Genius Society superiority about this newly discovered pattern.",
            ),
        }

        await db.execute(
            """
            INSERT INTO mood_cards (
                card_id, title, subtitle, color_hex_primary, color_hex_secondary,
                icon_name, target_genres, min_bpm, max_bpm, min_energy, max_energy,
                crossfade_style, dj_persona_prompt, is_pinned_to_car_wheel
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                new_card["card_id"],
                new_card["title"],
                new_card["subtitle"],
                new_card["color_hex_primary"],
                new_card["color_hex_secondary"],
                new_card["icon_name"],
                json.dumps(new_card["target_genres"]),
                new_card["min_bpm"],
                new_card["max_bpm"],
                new_card["min_energy"],
                new_card["max_energy"],
                new_card["crossfade_style"],
                new_card["dj_persona_prompt"],
            ),
        )
        await db.commit()
        return new_card
