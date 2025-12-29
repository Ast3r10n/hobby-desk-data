#!/usr/bin/env python3
"""
Manifest Generator for Hobby Desk Paint Data

Scans all paint JSON files, computes hashes, and generates manifest.json
Run with: python scripts/generate_manifest.py
"""

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
MANIFEST_PATH = ROOT_DIR / "manifest.json"

# Directories to skip
SKIP_DIRS = {".git", "node_modules", "scripts", ".github", "__pycache__"}

# Files to skip (cache files, etc.)
SKIP_FILES = {".ak_set_skus_cache.json"}

# Brand name mappings
BRAND_MAP = {
    "ak-interactive": "AK Interactive",
    "army-painter": "The Army Painter",
    "colour-forge": "Colour Forge",
    "games-workshop": "Games Workshop",
    "monument-hobbies": "Monument Hobbies",
    "two-thin-coats": "Two Thin Coats",
    "vallejo": "Vallejo",
}


def get_commit_hash() -> str:
    """Get the current git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of file contents."""
    with open(file_path, "rb") as f:
        hash_value = hashlib.sha256(f.read()).hexdigest()
    return f"sha256:{hash_value}"


def format_brand_name(dir_name: str) -> str:
    """Convert directory name to display brand name."""
    if dir_name in BRAND_MAP:
        return BRAND_MAP[dir_name]
    return " ".join(word.capitalize() for word in dir_name.split("-"))


def extract_range_name(paints: list) -> str:
    """Extract range name from first paint in list."""
    if paints and isinstance(paints[0], dict) and "range" in paints[0]:
        return paints[0]["range"]
    return "Unknown"


def find_paint_files() -> list[dict]:
    """Find all paint JSON files in the repository."""
    files = []

    for root, dirs, filenames in os.walk(ROOT_DIR):
        # Filter out directories we want to skip
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for filename in filenames:
            if filename.endswith(".json") and filename != "manifest.json" and filename not in SKIP_FILES:
                full_path = Path(root) / filename
                relative_path = full_path.relative_to(ROOT_DIR)
                brand_dir = relative_path.parts[0] if relative_path.parts else ""

                files.append(
                    {
                        "full_path": full_path,
                        "relative_path": str(relative_path),
                        "brand_dir": brand_dir,
                    }
                )

    return files


def generate_manifest() -> dict:
    """Generate the manifest file."""
    print("Scanning for paint files...")
    paint_files = find_paint_files()
    print(f"Found {len(paint_files)} paint files")

    manifest_files = []
    total_paints = 0

    for file_info in paint_files:
        print(f"Processing: {file_info['relative_path']}")

        try:
            with open(file_info["full_path"], "r", encoding="utf-8") as f:
                paints = json.load(f)

            if not isinstance(paints, list):
                print("  Skipping: not an array of paints")
                continue

            brand = format_brand_name(file_info["brand_dir"])
            range_name = extract_range_name(paints)
            file_hash = compute_file_hash(file_info["full_path"])
            paint_count = len(paints)

            manifest_files.append(
                {
                    "brand": brand,
                    "range": range_name,
                    "path": file_info["relative_path"],
                    "hash": file_hash,
                    "paintCount": paint_count,
                }
            )

            total_paints += paint_count
            print(f"  Brand: {brand}, Range: {range_name}, Paints: {paint_count}")

        except json.JSONDecodeError as e:
            print(f"  Error parsing JSON: {e}")
        except Exception as e:
            print(f"  Error processing: {e}")

    # Sort files by brand, then range
    manifest_files.sort(key=lambda x: (x["brand"], x["range"]))

    manifest = {
        "version": 1,
        "commitHash": get_commit_hash(),
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "totalPaints": total_paints,
        "files": manifest_files,
    }

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    print(f"\nManifest generated: {MANIFEST_PATH}")
    print(f"Total paints: {total_paints}")
    print(f"Total files: {len(manifest_files)}")

    return manifest


if __name__ == "__main__":
    generate_manifest()
