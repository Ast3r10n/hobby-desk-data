#!/usr/bin/env python3
"""
Reaper Miniatures Paint Scraper

Scrapes reapermini.com to build a paint database with hex colors.
Uses embedded Vue.js JSON data from collection pages.

Requirements:
    pip install requests beautifulsoup4 pillow

Usage:
    python reaper_paint_scraper.py [--range RANGE_NAME] [--output OUTPUT_FILE]

Examples:
    # Scrape a single range
    python reaper_paint_scraper.py --range core

    # Scrape all ranges and generate individual JSON files
    python reaper_paint_scraper.py --range all --generate

    # Scrape without color sampling (faster, for testing)
    python reaper_paint_scraper.py --range core --no-colors

    # Include triads data from separate triads page
    python reaper_paint_scraper.py --range all --generate --with-triads

Output format matches the standard paint database schema:
{
    "brand": "Reaper",
    "brandData": {
        "flexibleTriad": {
            "triadId": "blood-colors",
            "colors": ["reaper-09003", "reaper-09004", "reaper-09005"]
        }
    },
    "category": "",
    "discontinued": false,
    "hex": "#8B4513",
    "id": "reaper-09001",
    "impcat": {"layerId": null, "shadeId": null},
    "name": "Dragon Red",
    "range": "Master Series Core",
    "sku": "09001",
    "type": "opaque",
    "url": "https://www.reapermini.com/search/09001"
}
"""

import argparse
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image

# Reaper paint ranges - page URLs and metadata
REAPER_RANGES = {
    "core": {
        "name": "Master Series Core Colors",
        "range": "Master Series Core",
        "type": "opaque",
        "url": "https://www.reapermini.com/paints/master-series-paints-core-colors"
    },
    "bones": {
        "name": "Master Series Bones",
        "range": "Master Series Bones",
        "type": "opaque",
        "url": "https://www.reapermini.com/paints/master-series-paints-bones"
    },
    "pathfinder": {
        "name": "Master Series Pathfinder",
        "range": "Master Series Pathfinder",
        "type": "opaque",
        "url": "https://www.reapermini.com/paints/master-series-paints-pathfinder-colors"
    },
}

# Triads page URL (contains triad set products, not individual paints)
TRIADS_URL = "https://www.reapermini.com/paints/master-series-paints-triads"

# Image base URL - Reaper hosts images at images.reapermini.com/{size}/{filename}
IMAGE_BASE_URL = "https://images.reapermini.com"

# Type overrides based on name keywords
TYPE_OVERRIDES = {
    'metallic': 'metallic',
    'metal': 'metallic',
    'gold': 'metallic',
    'silver': 'metallic',
    'copper': 'metallic',
    'bronze': 'metallic',
    'brass': 'metallic',
    'steel': 'metallic',
    'iron': 'metallic',
    'chrome': 'metallic',
    'ink': 'ink',
    'wash': 'wash',
    'liner': 'wash',
    'glaze': 'transparent',
    'clear': 'transparent',
    'primer': 'primer',
    'varnish': 'varnish',
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

# SKU prefixes for filtering out non-individual paint products
# Individual paints have 5-digit SKUs starting with these prefixes
INDIVIDUAL_PAINT_PREFIXES = ['09', '89']  # Core/HD use 09xxx, Pathfinder uses 89xxx

# Price threshold for individual paints (in cents) - sets cost more
MAX_INDIVIDUAL_PAINT_PRICE = 500  # $5.00 - individual paints are ~$3.89


def to_title_case(name: str) -> str:
    """Convert name to title case."""
    if not name:
        return name
    name = html.unescape(name)
    words = name.split()
    result = []
    for word in words:
        if word.upper() == word or word.lower() == word:
            word = word.title()
        result.append(word)
    return ' '.join(result)


def get_paint_type(name: str, default_type: str) -> str:
    """Determine paint type from name keywords."""
    name_lower = name.lower()
    for keyword, paint_type in TYPE_OVERRIDES.items():
        if keyword in name_lower:
            return paint_type
    return default_type


def is_individual_paint(paint: dict) -> bool:
    """Filter out sets and non-individual paint products."""
    sku = paint.get('sku', '')
    price = paint.get('price', 0)
    name = paint.get('name', '').lower()

    # Check if SKU matches individual paint pattern
    if not any(sku.startswith(prefix) for prefix in INDIVIDUAL_PAINT_PREFIXES):
        return False

    # Must have 5-digit SKU
    if not re.match(r'^\d{5}$', sku):
        return False

    # Price filter - sets cost more than $5
    if price > MAX_INDIVIDUAL_PAINT_PRICE:
        return False

    # Exclude sets, kits, and multi-packs
    exclude_keywords = ['set', 'kit', 'pack', 'triad', 'collection', 'colors of']
    if any(kw in name for kw in exclude_keywords):
        return False

    return True


def fetch_page(url: str, retries: int = 3) -> BeautifulSoup:
    """Fetch a page and return BeautifulSoup object."""
    for attempt in range(retries):
        try:
            print(f"    Fetching: {url}")
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            return BeautifulSoup(response.text, 'html.parser')
        except requests.RequestException as e:
            if attempt < retries - 1:
                print(f"    Retry {attempt + 1}/{retries}: {e}")
                time.sleep(2)
            else:
                raise


def extract_paints_from_page(soup: BeautifulSoup) -> list:
    """Extract paint data from embedded Vue.js JSON in the page."""
    paints = []

    # Find the script containing Vue data with paints array
    for script in soup.find_all('script'):
        script_text = script.string or ''

        # Look for Vue component initialization with paints array
        if 'paints:' in script_text and 'new Vue' in script_text:
            # Extract the paints array using regex
            # Pattern matches: paints: [{...}, {...}, ...], colors:
            match = re.search(r'paints:\s*(\[.*?\]),\s*colors:', script_text, re.DOTALL)
            if match:
                try:
                    paints_json = match.group(1)
                    # Clean up any JavaScript-specific syntax
                    paints_json = re.sub(r',\s*]', ']', paints_json)  # Remove trailing commas
                    paints_data = json.loads(paints_json)

                    for paint in paints_data:
                        paints.append({
                            'id': paint.get('_id'),
                            'sku': paint.get('sku', ''),
                            'name': paint.get('name', ''),
                            'price': paint.get('price', 0),
                            'inventory': paint.get('inventory', 0),
                            'images': paint.get('images', []),
                            'meta': paint.get('meta', {}),
                        })
                    break
                except json.JSONDecodeError as e:
                    print(f"      Warning: Failed to parse paints JSON: {e}")

    return paints


def extract_triads_from_page(soup: BeautifulSoup) -> dict:
    """Extract triad set data from the triads page.

    Returns a dict mapping triad names to their component SKUs.
    Triads are sold as sets but contain 3 individual paints.
    """
    triads = {}

    for script in soup.find_all('script'):
        script_text = script.string or ''

        if 'paints:' in script_text and 'new Vue' in script_text:
            match = re.search(r'paints:\s*(\[[\s\S]*?\])\s*,\s*(?:filters|selectedFilters|sortBy)', script_text)
            if match:
                try:
                    paints_json = match.group(1)
                    paints_json = re.sub(r',\s*]', ']', paints_json)
                    paints_data = json.loads(paints_json)

                    for paint in paints_data:
                        name = paint.get('name', '')
                        sku = paint.get('sku', '')

                        # Triad sets have names like "Blood Colors", "Tanned Skin", etc.
                        # They are priced around $11.49 for 3 paints
                        if paint.get('price', 0) > 1000:  # > $10 indicates a set
                            # Create a slug from the triad name
                            triad_id = name.lower().replace(' ', '-').replace("'", '')
                            triad_id = re.sub(r'[^a-z0-9-]', '', triad_id)

                            triads[triad_id] = {
                                'name': name,
                                'sku': sku,
                                # Component SKUs will be inferred from sequential SKUs
                                # e.g., 09701 triad -> 09003, 09004, 09005
                            }
                except json.JSONDecodeError as e:
                    print(f"      Warning: Failed to parse triads JSON: {e}")

    return triads


def get_image_url(paint: dict, size: int = 4) -> str:
    """Get the image URL for a paint."""
    images = paint.get('images', [])
    if images:
        filename = images[0].get('filename', '')
        if filename:
            return f"{IMAGE_BASE_URL}/{size}/{filename}"

    # Fallback: construct from SKU
    sku = paint.get('sku', '')
    if sku:
        return f"{IMAGE_BASE_URL}/{size}/{sku}.jpg"

    return None


def sample_color_from_image(img_url: str, verbose: bool = False) -> str:
    """Download image and sample the paint color.

    Reaper paint bottles have a color swatch on the cap/label.
    The images typically show the bottle with the paint color visible.
    """
    try:
        response = requests.get(img_url, headers=HEADERS, timeout=30)
        response.raise_for_status()

        img = Image.open(BytesIO(response.content)).convert('RGB')
        width, height = img.size

        # Reaper bottle images show the paint color in the middle-upper area
        # Sample from multiple locations to find the dominant paint color
        sample_regions = [
            # Center area where bottle label/cap color is visible
            (int(width * 0.40), int(height * 0.35)),
            (int(width * 0.50), int(height * 0.35)),
            (int(width * 0.60), int(height * 0.35)),
            (int(width * 0.45), int(height * 0.40)),
            (int(width * 0.55), int(height * 0.40)),
            (int(width * 0.50), int(height * 0.45)),
            # Upper portion
            (int(width * 0.50), int(height * 0.30)),
            (int(width * 0.45), int(height * 0.32)),
            (int(width * 0.55), int(height * 0.32)),
        ]

        best_color = None
        best_score = -1

        for x, y in sample_regions:
            # Sample a small region around the point
            colors = []
            for dx in range(-6, 7, 2):
                for dy in range(-6, 7, 2):
                    px = max(0, min(x + dx, width - 1))
                    py = max(0, min(y + dy, height - 1))
                    colors.append(img.getpixel((px, py)))

            # Average the colors
            r = sum(c[0] for c in colors) // len(colors)
            g = sum(c[1] for c in colors) // len(colors)
            b = sum(c[2] for c in colors) // len(colors)

            # Score: prefer saturated, non-white, non-black colors
            max_c = max(r, g, b)
            min_c = min(r, g, b)
            saturation = (max_c - min_c) / max(max_c, 1) if max_c > 0 else 0
            brightness = (r + g + b) / 3

            # Skip near-white or near-black (background/shadows)
            if brightness > 240 or brightness < 15:
                continue

            # Prefer mid-brightness, saturated colors
            brightness_penalty = abs(brightness - 127) / 127
            score = saturation * (1 - brightness_penalty * 0.3) + 0.1

            if score > best_score:
                best_score = score
                best_color = (r, g, b)

        if best_color:
            hex_color = "#{:02X}{:02X}{:02X}".format(*best_color)
            if verbose:
                print(f"        -> {hex_color} (score: {best_score:.3f})")
            return hex_color

        # Fallback: sample from center
        x, y = int(width * 0.50), int(height * 0.40)
        r, g, b = img.getpixel((x, y))
        return "#{:02X}{:02X}{:02X}".format(r, g, b)

    except Exception as e:
        print(f"        Error sampling color: {e}")
        return None


def sample_paint_color(paint: dict, verbose: bool = False) -> dict:
    """Sample color for a single paint. Returns the paint dict with hex added."""
    img_url = get_image_url(paint)
    if img_url:
        paint['img_url'] = img_url
        paint['hex'] = sample_color_from_image(img_url, verbose)
    return paint


def build_triad_mapping(all_paints: list) -> dict:
    """Build triad groupings from paint SKUs.

    Reaper organizes paints in triads where:
    - Shadow colors end in 3, 6, 9, etc. (every 3rd starting at 3)
    - Midtone colors end in 4, 7, 0 (every 3rd starting at 1)
    - Highlight colors end in 5, 8, 1 (every 3rd starting at 2)

    Actually, Reaper uses a simpler pattern: consecutive SKUs form triads.
    e.g., 09003, 09004, 09005 are a triad (shadow, midtone, highlight).
    """
    triads = {}

    # Group paints by their triad (floor divide SKU by 3)
    for paint in all_paints:
        sku = paint.get('sku', '')
        if not sku or not sku.isdigit():
            continue

        sku_num = int(sku)
        # Determine triad group: (sku - 3) // 3 for SKUs starting at 09003
        # This groups 09003-09005, 09006-09008, etc.
        if sku_num >= 9003:
            adjusted = sku_num - 3
            triad_num = (adjusted // 3) * 3 + 3
            triad_id = f"triad-{triad_num:05d}"

            if triad_id not in triads:
                triads[triad_id] = []
            triads[triad_id].append(paint)

    # Filter to only complete triads (exactly 3 colors)
    complete_triads = {k: v for k, v in triads.items() if len(v) == 3}

    return complete_triads


def scrape_range(range_key: str, sample_colors: bool = True, verbose: bool = False, max_workers: int = 8) -> list:
    """Scrape all paints from a Reaper range."""
    if range_key not in REAPER_RANGES:
        print(f"Unknown range: {range_key}")
        return []

    range_info = REAPER_RANGES[range_key]
    range_name = range_info['name']
    url = range_info['url']
    default_type = range_info['type']

    print(f"\n{'='*60}")
    print(f"Scraping: {range_name} ({range_key})")
    print('='*60)

    try:
        soup = fetch_page(url)
        paints = extract_paints_from_page(soup)

        if not paints:
            print(f"    No paints found for: {range_key}")
            return []

        print(f"    Found {len(paints)} products")

        # Filter to individual paints only
        paints = [p for p in paints if is_individual_paint(p)]
        print(f"    After filtering: {len(paints)} individual paints")

        # Sample colors if requested
        if sample_colors and paints:
            print(f"    Sampling colors ({max_workers} threads)...")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(sample_paint_color, paint, verbose): paint for paint in paints}
                completed = 0
                for future in as_completed(futures):
                    completed += 1
                    paint = future.result()
                    sku = paint.get('sku') or '?'
                    hex_val = paint.get('hex') or 'failed'
                    if verbose or completed % 20 == 0 or completed == len(paints):
                        print(f"      [{completed}/{len(paints)}] {sku}: {hex_val}")

        # Add metadata to each paint
        for paint in paints:
            paint['paint_type'] = get_paint_type(paint.get('name', ''), default_type)
            paint['range_name'] = range_info['range']
            paint['product_url'] = f"https://www.reapermini.com/search/{paint.get('sku', '')}"

        print(f"  Total: {len(paints)} paints")
        return paints

    except Exception as e:
        print(f"    Error scraping {range_key}: {e}")
        return []


def scrape_all_ranges(sample_colors: bool = True, verbose: bool = False, max_workers: int = 8) -> dict:
    """Scrape all Reaper ranges."""
    all_data = {}

    for range_key in REAPER_RANGES.keys():
        paints = scrape_range(range_key, sample_colors, verbose, max_workers)
        all_data[range_key] = {
            'name': REAPER_RANGES[range_key]['name'],
            'range': REAPER_RANGES[range_key]['range'],
            'paints': paints
        }
        time.sleep(1)  # Be polite between ranges

    return all_data


def generate_catalogue(scraped_data: list, range_name: str, triads: dict = None) -> list:
    """Generate a catalogue in standard format from scraped data."""
    catalogue = []
    seen_skus = set()

    # Build triad mapping if we have triads
    triad_map = {}
    if triads:
        for triad_id, triad_paints in triads.items():
            # Sort by SKU to get shadow/midtone/highlight order
            sorted_paints = sorted(triad_paints, key=lambda p: p.get('sku', ''))
            color_ids = [f"reaper-{p.get('sku', '')}" for p in sorted_paints]

            for paint in sorted_paints:
                sku = paint.get('sku', '')
                triad_map[sku] = {
                    'triadId': triad_id,
                    'colors': color_ids
                }

    for paint in scraped_data:
        sku = paint.get('sku', '')
        if not sku or sku in seen_skus:
            continue

        seen_skus.add(sku)
        name = to_title_case(paint.get('name', ''))

        # Build brandData with triad info if available
        brand_data = {}
        if sku in triad_map:
            brand_data['flexibleTriad'] = triad_map[sku]

        entry = {
            "brand": "Reaper",
            "brandData": brand_data,
            "category": "",
            "discontinued": False,
            "hex": paint.get('hex', ''),
            "id": f"reaper-{sku}",
            "impcat": {
                "layerId": None,
                "shadeId": None
            },
            "name": name,
            "range": paint.get('range_name', range_name),
            "sku": sku,
            "type": paint.get('paint_type', 'opaque'),
            "url": paint.get('product_url', '')
        }
        catalogue.append(entry)

    # Sort by SKU
    catalogue.sort(key=lambda x: x['sku'])
    return catalogue


# Mapping of range keys to output filenames
RANGE_TO_FILE = {
    'core': 'reaper_master_series_core.json',
    'bones': 'reaper_master_series_bones.json',
    'pathfinder': 'reaper_master_series_pathfinder.json',
}


def main():
    parser = argparse.ArgumentParser(
        description='Scrape Reaper Miniatures paint data with hex colors',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available ranges:
  core                Master Series Core Colors
  bones               Master Series Bones
  pathfinder          Master Series Pathfinder

  all                 Scrape everything
        """
    )
    parser.add_argument('--range', '-r', default='all',
                       help='Range to scrape (default: all)')
    parser.add_argument('--output', '-o', default='reaper_paints.json',
                       help='Output JSON file')
    parser.add_argument('--no-colors', action='store_true',
                       help='Skip color sampling')
    parser.add_argument('--workers', '-w', type=int, default=8,
                       help='Number of parallel threads for image sampling (default: 8)')
    parser.add_argument('--generate', '-g', action='store_true',
                       help='Generate fresh catalogue files instead of single output')
    parser.add_argument('--with-triads', action='store_true',
                       help='Include triad grouping data in output')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')

    args = parser.parse_args()
    sample_colors = not args.no_colors

    if args.range == 'all':
        print("Scraping ALL Reaper ranges...")
        data = scrape_all_ranges(sample_colors, args.verbose, args.workers)

        # Build triad mappings if requested
        triads = {}
        if args.with_triads:
            print("\nBuilding triad mappings...")
            # Combine all paints for triad analysis
            all_paints = []
            for range_data in data.values():
                all_paints.extend(range_data['paints'])
            triads = build_triad_mapping(all_paints)
            print(f"  Found {len(triads)} complete triads")

        if args.generate:
            # Generate separate files per range
            print(f"\nGenerating {len(data)} catalogue files:")
            for range_key, range_data in data.items():
                output_file = RANGE_TO_FILE.get(range_key, f'reaper_{range_key}.json')
                catalogue = generate_catalogue(range_data['paints'], range_data['range'], triads)
                with open(output_file, 'w') as f:
                    json.dump(catalogue, f, indent=2)
                print(f"  {output_file}: {len(catalogue)} paints")
            print("\nDone!")
        else:
            # Flatten all paints
            all_paints = []
            for range_data in data.values():
                all_paints.extend(range_data['paints'])

            with open(args.output, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"\nSaved: {args.output}")
    else:
        if args.range not in REAPER_RANGES:
            print(f"Unknown range: {args.range}")
            print(f"Available: {', '.join(REAPER_RANGES.keys())}")
            return

        paints = scrape_range(args.range, sample_colors, args.verbose, args.workers)

        # Build triads if requested
        triads = {}
        if args.with_triads:
            triads = build_triad_mapping(paints)
            print(f"  Found {len(triads)} complete triads")

        if args.generate:
            output_file = RANGE_TO_FILE.get(args.range, f'reaper_{args.range}.json')
            range_name = REAPER_RANGES[args.range]['range']
            catalogue = generate_catalogue(paints, range_name, triads)
            with open(output_file, 'w') as f:
                json.dump(catalogue, f, indent=2)
            print(f"\nGenerated {output_file}: {len(catalogue)} paints")
        else:
            output_data = {
                'range': args.range,
                'name': REAPER_RANGES[args.range]['name'],
                'paints': paints
            }
            with open(args.output, 'w') as f:
                json.dump(output_data, f, indent=2)
            print(f"\nSaved: {args.output}")


if __name__ == '__main__':
    main()
