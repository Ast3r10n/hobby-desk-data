#!/usr/bin/env python3
"""
P3 (Formula P3) Paint Scraper

Scrapes steamforged.com (Shopify) to build a paint database with hex colors
for P3 paints, originally by Privateer Press, now distributed by Steamforged Games.

Requirements:
    pip install requests pillow

Usage:
    python p3_paint_scraper.py [--range RANGE_NAME]

Examples:
    # Scrape all ranges and generate individual JSON files
    python p3_paint_scraper.py --range all

    # Scrape without color sampling (faster, for testing)
    python p3_paint_scraper.py --range all --no-colors

    # Scrape only standard paints
    python p3_paint_scraper.py --range standard

Output format matches the standard paint database schema:
{
    "brand": "P3",
    "brandData": {},
    "category": "",
    "discontinued": false,
    "hex": "#RRGGBB",
    "id": "p3-arcane-blue",
    "impcat": {"layerId": null, "shadeId": null},
    "name": "Arcane Blue",
    "range": "Formula P3",
    "sku": "SFP3-N136-S",
    "type": "opaque",
    "url": "https://steamforged.com/en-gb/products/p3-paints-arcane-blue"
}
"""

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

import requests
from PIL import Image

# Base URL for P3 products at Steamforged Games (Shopify store)
BASE_URL = "https://steamforged.com/en-gb"
COLLECTION_URL = f"{BASE_URL}/collections/p3-paints/products.json"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Products to exclude (sets, accessories, mediums)
EXCLUDE_KEYWORDS = [
    'starter set', 'set ', 'bundle', 'collection', 'kit', 'pack',
    'brush', 'palette', 'tool',
]

# Known metallic paint names (from SKU range N222-N234, N240-N244)
METALLIC_SKUS = {
    'SFP3-N222-S', 'SFP3-N223-S', 'SFP3-N224-S', 'SFP3-N225-S',
    'SFP3-N226-S', 'SFP3-N227-S', 'SFP3-N228-S', 'SFP3-N229-S',
    'SFP3-N230-S', 'SFP3-N231-S', 'SFP3-N232-S', 'SFP3-N233-S',
    'SFP3-N234-S', 'SFP3-N240-S', 'SFP3-N241-S', 'SFP3-N242-S',
    'SFP3-N243-S', 'SFP3-N244-S',
}

# Metallic paint name keywords (backup detection)
METALLIC_KEYWORDS = [
    'gold', 'silver', 'steel', 'bronze', 'copper', 'iron',
    'platinum', 'brass', 'metal',
]

# Medium SKUs
MEDIUM_SKUS = {'SFP3-N235-S'}

# Mapping of range keys to output filenames
RANGE_TO_FILE = {
    'standard': 'p3_formula_p3.json',
    'metallic': 'p3_metallic.json',
}


def fetch_json(url: str, retries: int = 3) -> dict:
    """Fetch JSON from a URL."""
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            if attempt < retries - 1:
                print(f"    Retry {attempt + 1}/{retries}: {e}")
                time.sleep(2)
            else:
                raise


def get_all_products() -> list:
    """Fetch all P3 products from Steamforged Shopify API."""
    products = []
    page = 1

    while True:
        url = f"{COLLECTION_URL}?page={page}&limit=250"
        print(f"    Fetching: {url}")

        try:
            data = fetch_json(url)
            page_products = data.get('products', [])

            if not page_products:
                break

            products.extend(page_products)
            print(f"    Page {page}: {len(page_products)} products")

            if len(page_products) < 250:
                break

            page += 1
            time.sleep(0.5)

        except Exception as e:
            print(f"    Error fetching page {page}: {e}")
            break

    return products


def is_individual_paint(product: dict) -> bool:
    """Filter out sets, accessories, and non-individual paints."""
    title = (product.get('title') or '').lower()
    handle = (product.get('handle') or '').lower()

    # Check exclusion keywords
    for keyword in EXCLUDE_KEYWORDS:
        if keyword in title or keyword in handle:
            return False

    # Must have a SKU starting with SFP3
    variants = product.get('variants', [])
    if variants:
        sku = (variants[0].get('sku') or '').upper()
        if sku.startswith('SFP3-'):
            return True

    return False


def normalize_name(title: str) -> str:
    """Normalize paint name by removing 'P3 Paints: ' prefix."""
    name = title.strip()
    # Remove "P3 Paints: " prefix
    if name.startswith('P3 Paints: '):
        name = name[len('P3 Paints: '):]
    elif name.startswith('P3 Paints:'):
        name = name[len('P3 Paints:'):]
    return name.strip()


def get_paint_type(name: str, sku: str) -> str:
    """Determine paint type from name and SKU."""
    sku_upper = sku.upper()

    # Medium
    if sku_upper in MEDIUM_SKUS or 'medium' in name.lower():
        return 'medium'

    # Metallic - use SKU ranges which are authoritative
    if sku_upper in METALLIC_SKUS:
        return 'metallic'

    return 'opaque'


def get_range_name(paint_type: str) -> str:
    """Determine range name from paint type."""
    if paint_type == 'metallic':
        return 'Formula P3 Metallic'
    return 'Formula P3'


def get_range_key(paint_type: str) -> str:
    """Determine range key from paint type."""
    if paint_type == 'metallic':
        return 'metallic'
    return 'standard'


def sample_color_from_image(img_url: str, verbose: bool = False) -> str:
    """Download image and sample the paint color from the background.

    P3 product images on Steamforged have the paint color as the
    background, with the bottle centered in the image. We sample
    from the corners/edges where the background is visible.
    """
    try:
        if not img_url:
            return None

        # Handle protocol-relative URLs
        if img_url.startswith('//'):
            img_url = 'https:' + img_url

        response = requests.get(img_url, headers=HEADERS, timeout=30)
        response.raise_for_status()

        img = Image.open(BytesIO(response.content)).convert('RGB')
        width, height = img.size

        # P3 images: bottle centered, background is the paint color
        # Sample from corners and edges where background is visible
        sample_regions = [
            # Top-left corner
            (int(width * 0.05), int(height * 0.05)),
            (int(width * 0.10), int(height * 0.10)),
            # Top-right corner
            (int(width * 0.95), int(height * 0.05)),
            (int(width * 0.90), int(height * 0.10)),
            # Bottom-left corner
            (int(width * 0.05), int(height * 0.95)),
            (int(width * 0.10), int(height * 0.90)),
            # Bottom-right corner
            (int(width * 0.95), int(height * 0.95)),
            (int(width * 0.90), int(height * 0.90)),
            # Mid-left edge
            (int(width * 0.05), int(height * 0.50)),
            # Mid-right edge
            (int(width * 0.95), int(height * 0.50)),
        ]

        # Collect all sampled colors
        all_colors = []
        for x, y in sample_regions:
            colors = []
            for dx in range(-5, 6, 2):
                for dy in range(-5, 6, 2):
                    px = max(0, min(x + dx, width - 1))
                    py = max(0, min(y + dy, height - 1))
                    colors.append(img.getpixel((px, py)))

            r = sum(c[0] for c in colors) // len(colors)
            g = sum(c[1] for c in colors) // len(colors)
            b = sum(c[2] for c in colors) // len(colors)
            all_colors.append((r, g, b))

        if all_colors:
            # Average all corner samples - they should all be the background
            r = sum(c[0] for c in all_colors) // len(all_colors)
            g = sum(c[1] for c in all_colors) // len(all_colors)
            b = sum(c[2] for c in all_colors) // len(all_colors)
            return "#{:02X}{:02X}{:02X}".format(r, g, b)

        return None

    except Exception as e:
        if verbose:
            print(f"        Error sampling color: {e}")
        return None


def slugify(name: str) -> str:
    """Convert name to URL-friendly slug."""
    slug = name.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug


def process_product(product: dict, sample_colors: bool = True, verbose: bool = False) -> dict:
    """Process a single product and return paint entry."""
    title = product.get('title', '')
    handle = product.get('handle', '')
    variants = product.get('variants', [])
    images = product.get('images', [])

    # Get SKU from first variant
    sku = variants[0].get('sku', '') if variants else ''

    # Get image URL (first image)
    img_url = images[0].get('src', '') if images else ''

    # Normalize name
    name = normalize_name(title)

    # Determine paint type
    paint_type = get_paint_type(name, sku)

    # Determine range
    range_name = get_range_name(paint_type)

    # Sample color from image
    hex_color = None
    if sample_colors and img_url:
        hex_color = sample_color_from_image(img_url, verbose)

    # Build brand data (empty for standard P3 paints)
    brand_data = {}

    # Create ID
    paint_id = f"p3-{slugify(name)}"

    # Build product URL
    url = f"{BASE_URL}/products/{handle}"

    return {
        "brand": "P3",
        "brandData": brand_data,
        "category": "",
        "discontinued": False,
        "hex": hex_color or "",
        "id": paint_id,
        "impcat": {"layerId": None, "shadeId": None},
        "name": name,
        "range": range_name,
        "sku": sku,
        "type": paint_type,
        "url": url
    }


def scrape_all(sample_colors: bool = True, verbose: bool = False, max_workers: int = 8) -> dict:
    """Scrape all P3 paints and categorize by range."""
    print("Fetching all P3 products from Steamforged...")

    products = get_all_products()
    print(f"Found {len(products)} total products")

    # Filter to individual paints only
    paint_products = [p for p in products if is_individual_paint(p)]
    print(f"Filtered to {len(paint_products)} individual paints")

    # Filter out mediums
    paint_products = [p for p in paint_products
                      if not (p.get('title', '').lower().endswith('mixing medium')
                              or (p.get('variants', [{}])[0].get('sku', '').upper() in MEDIUM_SKUS))]
    print(f"After removing mediums: {len(paint_products)} paints")

    # Process all products
    all_paints = []

    if sample_colors and max_workers > 1:
        print(f"Processing paints ({max_workers} threads)...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(process_product, p, True, verbose): p
                for p in paint_products
            }
            completed = 0
            for future in as_completed(futures):
                completed += 1
                try:
                    paint = future.result()
                    all_paints.append(paint)
                    if verbose or completed % 10 == 0 or completed == len(paint_products):
                        print(f"    [{completed}/{len(paint_products)}] {paint['name']}: {paint['hex']}")
                except Exception as e:
                    print(f"    Error processing product: {e}")
    else:
        for i, product in enumerate(paint_products):
            paint = process_product(product, sample_colors, verbose)
            all_paints.append(paint)
            if verbose:
                print(f"    [{i+1}/{len(paint_products)}] {paint['name']}: {paint['hex']}")

    # Categorize by range key
    ranges = {}
    for paint in all_paints:
        range_key = get_range_key(paint['type'])
        if range_key not in ranges:
            ranges[range_key] = {
                'name': paint['range'],
                'paints': []
            }
        ranges[range_key]['paints'].append(paint)

    # Sort paints within each range
    for range_data in ranges.values():
        range_data['paints'].sort(key=lambda x: x['name'].lower())

    return ranges


def main():
    parser = argparse.ArgumentParser(
        description='Scrape P3 (Formula P3) paint data with hex colors',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available ranges:
  standard        Formula P3 (standard acrylic paints)
  metallic        Formula P3 Metallic (metallic paints)

  all             Scrape everything
        """
    )
    parser.add_argument('--range', '-r', default='all',
                       help='Range to scrape (default: all)')
    parser.add_argument('--no-colors', action='store_true',
                       help='Skip color sampling')
    parser.add_argument('--workers', '-w', type=int, default=8,
                       help='Number of parallel threads for image sampling (default: 8)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')

    args = parser.parse_args()
    sample_colors = not args.no_colors

    print("Scraping P3 (Formula P3) paints...")
    data = scrape_all(sample_colors, args.verbose, args.workers)

    if args.range != 'all':
        # Filter to specific range
        range_key = args.range
        if range_key not in data:
            print(f"Unknown range: {range_key}")
            print(f"Available: {', '.join(data.keys())}")
            return
        data = {range_key: data[range_key]}

    # Generate separate files per range
    print(f"\nGenerating {len(data)} catalogue files:")
    total_paints = 0
    for range_key, range_data in data.items():
        output_file = RANGE_TO_FILE.get(range_key, f'p3_{range_key}.json')
        paints = range_data['paints']
        with open(output_file, 'w') as f:
            json.dump(paints, f, indent=2)
        print(f"  {output_file}: {len(paints)} paints")
        total_paints += len(paints)

    print(f"\nTotal: {total_paints} paints across {len(data)} ranges")


if __name__ == '__main__':
    main()
