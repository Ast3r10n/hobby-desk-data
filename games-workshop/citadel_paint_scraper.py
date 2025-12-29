#!/usr/bin/env python3
"""
Citadel Paint Scraper

Extracts paint data from a warhammer.com HAR file and fetches hex colors from SVG images.

Requirements:
    pip install requests (optional - uses urllib by default)

Usage:
    python citadel_paint_scraper.py <har_file> [options]

Examples:
    # Basic extraction with colors
    python citadel_paint_scraper.py warhammer.har
    
    # Generate catalogue file
    python citadel_paint_scraper.py warhammer.har --generate
    
    # Skip color fetching (faster, for testing)
    python citadel_paint_scraper.py warhammer.har --no-colors
    
    # Update existing JSON with new hex colors
    python citadel_paint_scraper.py warhammer.har --update-json citadel_paints.json
    
    # Update all JSON files in current directory
    python citadel_paint_scraper.py warhammer.har --update-all
    
    # Filter by category
    python citadel_paint_scraper.py warhammer.har --category Base --generate

Output format matches the standard paint database schema:
{
    "brand": "Games Workshop",
    "brandData": {},
    "category": "Base",
    "discontinued": false,
    "hex": "#8F7C68",
    "id": "citadel-99189950001",
    "impcat": {"layerId": null, "shadeId": null},
    "name": "Abaddon Black",
    "range": "Citadel",
    "sku": "99189950001",
    "type": "opaque",
    "url": "https://www.warhammer.com/en-GB/shop/..."
}
"""

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlopen, Request

# Base URL for SVG images
BASE_URL = "https://www.warhammer.com"

# Citadel paint categories and their default types
CITADEL_CATEGORIES = {
    "Base": {
        "name": "Base",
        "type": "opaque",
        "description": "High-pigment foundation paints"
    },
    "Layer": {
        "name": "Layer",
        "type": "opaque",
        "description": "Smooth coverage for layering"
    },
    "Shade": {
        "name": "Shade",
        "type": "wash",
        "description": "Washes for shading recesses"
    },
    "Dry": {
        "name": "Dry",
        "type": "opaque",
        "description": "Thick paints for drybrushing"
    },
    "Contrast": {
        "name": "Contrast",
        "type": "contrast",
        "description": "One-coat base and shade"
    },
    "Technical": {
        "name": "Technical",
        "type": "technical",
        "description": "Special effects paints"
    },
    "Spray": {
        "name": "Spray",
        "type": "spray",
        "description": "Spray primers and base coats"
    },
    "Air": {
        "name": "Air",
        "type": "air",
        "description": "Airbrush-ready paints"
    },
}

# Mapping of category keys to output filenames
CATEGORY_TO_FILE = {
    'Base': 'citadel_base.json',
    'Layer': 'citadel_layer.json',
    'Shade': 'citadel_shade.json',
    'Dry': 'citadel_dry.json',
    'Contrast': 'citadel_contrast.json',
    'Technical': 'citadel_technical.json',
    'Spray': 'citadel_spray.json',
    'Air': 'citadel_air.json',
}

# Metallic color ranges from Citadel
METALLIC_RANGES = {'Gold', 'Silver', 'Bronze', 'Brass', 'Copper'}

# Known technical paints that are actually other types
VARNISH_PAINTS = {
    "'Ardcoat", "Ardcoat", "Stormshield", "Munitorum Varnish"
}
THINNER_PAINTS = {
    "Lahmian Medium", "Contrast Medium", "Air Caste Thinner"
}

# Type overrides based on name keywords
TYPE_OVERRIDES = {
    'varnish': 'varnish',
    'thinner': 'thinner',
    'medium': 'thinner',
    'primer': 'primer',
    'undercoat': 'primer',
    'glaze': 'transparent',
    'ink': 'ink',
}

# Words that indicate non-paint products
EXCLUDE_KEYWORDS = [
    'brush', 'guide', 'cleaner', ' set', 'pack', 'bundle', 'kit',
    'book', 'magazine', 'full range', 'combo', 'collection',
    'colors set', 'colours set', 'paint set', 'color set', 'colour set',
    'case', 'suitcase', 'all colors', 'all colours', 'complete range',
    'super pack', 'display', 'stand', 'rack', 'airbrush', 'compressor',
    'stencil', 'tool', 'knife', 'cutter', 'tweezer', 'scenery', 'scenics',
    'grass', 'flock', 'tuft', 'palette', 'holder', 'handle'
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'image/svg+xml,*/*',
}


def normalize_sku(sku: str) -> str:
    """Normalize SKU for matching."""
    if not sku:
        return ''
    # Extract just the numeric part
    match = re.search(r'(\d{11})', sku)
    return match.group(1) if match else re.sub(r'\s+', '', sku).strip()


def normalize_name(name: str) -> str:
    """Normalize paint name for fuzzy matching."""
    if not name:
        return ''
    name = name.lower()
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    for word in ['citadel', 'paint', 'color', 'colour', 'games workshop']:
        name = re.sub(rf'\b{word}\b', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def get_paint_type(name: str, category: str, colour_range: str | None) -> str:
    """
    Determine the paint type based on name, category and colour range.
    
    Handles special cases like metallics, varnishes, thinners, and primers.
    """
    # Check for varnishes
    if name in VARNISH_PAINTS:
        return 'varnish'
    
    # Check for thinners/mediums
    if name in THINNER_PAINTS:
        return 'thinner'
    
    # Check name for type overrides
    name_lower = name.lower()
    for keyword, paint_type in TYPE_OVERRIDES.items():
        if keyword in name_lower:
            return paint_type
    
    # Check for metallics by colour range
    if colour_range in METALLIC_RANGES:
        return 'metallic'
    
    # Check for metallics by name keywords
    metallic_keywords = ['gold', 'silver', 'brass', 'bronze', 'copper', 'steel', 
                         'iron', 'leadbelcher', 'runefang', 'stormhost', 'retributor',
                         'balthasar', 'gehenna', 'auric', 'liberator', 'sycorax',
                         'canoptek', 'runelord', 'castellax', 'hashut', 'fulgurite',
                         'skullcrusher', 'brass scorpion', 'ironbreaker', 'necron compound',
                         'golden griffon', 'sigmarite', 'thallax', 'valdor']
    if any(kw in name_lower for kw in metallic_keywords):
        return 'metallic'
    
    # Fall back to category-based type
    cat_info = CITADEL_CATEGORIES.get(category, {})
    return cat_info.get('type', 'opaque')


def is_paint_product(paint: dict) -> bool:
    """Filter out non-paint products like brushes, sets, tools."""
    name = (paint.get('name') or '').lower()
    
    for keyword in EXCLUDE_KEYWORDS:
        if keyword in name:
            return False
    
    return True


def extract_hex_from_svg(svg_content: str) -> str | None:
    """
    Extract the primary paint color from SVG content.
    
    Citadel paint SVGs use a clip-path with id="pot" or id="spray" to define
    the paint pot/can shape, then fill it with a rect. The fill color on that
    rect is the paint color.
    """
    # Strategy 1: Look for rect fill inside the pot/spray clip-path group
    pot_pattern = r'clip-path="url\(#(?:pot|spray)\)"[^>]*>.*?<rect[^>]*fill="(#[0-9A-Fa-f]{6})"'
    match = re.search(pot_pattern, svg_content, re.DOTALL)
    if match:
        return match.group(1).upper()
    
    # Strategy 2: Look for any rect with a fill that's the paint color
    rect_fill_pattern = r'<rect[^>]*fill="(#[0-9A-Fa-f]{6})"'
    rect_matches = re.findall(rect_fill_pattern, svg_content)
    
    # Strategy 3: Fallback - find all hex colors and pick smartly
    hex_pattern = r'#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})(?![0-9A-Fa-f])'
    all_matches = re.findall(hex_pattern, svg_content)
    
    if not all_matches and not rect_matches:
        return None
    
    # Normalize to 6-digit hex
    normalized = []
    for m in (rect_matches or all_matches):
        color = m.lstrip('#')
        if len(color) == 3:
            color = ''.join(c + c for c in color)
        normalized.append(color.upper())
    
    # Count occurrences
    color_counts = Counter(normalized)
    
    # Filter out common non-paint colors (UI elements, shadows, labels)
    ignore_colors = {
        'FFFFFF', 'FEFEFE', 'FDFDFD', 'FCFCFC', 'FAFAFA',  # whites
        '000000', '010101', '020202', '030303',             # blacks
        'F5F5F5', 'EFEFEF', 'E0E0E0', 'D0D0D0', 'C0C0C0',  # light grays
        '808080', '888888', '999999', 'AAAAAA', 'B0B0B0',  # mid grays
        '292929', '333333', '444444', '555555', '666666', '1A1A1A',  # dark grays
    }
    
    # Get the most common color that isn't in our ignore list
    for color, count in color_counts.most_common():
        if color not in ignore_colors:
            return f"#{color}"
    
    # If all colors were ignored, return the most common one anyway
    if color_counts:
        return f"#{color_counts.most_common(1)[0][0]}"
    
    return None


def fetch_svg(url: str, retries: int = 3, delay: float = 0.5) -> str | None:
    """Fetch SVG content from URL with retries and rate limiting."""
    for attempt in range(retries):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=10) as response:
                return response.read().decode('utf-8')
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                return None
    return None


def sample_paint_color(paint: dict, verbose: bool = False) -> dict:
    """Fetch SVG and extract color for a single paint. Returns paint dict with hex added."""
    images = paint.get('images', [])
    if not images:
        return paint
    
    image_path = images[0]
    if not image_path.endswith('.svg'):
        return paint
    
    image_url = f"{BASE_URL}{image_path}"
    paint['_image_url'] = image_url
    
    svg_content = fetch_svg(image_url)
    if svg_content:
        paint['_hex'] = extract_hex_from_svg(svg_content)
        if verbose and paint.get('_hex'):
            print(f"      {paint.get('name', '?')}: {paint['_hex']}")
    
    return paint


def extract_paints_from_har(har_path: str) -> list[dict]:
    """Extract unique paint products from HAR file."""
    with open(har_path, 'r') as f:
        har = json.load(f)
    
    paints = []
    seen_skus = set()
    
    for entry in har['log']['entries']:
        try:
            content = json.loads(entry['response']['content']['text'])
            if 'results' not in content:
                continue
                
            for result in content['results']:
                for hit in result.get('hits', []):
                    if hit.get('productType') != 'paint':
                        continue
                    
                    sku = hit.get('sku')
                    if sku in seen_skus:
                        continue
                    seen_skus.add(sku)
                    
                    paints.append(hit)
        except (json.JSONDecodeError, KeyError):
            continue
    
    return paints


def scrape_category(paints: list[dict], category: str, sample_colors: bool = True, 
                    verbose: bool = False, max_workers: int = 8, filter_products: bool = True) -> list[dict]:
    """Extract and process paints for a specific category."""
    # Filter to category
    category_paints = [p for p in paints if (p.get('paintType') or [''])[0] == category]
    
    if not category_paints:
        print(f"    No paints found for category: {category}")
        return []
    
    # Filter non-paint products
    if filter_products:
        before_filter = len(category_paints)
        filtered_out = [p for p in category_paints if not is_paint_product(p)]
        category_paints = [p for p in category_paints if is_paint_product(p)]
        
        if filtered_out and verbose:
            print(f"      Filtered out {len(filtered_out)}: {', '.join(p.get('name', '?') for p in filtered_out)}")
    
    print(f"    {category}: {len(category_paints)} paints")
    
    if sample_colors:
        print(f"    Fetching SVGs ({max_workers} threads)...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(sample_paint_color, paint, verbose): paint for paint in category_paints}
            completed = 0
            for future in as_completed(futures):
                completed += 1
                paint = future.result()
                if verbose or completed % 20 == 0 or completed == len(category_paints):
                    sku = normalize_sku(paint.get('sku', ''))
                    hex_val = paint.get('_hex') or 'no color'
                    print(f"      [{completed}/{len(category_paints)}] {sku}: {hex_val}")
    
    return category_paints


def scrape_all_categories(har_path: str, sample_colors: bool = True, verbose: bool = False, 
                          max_workers: int = 8, filter_products: bool = True) -> dict:
    """Extract paints from HAR file, grouped by category."""
    print(f"Reading paints from {har_path}...")
    all_paints = extract_paints_from_har(har_path)
    print(f"Found {len(all_paints)} unique paints in HAR file")
    
    result = {}
    for category in CITADEL_CATEGORIES.keys():
        print(f"\n{'='*60}")
        print(f"Processing: {category}")
        print('='*60)
        
        category_paints = scrape_category(all_paints, category, sample_colors, 
                                          verbose, max_workers, filter_products)
        
        if category_paints:
            result[category] = {
                'name': category,
                'type': CITADEL_CATEGORIES[category]['type'],
                'paints': category_paints
            }
    
    return result


def generate_catalogue(scraped_data: list[dict], category: str = None) -> list[dict]:
    """Generate a fresh catalogue in standard format from scraped data."""
    catalogue = []
    seen_skus = {}
    
    for paint in scraped_data:
        raw_sku = paint.get('sku', '')
        sku = normalize_sku(raw_sku)
        if not sku:
            continue
        
        # Skip duplicates
        if sku in seen_skus:
            continue
        
        name = paint.get('name', 'Unknown')
        paint_types = paint.get('paintType', [])
        paint_category = paint_types[0] if paint_types else (category or 'Unknown')
        colour_range = paint.get('paintColourRange')
        
        entry = {
            "brand": "Games Workshop",
            "brandData": {},
            "category": paint_category,
            "discontinued": not paint.get('isAvailable', True),
            "hex": paint.get('_hex'),
            "id": f"citadel-{sku}",
            "impcat": {
                "layerId": None,
                "shadeId": None
            },
            "name": name,
            "range": "Citadel",
            "sku": sku,
            "type": get_paint_type(name, paint_category, colour_range),
            "url": f"https://www.warhammer.com/en-GB/shop/{paint.get('slug', '')}",
        }
        seen_skus[sku] = len(catalogue)
        catalogue.append(entry)
    
    # Sort by name
    catalogue.sort(key=lambda x: x['name'])
    return catalogue


def update_existing_json(json_path: str, scraped_data: list[dict]) -> dict:
    """Update existing JSON with scraped hex colors by matching SKU."""
    with open(json_path, 'r') as f:
        existing = json.load(f)
    
    # Build SKU -> data lookup
    sku_to_data = {}
    for paint in scraped_data:
        sku = normalize_sku(paint.get('sku', ''))
        if sku and paint.get('_hex'):
            sku_to_data[sku] = paint
    
    # Handle both formats
    if isinstance(existing, list):
        paint_list = existing
    elif isinstance(existing, dict) and 'paints' in existing:
        paint_list = existing['paints']
    else:
        print(f"  Unrecognized format")
        return existing
    
    updated = 0
    for paint in paint_list:
        sku = normalize_sku(paint.get('sku', ''))
        if sku in sku_to_data:
            scraped = sku_to_data[sku]
            if scraped.get('_hex'):
                paint['hex'] = scraped['_hex']
                updated += 1
    
    print(f"  Updated {updated} paints with hex colors")
    return existing


def batch_update_json_files(directory: str, scraped_data: list[dict]):
    """Update ALL JSON files in a directory with scraped hex colors."""
    # Build master SKU -> data lookup
    sku_to_data = {}
    name_to_data = {}
    
    for paint in scraped_data:
        sku = normalize_sku(paint.get('sku', ''))
        if sku and paint.get('_hex'):
            sku_to_data[sku] = paint
            
            # Also build name lookup for fallback matching
            name = paint.get('name', '')
            if name:
                norm_name = normalize_name(name)
                if norm_name and norm_name not in name_to_data:
                    name_to_data[norm_name] = paint
    
    print(f"\nMaster lookup: {len(sku_to_data)} SKUs, {len(name_to_data)} names")
    print(f"Scanning directory: {directory}\n")
    
    json_files = list(Path(directory).glob('*.json'))
    total_updated = 0
    
    for json_path in json_files:
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            
            updated = 0
            not_found = []
            
            # Handle both formats
            if isinstance(data, list):
                paint_list = data
            elif isinstance(data, dict) and 'paints' in data:
                paint_list = data['paints']
            else:
                print(f"  {json_path.name}: skipped - unrecognized format")
                continue
            
            for paint in paint_list:
                sku = normalize_sku(paint.get('sku', ''))
                matched_data = None
                
                # First try SKU match
                if sku in sku_to_data:
                    matched_data = sku_to_data[sku]
                else:
                    # Fallback to name match
                    paint_name = paint.get('name', '')
                    norm_name = normalize_name(paint_name)
                    if norm_name and norm_name in name_to_data:
                        matched_data = name_to_data[norm_name]
                
                if matched_data:
                    if matched_data.get('_hex') and paint.get('hex') != matched_data.get('_hex'):
                        paint['hex'] = matched_data['_hex']
                        updated += 1
                elif sku:
                    not_found.append(sku)
            
            if updated > 0:
                with open(json_path, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f"  {json_path.name}: {updated} paints updated")
                total_updated += updated
            else:
                print(f"  {json_path.name}: no changes")
            
            if not_found and len(not_found) < 20:
                print(f"    Not in scrape: {', '.join(not_found[:10])}")
                
        except Exception as e:
            print(f"  {json_path.name}: skipped - {e}")
    
    print(f"\nTotal: {total_updated} paints updated across {len(json_files)} files")


def main():
    parser = argparse.ArgumentParser(
        description='Extract Citadel paint data from HAR file with hex colors',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available categories:
  Base          High-pigment foundation paints
  Layer         Smooth coverage for layering
  Shade         Washes for shading recesses
  Dry           Thick paints for drybrushing
  Contrast      One-coat base and shade
  Technical     Special effects paints
  Spray         Spray primers and base coats
  Air           Airbrush-ready paints
  
  all           Process all categories (default)
        """
    )
    parser.add_argument('har_file', help='Path to HAR file from warhammer.com')
    parser.add_argument('--category', '-c', default='all',
                       help='Category to extract (default: all)')
    parser.add_argument('--output', '-o', default='citadel_paints.json',
                       help='Output JSON file')
    parser.add_argument('--update-json', '-u',
                       help='Update a single JSON file with scraped hex colors')
    parser.add_argument('--update-all', '-a', action='store_true',
                       help='Update ALL .json files in current directory with scraped hex colors')
    parser.add_argument('--no-colors', action='store_true',
                       help='Skip color fetching from SVGs')
    parser.add_argument('--no-filter', action='store_true',
                       help='Include non-paint products (sets, tools, etc.)')
    parser.add_argument('--workers', '-w', type=int, default=8,
                       help='Number of parallel threads for SVG fetching (default: 8)')
    parser.add_argument('--generate', '-g', action='store_true',
                       help='Generate fresh catalogue files instead of updating existing ones')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    
    args = parser.parse_args()
    sample_colors = not args.no_colors
    filter_products = not args.no_filter
    
    har_path = args.har_file
    if not Path(har_path).exists():
        print(f"Error: HAR file not found: {har_path}", file=sys.stderr)
        sys.exit(1)
    
    if args.category == 'all':
        print("Processing ALL Citadel categories...")
        data = scrape_all_categories(har_path, sample_colors, args.verbose, 
                                     args.workers, filter_products)
        
        # Flatten all paints
        all_paints = []
        for cat_data in data.values():
            all_paints.extend(cat_data['paints'])
        
        if args.generate:
            # Generate separate files per category
            print(f"\nGenerating {len(data)} catalogue files:")
            for category, cat_data in data.items():
                output_file = CATEGORY_TO_FILE.get(category, f'citadel_{category.lower()}.json')
                catalogue = generate_catalogue(cat_data['paints'], category)
                with open(output_file, 'w') as f:
                    json.dump(catalogue, f, indent=2)
                print(f"  {output_file}: {len(catalogue)} paints")
            print("\nDone!")
        elif args.update_all:
            batch_update_json_files('.', all_paints)
        elif args.update_json:
            updated = update_existing_json(args.update_json, all_paints)
            with open(args.update_json, 'w') as f:
                json.dump(updated, f, indent=2)
            print(f"\nUpdated: {args.update_json}")
        else:
            # Generate single combined catalogue
            catalogue = generate_catalogue(all_paints)
            
            # Count results
            with_color = sum(1 for p in catalogue if p['hex'])
            without_color = len(catalogue) - with_color
            
            output = {
                "metadata": {
                    "brand": "Games Workshop",
                    "range": "Citadel",
                    "extracted": time.strftime("%Y-%m-%d"),
                    "source": "warhammer.com",
                    "total_paints": len(catalogue),
                    "paints_with_hex": with_color,
                    "paints_without_hex": without_color
                },
                "paints": catalogue
            }
            
            with open(args.output, 'w') as f:
                json.dump(output, f, indent=2)
            
            print(f"\nDone! Saved {len(catalogue)} paints to {args.output}")
            print(f"  - With hex colors: {with_color}")
            print(f"  - Without hex colors: {without_color}")
            
            # Show category breakdown
            cats = Counter(p['category'] for p in catalogue)
            print("\nBreakdown by category:")
            for cat, count in sorted(cats.items()):
                print(f"  {cat}: {count}")
            
            # Show type breakdown
            types = Counter(p['type'] for p in catalogue)
            print("\nBreakdown by type:")
            for t, count in sorted(types.items()):
                print(f"  {t}: {count}")
    else:
        if args.category not in CITADEL_CATEGORIES:
            print(f"Unknown category: {args.category}")
            print(f"Available: {', '.join(CITADEL_CATEGORIES.keys())}")
            return
        
        print(f"Reading paints from {har_path}...")
        all_paints = extract_paints_from_har(har_path)
        print(f"Found {len(all_paints)} unique paints in HAR file")
        
        category_paints = scrape_category(all_paints, args.category, sample_colors, 
                                          args.verbose, args.workers, filter_products)
        
        if args.generate:
            output_file = CATEGORY_TO_FILE.get(args.category, f'citadel_{args.category.lower()}.json')
            catalogue = generate_catalogue(category_paints, args.category)
            with open(output_file, 'w') as f:
                json.dump(catalogue, f, indent=2)
            print(f"\nGenerated {output_file}: {len(catalogue)} paints")
        elif args.update_all:
            batch_update_json_files('.', category_paints)
        elif args.update_json:
            updated = update_existing_json(args.update_json, category_paints)
            with open(args.update_json, 'w') as f:
                json.dump(updated, f, indent=2)
            print(f"\nUpdated: {args.update_json}")
        else:
            output_data = {
                'category': args.category,
                'name': CITADEL_CATEGORIES[args.category]['name'],
                'paints': generate_catalogue(category_paints, args.category)
            }
            with open(args.output, 'w') as f:
                json.dump(output_data, f, indent=2)
            print(f"\nSaved: {args.output}")


if __name__ == '__main__':
    main()
