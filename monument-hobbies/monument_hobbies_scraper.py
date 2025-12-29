#!/usr/bin/env python3
"""
Monument Hobbies Paint Scraper

Scrapes paint data from monumenthobbies.com and generates a catalogue
in standardized JSON format for the miniature paint database.

Supports:
- Pro Acryl paints (MPA-XXX)
- Expert Acrylics (MEA-XXX)
- Primers (MPAP-XXX)
- Sprays (MPAR-XXX)
- Mediums (MPAM-XXX)
- AMP Colors (AMP-XXX)

Usage:
    python monument_hobbies_scraper.py -g -w 8           # Generate with 8 workers
    python monument_hobbies_scraper.py -u existing.json # Update hex colors in existing file
    python monument_hobbies_scraper.py -a               # Update all .json files in directory
"""

import argparse
import glob
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import Optional

import requests
from PIL import Image

# Constants
BASE_URL = "https://monumenthobbies.com"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Collection URLs
COLLECTIONS = {
    'paint-singles': '/collections/paint-singles',
    'signature-series': '/collections/signature-series-paints',
    'fluorescents': '/collections/fluorescents',
    'washes': '/collections/washes',
    'primers': '/collections/pro-acryl-paints-primer',
    'metallics': '/collections/pro-acryl-paints-metallics',
    'mediums': '/collections/paint-mediums',
    'expert-acrylics': '/collections/expert-artist-acrylics',
}

# SKU pattern to category and paint type mapping
SKU_CATEGORY_MAP = [
    # Expert Acrylics
    (r'^MEA-\d+$', 'Expert Acrylics', 'opaque'),
    
    # AMP Colors
    (r'^AMP-0(11|12|23|24)$', 'AMP Colors', 'wash'),
    (r'^AMP-010$', 'AMP Colors', 'metallic'),
    (r'^AMP-\d+$', 'AMP Colors', 'opaque'),
    
    # Signature Series
    (r'^MPA-S24$', 'Signature Series', 'metallic'),
    (r'^MPA-S42$', 'Signature Series', 'metallic'),
    (r'^MPA-S\d+$', 'Signature Series', 'opaque'),
    
    # Fluorescents
    (r'^MPA-F\d+$', 'Fluorescents', 'opaque'),
    
    # Washes
    (r'^MPA-2\d{2}$', 'Washes', 'wash'),
    
    # Primers
    (r'^MPAP-\d+$', 'Primers', 'primer'),
    
    # Spray paints
    (r'^MPAR-P\d+$', 'Spray Primers', 'spray'),
    (r'^MPAR-V\d+$', 'Spray Varnishes', 'varnish'),
    (r'^MPAR-\d+$', 'Spray Paints', 'spray'),
    
    # Mediums
    (r'^MPAM-00[134]$', 'Mediums', 'thinner'),  # Glaze, Matte, Gloss
    (r'^MPAM-\d+$', 'Mediums', 'technical'),
    
    # Metallics (025-033)
    (r'^MPA-0(25|26|27|28|29|30|31|32|33)$', 'Metallics', 'metallic'),
    
    # Transparents (046-053 and 064)
    (r'^MPA-0(46|47|48|49|50|51|52|53|64)$', 'Transparents', 'transparent'),
    
    # Standard Colors (everything else MPA-0XX)
    (r'^MPA-0\d{2}$', 'Standard Colors', 'opaque'),
]

# Fallback colors for products where images don't show the paint color
FALLBACK_COLORS = {
    # Spray primers - cans don't show paint color
    'MPAR-P02': '#0A0A0A',  # Matte Black Primer
    'MPAR-P03': '#F5F5F5',  # Matte White Primer
    'MPAR-P05': '#5A5A5A',  # Matte Grey Primer
    # Spray varnishes
    'MPAR-V01': '#FFFFFF',  # Clear Matte Varnish (transparent)
}

# Signature Series artist mapping
SIGNATURE_ARTISTS = {
    range(1, 7): 'Vince Venturella',
    range(7, 13): 'Ninjon',
    range(13, 19): 'Ben Komets',
    range(19, 25): 'Matt Cexwish',
    range(25, 31): 'Flameon',
    range(31, 37): 'Rogue Hobbies',
    range(37, 43): 'AdeptiCon Spray-Team',
    range(49, 50): 'NOVA',
}


def get_session() -> requests.Session:
    """Create a requests session."""
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def fetch_page(session: requests.Session, url: str, retries: int = 3) -> Optional[str]:
    """Fetch a page with retry logic."""
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


def extract_meta_from_html(html: str) -> Optional[dict]:
    """Extract the var meta = {...} JSON from page HTML."""
    pattern = r'var\s+meta\s*=\s*(\{.*?\});'
    match = re.search(pattern, html, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def get_collection_products(session: requests.Session, collection_url: str, verbose: bool = False) -> list:
    """Fetch all products from a collection with pagination."""
    products = []
    page = 1
    
    while True:
        url = f"{BASE_URL}{collection_url}?page={page}"
        if verbose:
            print(f"    Fetching page {page}...", file=sys.stderr)
        
        html = fetch_page(session, url)
        if not html:
            break
        
        meta = extract_meta_from_html(html)
        if not meta or 'products' not in meta:
            break
        
        page_products = meta['products']
        if not page_products:
            break
        
        products.extend(page_products)
        
        if len(page_products) < 25:
            break
        
        page += 1
        time.sleep(0.3)
    
    return products


def clean_name(raw_name: str, sku: str) -> str:
    """Clean up the product name by removing SKU prefixes and brand text."""
    name = raw_name
    
    # Remove common prefixes - order matters, remove longer patterns first
    patterns = [
        # Pro Acryl variants with PRIME/Spray
        r'^PRO Acryl PRIME\s+\d+\s*-\s*',
        r'^Pro Acryl PRIME\s+\d+\s*-\s*',
        r'^PRO Acryl Spray\s*-\s*',
        r'^Pro Acryl Spray\s*-\s*',
        # Standalone PRIME/Spray (in case Pro Acryl was already removed)
        r'^PRIME\s+\d+\s*-\s*',
        r'^Spray\s*-\s*',
        # Standard Pro Acryl prefixes
        r'^\d{3}-Pro Acryl\s*',
        r'^[A-Z]\d{2}-Pro Acryl\s*',
        r'^\d{3}\s*-\s*Pro Acryl\s*',
        r'^Pro Acryl\s+',
        r'^PRO Acryl\s+',
        # AMP and Expert
        r'^AMP Colors\s+\d+\s*-\s*',
        r'^Expert Acrylics\s+\d+\s*-\s*',
    ]
    
    for pattern in patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)
    
    # For signature series, extract just the color name
    sig_match = re.match(r'^S\d{2}\s*-\s*(?:Vince Venturella|Ninjon|Ben Komets|Matt Cexwish|Flameon|Rogue Hobbies|Adepticon|NOVA)\s+(.+)$', name, re.IGNORECASE)
    if sig_match:
        name = sig_match.group(1)
    
    return name.strip()


def categorize_paint(sku: str) -> tuple:
    """Determine category and paint type from SKU."""
    for pattern, category, paint_type in SKU_CATEGORY_MAP:
        if re.match(pattern, sku):
            return category, paint_type
    return 'Unknown', 'opaque'


def get_signature_artist(sku: str) -> Optional[str]:
    """Get the artist name for a signature series paint."""
    match = re.match(r'^MPA-S(\d+)$', sku)
    if not match:
        return None
    
    num = int(match.group(1))
    for num_range, artist in SIGNATURE_ARTISTS.items():
        if num in num_range:
            return artist
    return None


def sample_color_swatch(img: Image.Image) -> str:
    """Sample color from center of a circular swatch image (Pro Acryl paints)."""
    img_rgb = img.convert('RGB')
    w, h = img_rgb.size
    cx, cy = w // 2, h // 2
    
    colors = []
    sample_range = min(20, w // 10, h // 10)
    for dx in range(-sample_range, sample_range + 1, 4):
        for dy in range(-sample_range, sample_range + 1, 4):
            px = max(0, min(cx + dx, w - 1))
            py = max(0, min(cy + dy, h - 1))
            colors.append(img_rgb.getpixel((px, py)))
    
    r = sum(c[0] for c in colors) // len(colors)
    g = sum(c[1] for c in colors) // len(colors)
    b = sum(c[2] for c in colors) // len(colors)
    
    return "#{:02X}{:02X}{:02X}".format(r, g, b)


def sample_color_bottle_label(img: Image.Image) -> str:
    """Sample color from bottle label area (primers).
    
    Primer bottles have:
    - Black label in center
    - Colored areas on sides and bottom (y=70-85%)
    - White background
    
    We need to find the actual paint color, avoiding black label and white background.
    """
    img_rgb = img.convert('RGB')
    w, h = img_rgb.size
    
    # Sample multiple positions in the label area
    colors = []
    
    # Sample at different y positions and x offsets
    for y_pct in [0.70, 0.75, 0.80, 0.85]:
        py = int(h * y_pct)
        # Sample from sides where the color usually is
        for x_offset in [-100, -50, 50, 100]:
            px = max(0, min(w // 2 + x_offset, w - 1))
            c = img_rgb.getpixel((px, py))
            r, g, b = c
            
            # Skip white/very light colors (background)
            if r > 220 and g > 220 and b > 220:
                continue
            # Skip black/very dark colors (label)
            if r < 30 and g < 30 and b < 30:
                continue
            # Skip grey colors (likely background/label edge)
            if abs(r - g) < 15 and abs(g - b) < 15 and abs(r - b) < 15:
                if r > 100 and r < 180:  # Medium grey
                    continue
            
            colors.append(c)
    
    if not colors:
        # If we couldn't find good colors, try sampling more aggressively
        for y_pct in [0.70, 0.80]:
            py = int(h * y_pct)
            for x_pct in [0.3, 0.35, 0.65, 0.7]:
                px = int(w * x_pct)
                c = img_rgb.getpixel((px, py))
                r, g, b = c
                if not (r > 200 and g > 200 and b > 200) and not (r < 40 and g < 40 and b < 40):
                    colors.append(c)
    
    if not colors:
        # Last resort - just sample center-ish area
        return sample_color_swatch(img)
    
    # Average the valid colors
    r = sum(c[0] for c in colors) // len(colors)
    g = sum(c[1] for c in colors) // len(colors)
    b = sum(c[2] for c in colors) // len(colors)
    
    return "#{:02X}{:02X}{:02X}".format(r, g, b)


def sample_color_expert(img: Image.Image) -> str:
    """Sample color from Expert Acrylics bottle (color at y=70% center area)."""
    img_rgb = img.convert('RGB')
    w, h = img_rgb.size
    cx = w // 2
    
    # Expert Acrylics show paint color at around y=70%
    colors = []
    for y_pct in [0.68, 0.70, 0.72]:
        py = int(h * y_pct)
        for dx in range(-30, 31, 15):
            px = max(0, min(cx + dx, w - 1))
            c = img_rgb.getpixel((px, py))
            # Skip white background
            if not (c[0] > 240 and c[1] > 240 and c[2] > 240):
                colors.append(c)
    
    if not colors:
        return sample_color_swatch(img)
    
    r = sum(c[0] for c in colors) // len(colors)
    g = sum(c[1] for c in colors) // len(colors)
    b = sum(c[2] for c in colors) // len(colors)
    
    return "#{:02X}{:02X}{:02X}".format(r, g, b)


def sample_color_spray(img: Image.Image) -> str:
    """Sample color from spray can image."""
    img_rgb = img.convert('RGB')
    w, h = img_rgb.size
    
    # Spray cans - sample various areas to find colored region
    colors = []
    for y_pct in [0.5, 0.6, 0.7]:
        for x_pct in [0.4, 0.5, 0.6]:
            px, py = int(w * x_pct), int(h * y_pct)
            c = img_rgb.getpixel((px, py))
            # Skip white/light grey backgrounds
            if not (c[0] > 220 and c[1] > 220 and c[2] > 220):
                colors.append(c)
    
    if not colors:
        return sample_color_swatch(img)
    
    r = sum(c[0] for c in colors) // len(colors)
    g = sum(c[1] for c in colors) // len(colors)
    b = sum(c[2] for c in colors) // len(colors)
    
    return "#{:02X}{:02X}{:02X}".format(r, g, b)


def sample_color_from_image(session: requests.Session, img_url: str, sku: str) -> Optional[str]:
    """Download image and sample the paint color using appropriate method."""
    try:
        response = session.get(img_url, timeout=30)
        response.raise_for_status()
        
        img = Image.open(BytesIO(response.content))
        
        # Choose sampling method based on SKU/image type
        if sku.startswith('MEA-'):
            return sample_color_expert(img)
        elif 'Brush-On' in img_url or 'BrushOn' in img_url:
            # Brush-on primers have swatch images like regular paints
            return sample_color_swatch(img)
        elif sku.startswith('MPAP-') or 'PRIME' in img_url.upper():
            return sample_color_bottle_label(img)
        elif sku.startswith('MPAR-') or 'Matte' in img_url or 'Spray' in img_url or 'Gloss' in img_url:
            return sample_color_spray(img)
        elif sku.startswith('MPAM-'):
            # Mediums - use swatch sampling, they have color circles
            return sample_color_swatch(img)
        else:
            return sample_color_swatch(img)
            
    except Exception as e:
        return None


def find_product_image(session: requests.Session, handle: str, sku: str) -> Optional[str]:
    """Fetch product page to find the main product image URL."""
    url = f"{BASE_URL}/products/{handle}"
    html = fetch_page(session, url)
    if not html:
        return None
    
    # Different patterns for different product types
    patterns = [
        r'cdn/shop/files/(MPA-[^"\']+\.png)',  # Pro Acryl swatch images
        r'cdn/shop/files/(AMP-[^"\']+\.png)',  # AMP swatch images
        r'cdn/shop/files/(MPAM-[^"\']+\.png)',  # Mediums
        r'cdn/shop/files/(MPAP-[^"\']+\.png)',  # Primers (MPAP prefix)
        r'cdn/shop/files/(MH-EAA[^"\']+\.png)',  # Expert Acrylics
        r'cdn/shop/files/(Pro_Acryl_PRIME[^"\']+\.png)',  # Primers (underscore)
        r'cdn/shop/files/(Pro-Acryl-PRIME[^"\']+\.png)',  # Primers (hyphen)
        r'cdn/shop/files/(Pro_Acryl[^"\']+\.png)',  # Other Pro Acryl products
        r'cdn/shop/files/(Matte[^"\']+\.png)',  # Spray cans
        r'cdn/shop/files/(Gloss[^"\']+\.png)',  # Varnish sprays
        r'cdn/shop/files/(PRO_Acryl[^"\']+\.png)',  # Alternative casing
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, html)
        if matches:
            for match in matches:
                if 'Monument' not in match and 'Icon' not in match:
                    return f"https://monumenthobbies.com/cdn/shop/files/{match}"
    
    return None


def get_color_for_product(session: requests.Session, product: dict, verbose: bool = False) -> tuple:
    """Get hex color for a product. Returns (sku, hex)."""
    if not product.get('variants'):
        return None, None
    
    variant = product['variants'][0]
    sku = variant.get('sku', '')
    handle = product.get('handle', '')
    
    if not sku or not handle:
        return None, None
    
    # Check for fallback color first (for sprays where cans don't show paint color)
    if sku in FALLBACK_COLORS:
        if verbose:
            print(f"    Using fallback for {sku}", file=sys.stderr)
        return sku, FALLBACK_COLORS[sku]
    
    if verbose:
        print(f"    Sampling {sku}...", file=sys.stderr)
    
    # Create a fresh session for this request to avoid threading issues
    local_session = get_session()
    
    img_url = find_product_image(local_session, handle, sku)
    if img_url:
        hex_color = sample_color_from_image(local_session, img_url, sku)
        if hex_color:
            return sku, hex_color
    
    return sku, None


def scrape_all_products(session: requests.Session, verbose: bool = False) -> list:
    """Scrape all products from all collections."""
    all_products = []
    seen_skus = set()
    
    for name, url in COLLECTIONS.items():
        print(f"  Fetching {name}...", file=sys.stderr)
        products = get_collection_products(session, url, verbose)
        print(f"    Found {len(products)} items", file=sys.stderr)
        
        for product in products:
            if product.get('variants'):
                sku = product['variants'][0].get('sku', '')
                if sku and sku not in seen_skus:
                    # Skip sets
                    if '-SET' in sku or sku.endswith('-Set'):
                        continue
                    # Skip basing textures
                    if sku.startswith('MPA-T'):
                        continue
                    
                    seen_skus.add(sku)
                    all_products.append(product)
    
    return all_products


def scrape_colors_parallel(session: requests.Session, products: list, max_workers: int = 8, verbose: bool = False) -> dict:
    """Scrape hex colors for products in parallel. Returns {sku: hex}."""
    colors = {}
    
    print(f"  Sampling colors with {max_workers} workers...", file=sys.stderr)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(get_color_for_product, session, p, verbose): p
            for p in products
        }
        
        for i, future in enumerate(as_completed(futures), 1):
            sku, hex_color = future.result()
            if sku and hex_color:
                colors[sku] = hex_color
            
            if i % 20 == 0:
                print(f"    Processed {i}/{len(products)}", file=sys.stderr)
    
    return colors


def get_range_for_sku(sku: str) -> str:
    """Get the range name for a SKU."""
    if sku.startswith('MEA-'):
        return 'Expert Acrylics'
    elif sku.startswith('AMP-'):
        return 'AMP Colors'
    else:
        return 'Pro Acryl'


def generate_catalogue(products: list, colors: dict) -> list:
    """Generate catalogue in standard format."""
    catalogue = []
    
    for product in products:
        if not product.get('variants'):
            continue
        
        variant = product['variants'][0]
        sku = variant.get('sku', '')
        
        if not sku:
            continue
        
        raw_name = variant.get('name', '')
        name = clean_name(raw_name, sku)
        handle = product.get('handle', '')
        
        category, paint_type = categorize_paint(sku)
        range_name = get_range_for_sku(sku)
        
        # Build brand data
        brand_data = {}
        artist = get_signature_artist(sku)
        if artist:
            brand_data['artist'] = artist
        
        entry = {
            "brand": "Monument Hobbies",
            "brandData": brand_data,
            "category": category,
            "discontinued": False,
            "hex": colors.get(sku, ''),
            "id": f"monument-hobbies-{sku.lower()}",
            "impcat": {"layerId": None, "shadeId": None},
            "name": name,
            "range": range_name,
            "sku": sku,
            "type": paint_type,
            "url": f"{BASE_URL}/products/{handle}"
        }
        catalogue.append(entry)
    
    # Sort by SKU
    def sku_sort_key(item):
        sku = item['sku']
        match = re.match(r'^([A-Z]+)-([A-Z]?)(\d+)$', sku)
        if match:
            prefix, letter, num = match.groups()
            return (prefix, letter or '', int(num))
        return (sku, '', 0)
    
    catalogue.sort(key=sku_sort_key)
    return catalogue


def update_existing_json(filepath: str, colors: dict) -> list:
    """Update hex colors in an existing JSON file."""
    with open(filepath, 'r') as f:
        data = json.load(f)
    
    updated = 0
    for paint in data:
        sku = paint.get('sku', '')
        if sku in colors and colors[sku]:
            if paint.get('hex') != colors[sku]:
                paint['hex'] = colors[sku]
                updated += 1
    
    print(f"    Updated {updated} colors in {filepath}", file=sys.stderr)
    return data


def main():
    parser = argparse.ArgumentParser(
        description='Scrape Monument Hobbies paint data'
    )
    parser.add_argument('--output', '-o', default='monument_hobbies.json',
                       help='Output JSON file (default: monument_hobbies.json)')
    parser.add_argument('--update-json', '-u', metavar='FILE',
                       help='Update existing JSON file with scraped hex colors')
    parser.add_argument('--update-all', '-a', action='store_true',
                       help='Update ALL monument*.json files in current directory')
    parser.add_argument('--no-colors', action='store_true',
                       help='Skip color sampling from images')
    parser.add_argument('--workers', '-w', type=int, default=8,
                       help='Number of parallel threads for image sampling (default: 8)')
    parser.add_argument('--generate', '-g', action='store_true',
                       help='Generate fresh catalogue file')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    
    args = parser.parse_args()
    
    session = get_session()
    sample_colors = not args.no_colors
    
    print("Scraping Monument Hobbies paints...", file=sys.stderr)
    
    # Scrape all products
    products = scrape_all_products(session, args.verbose)
    print(f"\nTotal unique products: {len(products)}", file=sys.stderr)
    
    # Sample colors if requested
    colors = {}
    if sample_colors:
        colors = scrape_colors_parallel(session, products, args.workers, args.verbose)
        print(f"  Sampled {len(colors)} colors", file=sys.stderr)
    
    # Handle different output modes
    if args.update_all:
        json_files = glob.glob('monument*.json')
        if not json_files:
            print("No monument*.json files found in current directory", file=sys.stderr)
            return
        
        for filepath in json_files:
            updated = update_existing_json(filepath, colors)
            with open(filepath, 'w') as f:
                json.dump(updated, f, indent=2)
        
        print(f"\nUpdated {len(json_files)} files", file=sys.stderr)
    
    elif args.update_json:
        updated = update_existing_json(args.update_json, colors)
        with open(args.update_json, 'w') as f:
            json.dump(updated, f, indent=2)
        print(f"\nUpdated: {args.update_json}", file=sys.stderr)
    
    elif args.generate or not (args.update_json or args.update_all):
        catalogue = generate_catalogue(products, colors)
        
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(catalogue, f, indent=2, ensure_ascii=False)
        
        print(f"\nSaved {len(catalogue)} paints to {args.output}", file=sys.stderr)
        
        # Print summary by category
        categories = {}
        for paint in catalogue:
            cat = paint['category']
            categories[cat] = categories.get(cat, 0) + 1
        
        print("\nPaints by category:", file=sys.stderr)
        for cat, count in sorted(categories.items()):
            print(f"  {cat}: {count}", file=sys.stderr)
        
        # Print summary by range
        ranges = {}
        for paint in catalogue:
            r = paint['range']
            ranges[r] = ranges.get(r, 0) + 1
        
        print("\nPaints by range:", file=sys.stderr)
        for r, count in sorted(ranges.items()):
            print(f"  {r}: {count}", file=sys.stderr)
        
        # Print hex coverage
        with_hex = sum(1 for p in catalogue if p.get('hex'))
        print(f"\nHex colors: {with_hex}/{len(catalogue)} ({100*with_hex//len(catalogue) if catalogue else 0}%)", file=sys.stderr)


if __name__ == '__main__':
    main()
