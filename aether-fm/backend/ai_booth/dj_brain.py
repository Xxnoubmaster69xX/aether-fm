"""
FM AI Broadcast Director, Artist Knowledge Graph & Related Discovery Engine (dj_brain.py)
Features:
1. Deep Artist Knowledge & Cross-Artist Relationship Graph: connects artists to related acts,
   shared producers, sonic DNA, and album context so commentary never repeats generic phrases.
2. Automatic Related-Artist Discovery (`discover_related_track`): expands your station beyond
   the static Music.txt file by discovering studio tracks from related artists (Madeon, Vaundy,
   Ado, ZUTOMAYO, Kenshi Yonezu, Kygo, Snail's House, ODESZA, King Gnu, Zach Bryan, etc.)
   and announcing how they connect to the artists you already listen to.
3. 30-item rolling fact/template deduplication memory so comments never repeat.
"""

from datetime import datetime
import asyncio
import hashlib
import json
import os
from pathlib import Path
import random
import re
import subprocess
import time
from typing import Any, Dict, List, Optional
import httpx

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_KEEP_ALIVE_ACTIVE,
    OLLAMA_KEEP_ALIVE_SLEEP,
    OLLAMA_MODEL,
    OLLAMA_NUM_CTX,
)
from ai_booth.persona_marketplace import PersonaMarketplaceManager
from database.db_manager import compute_track_mood, normalize_primary_artist

# Rich Artist Knowledge Graph: maps normalized artist -> related artists + specific non-repeating musical facts
ARTIST_KNOWLEDGE_GRAPH: Dict[str, Dict[str, Any]] = {
    "porter robinson": {
        "related": ["Madeon", "San Holo", "ODESZA", "Daft Punk", "Illenium"],
        "facts": [
            "bridging emotional synth-pop and intricate electronic production, much like his longtime collaborator Madeon",
            "blending nostalgic bit-crushed textures with indie songwriting across his Nurture and SMILE eras",
            "carrying that signature bittersweet electronic melodicism that connects straight to Madeon and San Holo",
            "layering warm analog vocals over bright digital synth arpeggios",
        ],
    },
    "avicii": {
        "related": ["Kygo", "Swedish House Mafia", "Alesso", "Calvin Harris", "Zedd"],
        "facts": [
            "fusing acoustic folk guitar melodies with euphoric progressive house drops alongside peers like Kygo and Alesso",
            "built on the unmistakable piano-driven progressive house hooks that defined an entire era of melodic electronic music",
            "pairing soulful vocalists like Aloe Blacc and Bonn with uplifting Scandinavian synth leads",
            "blending acoustic warmth into high-energy festival production, a blueprint shared with Swedish House Mafia and Kygo",
        ],
    },
    "natori": {
        "related": ["Vaundy", "imase", "Tatsuya Kitani", "EVE", "ZUTOMAYO"],
        "facts": [
            "driven by slick, late-night Tokyo basslines and jazz-inflected bedroom pop that fans of Vaundy and EVE immediately recognize",
            "blending nocturnal brass accents and tight funk rhythms in the same wave as imase and ZUTOMAYO",
            "combining low-register smoky vocals with infectious city-pop bass grooves",
            "sitting right at the intersection of modern Japanese indie-funk and dark electronic pop alongside Tatsuya Kitani",
        ],
    },
    "hoshimachi suisei": {
        "related": ["YOASOBI", "Ado", "Midnight Grand Orchestra", "Kenshi Yonezu", "Reol"],
        "facts": [
            "showcasing powerhouse vocal control over fast-paced J-pop and disco-rock production, right in league with Ado and YOASOBI",
            "connected directly to Midnight Grand Orchestra, her cinematic space-opera project with producer Taku Inoue",
            "pairing razor-sharp high-tempo vocals with brassy jazz-rock and modern Tokyo club instrumentation",
            "delivering high-velocity vocal precision that bridges modern J-rock and electronic pop",
        ],
    },
    "midnight grand orchestra": {
        "related": ["Hoshimachi Suisei", "YOASOBI", "SawanoHiroyuki[nZk]", "Aimer", "m-flo"],
        "facts": [
            "the cyber-symphonic duo pairing Hoshimachi Suisei's vocals with veteran composer Taku Inoue's orchestral club beats",
            "fusing sweeping cinematic strings with driving electro-house percussion",
            "bridging Hoshimachi Suisei's vocal range with space-opera synth production reminiscent of m-flo and Porter Robinson",
        ],
    },
    "moe shop": {
        "related": ["Snail's House", "Daft Punk", "Madeon", "Capsule", "Macross 82-99"],
        "facts": [
            "blending French-touch house compression inspired by Daft Punk and Justice with bright Japanese electro-pop vocals",
            "packing punchy sidechained basslines and future-funk groove that connects directly to Snail's House and Madeon",
            "fusing Parisian filter-house sampling with Tokyo club vocal features",
        ],
    },
    "yoasobi": {
        "related": ["Ado", "Kenshi Yonezu", "ZUTOMAYO", "Official HIGE DANdism", "natori"],
        "facts": [
            "featuring Ayase's rapid-fire piano-pop key changes and Ikura's crystal-clear vocal delivery",
            "driving the same high-BPM modern Tokyo synth-pop wave as Ado, ZUTOMAYO, and Hoshimachi Suisei",
        ],
    },
    "lisa": {
        "related": ["Aimer", "King Gnu", "SawanoHiroyuki[nZk]", "Tatsuya Kitani", "EVE"],
        "facts": [
            "bringing high-octane Japanese rock guitar riffs and arena-scale vocal energy alongside acts like King Gnu and EVE",
            "combining aggressive rock drumming with soaring melodic hooks",
        ],
    },
    "eve": {
        "related": ["natori", "Vaundy", "Kenshi Yonezu", "Tatsuya Kitani", "King Gnu"],
        "facts": [
            "known for intricate guitar-driven alt-rock arrangements that connect directly to natori, Vaundy, and Kenshi Yonezu",
            "layering nimble rhythmic guitar work over dark, kinetic Tokyo indie-rock grooves",
        ],
    },
    "morgan wallen": {
        "related": ["Luke Combs", "Zach Bryan", "Post Malone", "Chris Stapleton"],
        "facts": [
            "blending warm acoustic Nashville storytelling with modern trap-inflected drums alongside Luke Combs and Post Malone",
            "shifting the spectrum into laid-back heartland acoustic melodies and crisp vocal hooks",
        ],
    },
    "madeon": {
        "related": ["Porter Robinson", "Daft Punk", "San Holo", "Moe Shop"],
        "facts": [
            "Porter Robinson's closest musical collaborator from the Shelter tour, bringing French-touch synth-pop brilliance",
            "blending Daft Punk-inspired vocoders and shimmering analog chords that pair naturally with Porter Robinson and Moe Shop",
        ],
    },
    "vaundy": {
        "related": ["natori", "EVE", "imase", "Kenshi Yonezu"],
        "facts": [
            "a natural counterpart to natori and EVE, blending Tokyo indie-funk basslines with effortless genre-hopping hooks",
            "sharing that unmistakable late-night Japanese groove DNA with natori and Kenshi Yonezu",
        ],
    },
    "ado": {
        "related": ["Hoshimachi Suisei", "YOASOBI", "Reol", "ZUTOMAYO"],
        "facts": [
            "matching Hoshimachi Suisei and YOASOBI in sheer vocal intensity over theatrical electro-swing and J-rock",
            "delivering explosive vocal dynamics that connect directly to your Hoshimachi Suisei and LiSA rotation",
        ],
    },
    "zutomayo": {
        "related": ["natori", "YOASOBI", "Vaundy", "Hoshimachi Suisei"],
        "facts": [
            "famous for slap-bass virtuosity and frantic jazz-pop arrangements that fans of natori and YOASOBI love",
            "pairing intricate bass-and-piano interplay with nocturnal Tokyo energy",
        ],
    },
    "kenshi yonezu": {
        "related": ["EVE", "natori", "Vaundy", "YOASOBI", "King Gnu"],
        "facts": [
            "one of the architects of modern Japanese alt-pop, bridging the exact sonic territory of EVE, natori, and YOASOBI",
            "blending angular rock rhythms and horn sections with unmistakable melodic hooks",
        ],
    },
    "kygo": {
        "related": ["Avicii", "Swedish House Mafia", "Alesso", "Calvin Harris"],
        "facts": [
            "carrying forward the sunlit melodic piano-house legacy pioneered by Avicii",
            "pairing warm tropical synth plucks and vocal chops in the direct lineage of Avicii's melodic anthems",
        ],
    },
    "snail's house": {
        "related": ["Moe Shop", "Porter Robinson", "Madeon"],
        "facts": [
            "pioneering the kawaii future-bass and jazzy synth-funk sound right alongside Moe Shop and Porter Robinson",
        ],
    },
    "odesza": {
        "related": ["Porter Robinson", "San Holo", "Illenium"],
        "facts": [
            "layering cinematic drumlines and euphoric vocal samples that resonate with Porter Robinson's electronic soundscapes",
        ],
    },
    "king gnu": {
        "related": ["EVE", "natori", "Kenshi Yonezu", "LiSA"],
        "facts": [
            "fusing gritty Tokyo alternative rock with jazz-funk keyboards in the same lane as EVE and Kenshi Yonezu",
        ],
    },
    "tatsuya kitani": {
        "related": ["natori", "EVE", "YOASOBI", "Hoshimachi Suisei"],
        "facts": [
            "formerly a bass player and arranger across the Tokyo rock scene, sharing dark kinetic grooves with natori and EVE",
            "delivering high-velocity anime rock hooks that pair naturally with EVE, King Gnu, and SPYAIR",
        ],
    },
    "dave rodgers": {
        "related": ["Max Coveri", "Mega Nrg Man", "Manuel", "m.o.v.e", "Fastway"],
        "facts": [
            "the Godfather of Super Eurobeat behind Deja Vu and Space Boy, connected directly to Max Coveri and Mega Nrg Man",
            "driving 155-BPM Italian synth brass riffs that defined the Initial D mountain-pass sound alongside m.o.v.e and Manuel",
            "fusing aggressive rock guitar solos with high-speed Eurobeat octave basslines",
        ],
    },
    "max coveri": {
        "related": ["Dave Rodgers", "Manuel", "Marko Polo", "Leslie Parrish", "Niko"],
        "facts": [
            "delivering the unmistakable Running in the 90s Eurobeat synth lead alongside legends like Dave Rodgers and Marko Polo",
            "pairing high-velocity Italian Eurobeat arpeggios with non-stop Initial D adrenaline",
        ],
    },
    "manuel": {
        "related": ["Dave Rodgers", "Fastway", "Go2", "ACE", "Mega Nrg Man"],
        "facts": [
            "bringing the explosive Gas Gas Gas Eurobeat vocal energy, produced right alongside Dave Rodgers and Fastway",
            "combining arena-rock vocal power with relentless 156-BPM Super Eurobeat synth brass",
        ],
    },
    "fastway": {
        "related": ["Go2", "ACE", "Manuel", "Dave Rodgers", "Mega Nrg Man"],
        "facts": [
            "the powerhouse vocalist behind both Fastway and the duo Go2 with Ace, fueling high-speed SCP Eurobeat anthems",
            "blending hyper-melodic Italian Eurobeat hooks with overdriven guitar leads",
        ],
    },
    "m.o.v.e": {
        "related": ["Dave Rodgers", "Max Coveri", "TM Network", "Every Little Thing"],
        "facts": [
            "fusing motsu's rapid-fire rap verses, Yuri's soaring vocals, and t-kimura's Eurobeat-trance production across every stage of Initial D",
            "bridging 90s J-rap, high-BPM rave synths, and touge racing energy alongside Dave Rodgers",
        ],
    },
    "denonbu": {
        "related": ["KOTONOHOUSE", "Giga", "TeddyLoid", "Srav3R", "t+pazolite"],
        "facts": [
            "Bandai Namco's futuristic Tokyo electronic club project featuring producers like KOTONOHOUSE, Giga, TeddyLoid, and Srav3R",
            "blending hyper-kinetic Akihabara future-core, Jersey club, and hard dance basslines",
        ],
    },
    "giga": {
        "related": ["TeddyLoid", "Ado", "Reol", "KIRA", "DECO*27"],
        "facts": [
            "renowned for razor-sharp vocal chops and heavy electronic drop design alongside frequent collaborator TeddyLoid and vocalist Ado",
            "packing unmistakable sub-bass bounce and rapid-fire synth stabs into modern Vocaloid and J-pop club production",
        ],
    },
    "teddyloid": {
        "related": ["Giga", "Ado", "DENONBU", "m-flo", "Reol"],
        "facts": [
            "bridging heavy electro-house and J-pop alongside Giga and Ado, with production roots stretching back to m-flo",
            "delivering massive festival bass drops and glitch-heavy Tokyo club sound design",
        ],
    },
    "srav3r": {
        "related": ["USAO", "P*Light", "t+pazolite", "Camellia", "DJ Noriken"],
        "facts": [
            "a pillar of the Japanese HARDCORE TANO*C collective alongside USAO, P*Light, and t+pazolite, bringing relentless UK hardcore euphoria",
            "pumping high-BPM rave leads and reverse-bass kicks straight from the Tokyo rhythm-game underground",
        ],
    },
    "usao": {
        "related": ["Srav3R", "Camellia", "t+pazolite", "P*Light", "Massive New Krew"],
        "facts": [
            "famous in the HARDCORE TANO*C and rhythm-game scene for earth-shattering Frenchcore kicks and dubstep screeches alongside Camellia and Srav3R",
        ],
    },
    "martin garrix": {
        "related": ["Brooks", "Mesto", "Vluarr", "Arcando", "Zedd", "Alesso"],
        "facts": [
            "anchoring the STMPD RCRDS sound alongside collaborators like Brooks, Mesto, Vluarr, and Arcando",
            "blending euphoric progressive festival leads with aggressive club basslines",
        ],
    },
    "knock2": {
        "related": ["ISOxo", "Chris Lake", "Martin Garrix", "Habstrakt", "Joyryde"],
        "facts": [
            "leading the modern bass-house surge alongside ISOxo and Chris Lake with Warehouse-shaking drums and 90s rave stabs",
        ],
    },
    "linkin park": {
        "related": ["coldrain", "ONE OK ROCK", "Bring Me The Horizon", "Avenged Sevenfold", "Skillet"],
        "facts": [
            "fusing nu-metal guitar walls with industrial electronic sampling—a blueprint that directly inspired coldrain and ONE OK ROCK",
            "pairing Mike Shinoda's rhythmic verses and electronics with unmistakable alternative-metal hooks",
        ],
    },
    "coldrain": {
        "related": ["ONE OK ROCK", "SiM", "Crossfaith", "LINKIN PARK", "Survive Said The Prophet"],
        "facts": [
            "Nagoya's post-hardcore powerhouse led by Masato, bridging the heavy melodic intensity of LINKIN PARK and ONE OK ROCK",
        ],
    },
    "one ok rock": {
        "related": ["coldrain", "LINKIN PARK", "MY FIRST STORY", "SPYAIR"],
        "facts": [
            "blending Taka's soaring rock vocals with arena-scale post-hardcore and alternative rock alongside peers like coldrain and SPYAIR",
        ],
    },
    "casey edwards": {
        "related": ["Victor Borba", "SEGA SOUND TEAM", "Jamie Christopherson", "Mick Gordon"],
        "facts": [
            "architect of Devil May Cry 5's Bury the Light with vocalist Victor Borba, matching the boss-metal intensity of Sonic Frontiers",
        ],
    },
    "sega sound team": {
        "related": ["Tomoya Ohtani", "Kellin Quinn", "Casey Edwards", "Crush 40"],
        "facts": [
            "directed by Tomoya Ohtani with Sleeping With Sirens vocalist Kellin Quinn on the Sonic Frontiers metalcore boss anthems",
        ],
    },
    "hoyo-mix": {
        "related": ["Wuthering Waves", "Sān-Z", "SawanoHiroyuki[nZk]", "Masayoshi Soken"],
        "facts": [
            "blending full orchestral symphony with electronic rock and boss-theme intensity alongside Sān-Z and Wuthering Waves",
        ],
    },
    "wuthering waves": {
        "related": ["HOYO-MiX", "Sān-Z", "Vanguard Sound", "Hiroyuki Sawano"],
        "facts": [
            "delivering dark futuristic electronic post-rock and vocal anthems that pair directly with HOYO-MiX and Hiroyuki Sawano",
        ],
    },
    "masayoshi soken": {
        "related": ["Nobuo Uematsu", "HOYO-MiX", "Hiroyuki Sawano", "THE PRIMALS"],
        "facts": [
            "Final Fantasy composer known for fusing orchestral leitmotifs from Nobuo Uematsu with hard-rock and industrial synth-metal",
        ],
    },
    "spyair": {
        "related": ["KANA-BOON", "BURNOUT SYNDROMES", "Asian Kung-Fu Generation", "THE ORAL CIGARETTES"],
        "facts": [
            "delivering driving Japanese alternative rock anthems alongside KANA-BOON and Asian Kung-Fu Generation",
        ],
    },
    "creepy nuts": {
        "related": ["natori", "YOASOBI", "Kenshi Yonezu", "Ado"],
        "facts": [
            "pairing R-Shitei's rapid-fire triple-time lyricism with DJ Matsunaga's world-champion turntablism and Jersey-club bounce",
        ],
    },
}


def _clean_title_for_speech(raw: str) -> str:
    cleaned = re.sub(r"\s*\[.*?\]", "", raw or "")
    cleaned = re.sub(
        r"\s*\((?:CV|Prod|feat|ft|Official|Anime|Game|MV|Video|Audio|Size|Ver).*?\)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.replace("*", " ").strip()
    return cleaned or raw


def _strip_deepseek_thinking(raw_text: str) -> str:
    """
    Removes DeepSeek-R1's internal <think>...</think> chain-of-thought reasoning blocks
    and cleans up quotes/markdown so only the natural spoken character line goes to TTS.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text or "", flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.DOTALL)
    cleaned = cleaned.replace("*", "").replace('"', "").strip()
    # Keep at most 2 concise sentences so transitions stay tight
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    if len(sentences) > 2:
        cleaned = " ".join(sentences[:2])
    return cleaned.strip()


class HertaDJBrain:
    def __init__(self):
        self.base_url = OLLAMA_BASE_URL
        self.model = "deepseek-r1:1.5b"
        self.llm_mode = "hybrid_deepseek"  # Multi-AI Ensemble: Graph + Persona + DeepSeek/Qwen + Kokoro CUDA + Whisper
        self.llm_status = "MULTI-AI ENSEMBLE // 5 AGENTS ACTIVE"
        self.dj_enabled = True
        self.persona_manager = PersonaMarketplaceManager()
        self._ollama_online: bool = False
        self._installed_models: List[str] = []
        self._last_ollama_check: float = 0.0
        self._recent_spoken_facts: List[str] = []
        self._recent_openers: List[str] = []
        self._discovery_counter: int = 0

    async def _is_ollama_available(self, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and (now - self._last_ollama_check < 30.0):
            return self._ollama_online
        self._last_ollama_check = now
        try:
            async with httpx.AsyncClient(timeout=0.45) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                if r.status_code == 200:
                    data = r.json()
                    self._installed_models = [
                        m.get("name", "") for m in data.get("models", []) if m.get("name")
                    ]
                    self._ollama_online = True
                    return True
        except Exception:
            pass
        self._ollama_online = False
        return False

    def get_ai_engine_state(self) -> Dict[str, Any]:
        active_char = self.persona_manager.get_character()
        return {
            "active_character_id": self.persona_manager.active_character_id,
            "active_character": active_char,
            "characters": self.persona_manager.get_all_characters(),
            "llm_mode": self.llm_mode,
            "llm_model": self.model,
            "llm_status": self.llm_status,
            "ollama_online": self._ollama_online,
            "installed_models": self._installed_models,
            "recommended_models": [
                {
                    "id": "deepseek-r1:1.5b",
                    "label": "DEEPSEEK-R1 1.5B (DISTILLED // ~1.1 GB VRAM)",
                    "vram": "1.1 GB",
                },
                {
                    "id": "deepseek-r1:7b",
                    "label": "DEEPSEEK-R1 7B (DISTILLED // ~4.7 GB VRAM)",
                    "vram": "4.7 GB",
                },
                {
                    "id": "qwen2.5:7b-instruct-q4_K_M",
                    "label": "QWEN 2.5 7B INSTRUCT (INSTALLED // ~4.5 GB VRAM)",
                    "vram": "4.5 GB",
                },
            ],
        }

    async def configure_ai_engine(
        self, llm_mode: Optional[str] = None, llm_model: Optional[str] = None
    ) -> Dict[str, Any]:
        if llm_mode in ("instant_persona", "hybrid_deepseek"):
            self.llm_mode = llm_mode
        if llm_model:
            self.model = llm_model.strip()

        if self.llm_mode == "instant_persona":
            self.llm_status = "INSTANT PERSONA ENGINE (0 MB VRAM)"
            await self.unload_vram_for_sleep()
        else:
            asyncio.create_task(self.ensure_local_model_ready(self.model))
        return self.get_ai_engine_state()

    async def ensure_local_model_ready(self, model_name: str) -> None:
        """
        Starts the local Ollama daemon if not running, checks if `model_name`
        (e.g. `deepseek-r1:1.5b`) is downloaded, pulls it in the background if needed,
        and warms it up.
        """
        self.llm_status = f"CHECKING LOCAL RUNTIME ({model_name})..."
        online = await self._is_ollama_available(force=True)
        if not online:
            ollama_exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
            if ollama_exe.exists():
                self.llm_status = "STARTING LOCAL OLLAMA DAEMON..."
                try:
                    subprocess.Popen(
                        [str(ollama_exe), "serve"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    await asyncio.sleep(2.5)
                    online = await self._is_ollama_available(force=True)
                except Exception:
                    pass

        if not online:
            self.llm_status = "INSTANT PERSONA ENGINE ACTIVE (LOCAL DAEMON OFFLINE)"
            return

        if model_name not in self._installed_models:
            self.llm_status = f"DOWNLOADING {model_name.upper()} IN BACKGROUND..."
            try:
                async with httpx.AsyncClient(timeout=600.0) as client:
                    resp = await client.post(
                        f"{self.base_url}/api/pull",
                        json={"name": model_name, "stream": False},
                    )
                    if resp.status_code == 200:
                        await self._is_ollama_available(force=True)
            except Exception:
                self.llm_status = "INSTANT PERSONA ENGINE (MODEL PULL SKIPPED)"
                return

        self.llm_status = f"ACTIVE // {model_name.upper()}"
        await self.warmup_model()

    async def warmup_model(self) -> None:
        if not await self._is_ollama_available():
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": "ok",
                        "stream": False,
                        "keep_alive": OLLAMA_KEEP_ALIVE_ACTIVE,
                        "options": {"num_ctx": OLLAMA_NUM_CTX, "num_predict": 1},
                    },
                )
        except Exception:
            pass

    async def discover_related_track(
        self,
        seed_artist: str,
        exclude_ids: List[str],
        exclude_artists: List[str],
    ) -> Optional[Dict[str, Any]]:
        """
        Automatically discovers a studio song from a RELATED artist via the public iTunes API,
        attaching `related_to_artist` so the equipped character explains why it was picked!
        """
        norm_seed = normalize_primary_artist(seed_artist)
        excluded_artists_norm = {normalize_primary_artist(a) for a in exclude_artists if a}

        info = ARTIST_KNOWLEDGE_GRAPH.get(norm_seed)
        related_pool = list(info["related"]) if info and info.get("related") else [
            "Madeon",
            "Vaundy",
            "Ado",
            "ZUTOMAYO",
            "Kenshi Yonezu",
            "Kygo",
            "Snail's House",
            "ODESZA",
            "King Gnu",
            "Tatsuya Kitani",
        ]
        random.shuffle(related_pool)

        target_artist = next(
            (a for a in related_pool if normalize_primary_artist(a) not in excluded_artists_norm),
            related_pool[0],
        )

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://itunes.apple.com/search",
                    params={
                        "term": target_artist,
                        "entity": "song",
                        "limit": 12,
                    },
                )
                results = resp.json().get("results", [])
                valid = [
                    r
                    for r in results
                    if normalize_primary_artist(r.get("artistName", ""))
                    == normalize_primary_artist(target_artist)
                    and r.get("trackName")
                ]
                if not valid:
                    valid = [r for r in results if r.get("trackName")]
                if not valid:
                    return None

                random.shuffle(valid)
                for picked in valid:
                    title = picked.get("trackName", "").strip()
                    artist = picked.get("artistName", target_artist).strip()
                    album = picked.get("collectionName", "Discovery Single")

                    # Authenticity check: Reject covers, tributes, karaoke, lofi, or tv cuts in AI discovery
                    blob = f"{title} {album}".lower()
                    if any(bad in blob for bad in ["cover", "tribute", "karaoke", "lofi", "8-bit", "tv size", "piano ver"]):
                        continue

                    digest = hashlib.md5(f"{artist}_{title}".lower().encode("utf-8")).hexdigest()[:12]
                    track_id = f"disc_{digest}"
                    if track_id in exclude_ids:
                        continue

                    duration_sec = round(float(picked.get("trackTimeMillis", 205000)) / 1000.0, 1)
                    if duration_sec < 90.0 or duration_sec > 420.0:
                        continue

                    album = picked.get("collectionName", "Discovery Single")
                    cover = (picked.get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
                    genre_raw = (picked.get("primaryGenreName") or "electronic").lower()

                    track_dict: Dict[str, Any] = {
                        "track_id": track_id,
                        "title": title,
                        "artist": artist,
                        "album": album,
                        "source_type": "youtube",
                        "source_id": f"{artist} - {title}",
                        "cover_url": cover,
                        "duration_sec": duration_sec,
                        "bpm": 126.0,
                        "energy_score": 0.72,
                        "genres": [genre_raw, "electronic", "pop"],
                        "outro_start_sec": max(15.0, duration_sec - 12.0),
                        "favorite_boost": 1.05,
                        "related_to_artist": seed_artist,
                        "is_discovery": True,
                    }
                    track_dict["mood"] = compute_track_mood(track_dict)
                    return track_dict
        except Exception:
            pass
        return None

    def _build_rich_song_introduction(
        self,
        winner: Dict[str, Any],
        recent_tracks: List[str],
        active_card: Dict[str, Any],
    ) -> str:
        """
        Generates a natural, non-repetitive introduction voiced in the active character's
        distinctive persona (The Herta, Yae Miko, Kafka, Silver Wolf, Furina, Raiden Shogun,
        2B, Lucy, Zhongli, or Custom) while weaving in the song's Mood and Cross-Artist Facts!
        """
        character = self.persona_manager.get_character()
        title = _clean_title_for_speech(winner.get("title", "Unknown Track"))
        artist = winner.get("artist", "Unknown Artist")
        norm_artist = normalize_primary_artist(artist)
        album = (winner.get("album") or "").strip()
        mood = winner.get("mood") or compute_track_mood(winner)
        mood_lower = mood.lower()
        related_seed = winner.get("related_to_artist") or ""

        prev_artist = "the last track"
        if recent_tracks:
            last_entry = recent_tracks[-1]
            if " - " in last_entry:
                prev_artist = last_entry.split(" - ", 1)[0].strip()

        # Decide whether to drop artist trivia/data OCCASIONALLY (~33% of songs or on new discoveries)
        # vs. keeping a smooth, natural 6-11 second conversational vibe flow most of the time!
        self._discovery_counter += 1
        include_artist_data = bool(
            winner.get("is_discovery")
            or related_seed
            or (self._discovery_counter % 3 == 0)
        )

        artist_entry = ARTIST_KNOWLEDGE_GRAPH.get(norm_artist, {})
        facts_pool = artist_entry.get("facts", [])
        related_list = artist_entry.get("related", [])

        vibe_phrases = [
            f"keeping this {mood_lower} momentum rolling cleanly through the session",
            f"locking the station right into a {mood_lower} groove",
            f"letting that {mood_lower} energy carry the broadcast forward",
            f"turning up a crisp {mood_lower} cut for the next few minutes",
            f"riding this {mood_lower} wave without missing a beat",
            f"holding a steady {mood_lower} cadence across the frequency",
            f"letting the bassline breathe inside this {mood_lower} pocket",
            f"maintaining pristine dynamic range with this {mood_lower} transition",
            f"giving you an uncompressed taste of genuine {mood_lower} sound",
            f"tracking nicely through this {mood_lower} frequency block",
            f"keeping the master bus warm with high-fidelity {mood_lower} resonance",
            f"letting this {mood_lower} selection speak for itself on the monitors",
            f"gliding into pure {mood_lower} territory without looking back",
            f"delivering an immaculate studio mix tuned for late-night fidelity",
            f"letting the stereo spread settle into a crisp {mood_lower} atmosphere",
        ]

        chosen_fact = ""
        if include_artist_data:
            unspoken_facts = [f for f in facts_pool if f not in self._recent_spoken_facts]
            if unspoken_facts:
                chosen_fact = random.choice(unspoken_facts)
                self._recent_spoken_facts.append(chosen_fact)
                if len(self._recent_spoken_facts) > 25:
                    self._recent_spoken_facts.pop(0)
            elif len(related_list) >= 2:
                chosen_fact = f"sharing that {mood_lower} lineage with {related_list[0]} and {related_list[1]}"

        if not chosen_fact:
            chosen_fact = random.choice(vibe_phrases)

        # Case 1: Related Artist Discovery track
        if winner.get("is_discovery") or related_seed:
            seed_label = related_seed or prev_artist
            disc_templates = character.get("discovery_openers") or [
                "Connected to {seed_artist}, here is {artist} with {title}—{fact}."
            ]
            tpl = random.choice(disc_templates)
            line = tpl.format(
                seed_artist=seed_label,
                artist=artist,
                title=title,
                mood_lower=mood_lower,
                fact=chosen_fact,
            )
        else:
            # Case 2: Standard track in the active character's voice & cadence
            openers = character.get("openers") or [
                "Next on the deck is {artist} with {title}—{fact}."
            ]
            # Anti-repeat memory: remember up to len(openers) - 1 so all openers cycle before repetition
            max_opener_memory = max(1, len(openers) - 1)
            avail_openers = [o for o in openers if o not in self._recent_openers] or list(openers)
            tpl = random.choice(avail_openers)
            self._recent_openers.append(tpl)
            if len(self._recent_openers) > max_opener_memory:
                self._recent_openers.pop(0)

            # Announce variety: sometimes emphasize title, sometimes artist, sometimes pure vibe
            announcement_mode = random.choice(["standard", "title_first", "artist_only", "standard"])
            if announcement_mode == "title_first":
                tpl = tpl.replace("{artist} with {title}", "{title} from {artist}")
                tpl = tpl.replace("{artist} on {title}", "{title} by {artist}")
            elif announcement_mode == "artist_only":
                tpl = tpl.replace("with {title}", "")
                tpl = tpl.replace("on {title}", "")
                tpl = tpl.replace("performing {title}", "in the mix")

            line = tpl.format(
                prev_artist=prev_artist,
                artist=artist,
                title=title,
                mood_lower=mood_lower,
                fact=chosen_fact,
            )

        # Target speaking window: ~6 to 13 seconds (15 to 36 words)
        words = line.split()
        if len(words) < 14:
            closing_tags = [
                "Stay locked in as the rhythm unfolds.",
                "Let the stereo field do the talking.",
                "Pure signal on Deck A.",
                "Volume up as the groove settles in.",
                "Uninterrupted telemetry on FM.",
                "Turn the monitor gain up.",
            ]
            chosen_tag = random.choice([t for t in closing_tags if t not in self._recent_spoken_facts] or closing_tags)
            self._recent_spoken_facts.append(chosen_tag)
            line = f"{line} {chosen_tag}"
        elif len(words) > 36:
            line = " ".join(words[:35]).rstrip(",;—-") + "."
        return line

    async def _try_generate_local_deepseek_line(
        self,
        winner: Dict[str, Any],
        fallback_line: str,
    ) -> str:
        """
        If `hybrid_deepseek` mode is enabled and the local Ollama daemon has the model ready,
        generates an organic 1-2 sentence in-character radio intro using DeepSeek-R1 (1.5B/7B)
        or Qwen2.5, stripping any `<think>...</think>` blocks. Falls back in <2.2s if busy.
        """
        if self.llm_mode != "hybrid_deepseek":
            return fallback_line
        if not await self._is_ollama_available():
            return fallback_line
        if self.model not in self._installed_models:
            return fallback_line

        character = self.persona_manager.get_character()
        title = _clean_title_for_speech(winner.get("title", "Unknown Track"))
        artist = winner.get("artist", "Unknown Artist")
        mood = winner.get("mood") or compute_track_mood(winner)
        norm_artist = normalize_primary_artist(artist)
        facts = ARTIST_KNOWLEDGE_GRAPH.get(norm_artist, {}).get("facts", [])
        unspoken = [f for f in facts if f not in self._recent_spoken_facts]
        fact_hint = random.choice(unspoken) if unspoken else (random.choice(facts) if facts else f"a {mood.lower()} track")

        avoid_phrases = ", ".join(f"'{line[:30]}...'" for line in (self._recent_spoken_facts[-3:] if self._recent_spoken_facts else []))
        prompt = (
            f"{character.get('llm_persona', '')}\n"
            f"Write a fresh, natural, 1-sentence radio host introduction for the song '{title}' by {artist} "
            f"(Mood: {mood}; Musical context: {fact_hint}). "
            f"Vary your wording and do NOT repeat these recent phrases: {avoid_phrases}. "
            f"Do NOT use emojis or hashtags. Output ONLY the spoken line."
        )
        try:
            async with httpx.AsyncClient(timeout=2.4) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "keep_alive": OLLAMA_KEEP_ALIVE_ACTIVE,
                        "options": {
                            "num_ctx": 512,
                            "num_predict": 90,
                            "temperature": 0.75,
                        },
                    },
                )
                if resp.status_code == 200:
                    raw = resp.json().get("response", "")
                    cleaned = _strip_deepseek_thinking(raw)
                    if len(cleaned) >= 18:
                        return cleaned
        except Exception:
            pass
        return fallback_line

    async def select_next_track_and_script(
        self,
        candidates: List[Dict[str, Any]],
        recent_tracks: List[str],
        active_card: Dict[str, Any],
        recent_dj_lines: List[str],
        telemetry_context: Optional[Dict[str, Any]] = None,
        forced_track: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if forced_track:
            candidates = [forced_track]
        if not candidates:
            return {
                "selected_track_id": "",
                "transition_style": "beat_blend_8s",
                "emotion_tag": "aloof",
                "dj_script": "Catalog buffer is currently empty.",
            }

        winner = (
            forced_track
            if forced_track
            else random.choice(candidates[: min(6, len(candidates))])
        )

        if not self.dj_enabled:
            return {
                "selected_track_id": winner["track_id"],
                "transition_style": "beat_blend_8s",
                "emotion_tag": "aloof",
                "dj_script": "",
            }

        base_script = self._build_rich_song_introduction(winner, recent_tracks, active_card)
        final_script = await self._try_generate_local_deepseek_line(winner, base_script)
        return {
            "selected_track_id": winner["track_id"],
            "transition_style": "beat_blend_8s",
            "emotion_tag": random.choice(["aloof", "smug"]),
            "dj_script": final_script,
        }

    async def handle_three_skip_intervention(
        self, new_card: Dict[str, Any]
    ) -> Dict[str, str]:
        char_name = self.persona_manager.get_character().get("name", "HOST")
        return {
            "emotion_tag": "annoyed",
            "dj_script": f"Three rapid skips detected. {char_name} is switching the station to {new_card.get('title')} and pulling in fresh artists.",
        }

    async def handle_live_caller_request(
        self, caller_transcript: str, available_cards: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return {
            "action": "search_track",
            "search_query": caller_transcript,
            "target_card_id": "",
            "emotion_tag": "smug",
            "dj_script": f"Copy that on the studio line. Queuing {caller_transcript} onto the next deck.",
        }

    async def unload_vram_for_sleep(self) -> None:
        if not self._ollama_online:
            return
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": "",
                        "keep_alive": OLLAMA_KEEP_ALIVE_SLEEP,
                    },
                )
        except Exception:
            pass
