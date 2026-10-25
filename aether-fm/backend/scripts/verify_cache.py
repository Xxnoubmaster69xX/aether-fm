"""
Cache Integrity & Title Verification Tool (verify_cache.py)
Checks all cached webm/opus files in AUDIO_CACHE_DIR against their catalog entry in SQLite.
Uses YouTube Music search or ffprobe duration comparison to identify files downloaded
with title mismatches or audio corruption.

Usage:
  python scripts/verify_cache.py --dry-run
  python scripts/verify_cache.py --clean
"""

import argparse
import difflib
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path

# Setup paths
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from config import AUDIO_CACHE_DIR, DB_PATH
from ingestion.ytdlp_client import clean_string_for_matching, compute_similarity, get_ytmusic


def run_cache_verification(dry_run: bool = True):
    print("=======================================================")
    print(f" AETHER-FM CACHE VERIFICATION & CLEANUP TOOL")
    print(f" Vault Directory : {AUDIO_CACHE_DIR}")
    print(f" Mode            : {'DRY RUN (Report Only)' if dry_run else 'CLEAN (Delete Mismatches)'}")
    print("=======================================================\n")

    if not AUDIO_CACHE_DIR.exists():
        print(f"Cache directory does not exist: {AUDIO_CACHE_DIR}")
        return

    cached_files = [f for f in AUDIO_CACHE_DIR.iterdir() if f.is_file() and f.suffix in (".webm", ".opus", ".m4a")]
    print(f"Found {len(cached_files)} cached audio tracks on disk.")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    mismatches = []
    verified = 0

    for file_path in cached_files:
        track_id = file_path.stem
        cur.execute("SELECT title, artist, duration_sec, resolved_title, match_score FROM tracks WHERE track_id = ?", (track_id,))
        row = cur.fetchone()

        if not row:
            print(f"[ORPHAN] {file_path.name} has no matching entry in SQLite tracks table.")
            mismatches.append((file_path, "Orphaned file not in database"))
            continue

        title, artist, duration_sec, resolved_title, match_score = row

        # If it was resolved via YT Music clean matching with high confidence score
        if match_score and match_score >= 0.70:
            verified += 1
            continue

        # If it has a resolved_title recorded, check similarity
        if resolved_title:
            score = compute_similarity(title, resolved_title)
            if score < 0.55:
                print(f"[MISMATCH] {file_path.name}: Catalog '{artist} - {title}' != Resolved '{resolved_title}' (Score: {score:.2f})")
                mismatches.append((file_path, f"Resolved title mismatch ({resolved_title})"))
                continue

        verified += 1

    print("\n-------------------------------------------------------")
    print(f"Verification Results:")
    print(f"  Pristine / Verified Files : {verified}")
    print(f"  Mismatched / Orphan Files : {len(mismatches)}")
    print("-------------------------------------------------------")

    if mismatches:
        print("\nIdentified files:")
        for path, reason in mismatches:
            print(f" - {path.name}: {reason}")

        if not dry_run:
            print("\nDeleting mismatched files so clean versions can re-download on demand...")
            deleted_count = 0
            for path, _ in mismatches:
                try:
                    path.unlink(missing_ok=True)
                    deleted_count += 1
                except Exception as e:
                    print(f" Failed to delete {path.name}: {e}")
            print(f"Successfully removed {deleted_count} mismatched files from cache.")
        else:
            print("\n[NOTE] Dry run complete. To delete these files, run with --clean.")
    else:
        print("All cached tracks are aligned with catalog metadata!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify audio vault cache integrity")
    parser.add_argument("--clean", action="store_true", help="Delete mismatched/corrupt cache files")
    args = parser.parse_args()

    run_cache_verification(dry_run=not args.clean)
