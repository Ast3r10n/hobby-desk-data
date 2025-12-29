#!/usr/bin/env python3
"""
AK Interactive 3rd Gen Paint Scraper

Scrapes ak-interactive.com to build a paint database with hex colors.
Run locally where there are no proxy restrictions.

Requirements:
    pip install requests beautifulsoup4 pillow

Usage:
    python ak_paint_scraper.py [--range RANGE_NAME] [--output OUTPUT_FILE] [--update-json JSON_FILE]
    
Examples:
    python ak_paint_scraper.py --range 3gen-color-punch
    python ak_paint_scraper.py --range all --output ak_paints.json
    python ak_paint_scraper.py --range standard --update-json ak_3rd_gen_standard.json
"""

import argparse
import html
import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from io import BytesIO
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image

# All 3rd Gen color ranges from the website filter
COLOR_RANGES_3GEN = {
    "3gen-color-punch": "Color Punch",
    "general": "General", 
    "afv": "AFV",
    "air": "AIR",
    "acrylic-effect": "Effect",
    "fantasy": "Fantasy",
    "figures": "Figures",
    "intense": "Intense",
    "metallic": "Metallic",
    "pastel": "Pastel",
    "standard": "Standard",
    "wargame": "Wargame"
}

# Other product lines - each has its own base URL
OTHER_PRODUCTS = {
    "quick-gen": {
        "name": "Quick Gen",
        "url": "https://ak-interactive.com/product-category/paints/paints-acrylics/quick-gen/"
    },
    "real-colors": {
        "name": "Real Colors",
        "url": "https://ak-interactive.com/product-category/real-colors-en/"
    },
    "rc-markers": {
        "name": "Real Colors Markers",
        "url": "https://ak-interactive.com/product-category/paints/rc-markers/"
    },
    "playmarkers": {
        "name": "Playmarkers",
        "url": "https://ak-interactive.com/product-category/ak-playmarkers/"
    },
    "deep-shades": {
        "name": "Deep Shades",
        "url": "https://ak-interactive.com/product-category/paints/paints-acrylics/deep-shades/"
    },
    "the-inks": {
        "name": "The Inks",
        "url": "https://ak-interactive.com/product-category/paints/paints-acrylics-paints-for-modeling/paint-acrylic-inks/"
    },
    "acrylic-wash": {
        "name": "Acrylic Wash", 
        "url": "https://ak-interactive.com/product-category/paints/paints-acrylics/acrylic-wash/"
    }
}

# Combined for convenience
COLOR_RANGES = {**COLOR_RANGES_3GEN, **{k: v["name"] for k, v in OTHER_PRODUCTS.items()}}

# Paint type mapping (matches TypeScript PaintType)
RANGE_TO_TYPE = {
    # 3GEN sub-ranges
    'standard': 'opaque',
    'general': 'opaque',
    'afv': 'opaque',
    'figures': 'opaque',
    'intense': 'opaque',
    'pastel': 'opaque',
    'wargame': 'opaque',
    '3gen-color-punch': 'opaque',
    'acrylic-effect': 'opaque',
    'fantasy': 'opaque',
    'air': 'air',
    'metallic': 'metallic',
    # Other ranges
    'quick-gen': 'contrast',
    'real-colors': 'opaque',
    'rc-markers': 'opaque',
    'playmarkers': 'opaque',
    'deep-shades': 'wash',
    'the-inks': 'ink',
    'acrylic-wash': 'wash',
}


def get_paint_type(paint: dict, range_type: str) -> str:
    """Determine paint type, checking name for overrides."""
    name = (paint.get('title') or '').lower()
    
    # Check for type keywords in name
    if 'varnish' in name:
        return 'varnish'
    if 'thinner' in name:
        return 'thinner'
    if 'primer' in name:
        return 'primer'
    # Only match 'medium' when it's not part of a color name like "Medium Blue"
    # Look for patterns like "medium for", "gen medium", or just "medium" at end
    if re.search(r'\bmedium\s+(for|gen|paint)|medium$', name):
        return 'technical'
    if 'metallic' in name or 'metal' in name:
        return 'metallic'
    
    return range_type


def get_category(color_range: str) -> str:
    """Get category for a range. Only 3rd gen sub-ranges get categories."""
    if color_range in COLOR_RANGES_3GEN:
        return COLOR_RANGES_3GEN[color_range]
    return ''

BASE_URL_3GEN = "https://ak-interactive.com/product-category/paints/paints-acrylics/3rd-acrylics/"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# Words that indicate non-paint products - exclude these
# Sets are fetched dynamically from all product category pages

# Sets filter parameter (applied to any product category URL)
SETS_FILTER = "pa_product-pack-units=product-pack-set,product-pack-full-range,product-pack"

# Base URLs to check for sets (all product categories)
SETS_BASE_URLS = [
    "https://ak-interactive.com/product-category/paints/paints-acrylics/",  # 3rd Gen
    "https://ak-interactive.com/product-category/real-colors-en/",  # Real Colors
    "https://ak-interactive.com/product-category/paints/rc-markers/",  # RC Markers
    "https://ak-interactive.com/product-category/ak-playmarkers/",  # Playmarkers
    "https://ak-interactive.com/product-category/paints/paints-acrylics/deep-shades/",  # Deep Shades
    "https://ak-interactive.com/product-category/paints/paints-acrylics-paints-for-modeling/paint-acrylic-inks/",  # The Inks
    "https://ak-interactive.com/product-category/paints/paints-acrylics/acrylic-wash/",  # Acrylic Wash
    "https://ak-interactive.com/product-category/paints/paints-acrylics/quick-gen/",  # Quick Gen
]

# Cache for set SKUs (populated by fetch_set_skus)
_SET_SKUS_CACHE = set()

# Cache file for set SKUs
SET_SKUS_CACHE_FILE = Path(__file__).parent / '.ak_set_skus_cache.json'


def fetch_sets_from_url(base_url: str, verbose: bool = False) -> set:
    """Fetch set SKUs from a single product category URL."""
    set_skus = set()
    page = 1
    max_pages = 50
    
    # Valid SKU pattern
    valid_sku_pattern = re.compile(r'^(AK\d+|RCS\d+|RCM\d+|RC\d+|AKM\d+)$', re.IGNORECASE)
    
    while page <= max_pages:
        # Build URL with sets filter
        sep = '&' if '?' in base_url else '?'
        if page == 1:
            url = f"{base_url}{sep}{SETS_FILTER}"
        else:
            # Insert page into URL
            if '?' in base_url:
                url = f"{base_url.rstrip('/')}page/{page}/?{SETS_FILTER}"
            else:
                url = f"{base_url.rstrip('/')}/page/{page}/?{SETS_FILTER}"
        
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            
            if response.status_code == 404:
                break
            
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            page_skus = set()
            for product in soup.select('li.product'):
                sku = None
                
                # Method 1: From c-loop__sku element
                sku_elem = product.select_one('.c-loop__sku, p.c-loop__sku')
                if sku_elem:
                    sku = sku_elem.get_text(strip=True).upper()
                
                # Method 2: From data-product_sku attribute
                if not sku:
                    add_btn = product.select_one('[data-product_sku]')
                    if add_btn:
                        sku = add_btn.get('data-product_sku', '').upper()
                
                # Method 3: From product link URL
                if not sku:
                    link = product.select_one('a[href*="/product/"]')
                    if link:
                        href = link.get('href', '')
                        match = re.search(r'/product/(ak\d+|rcs\d+|rcm\d+|rc\d+|akm\d+)', href, re.IGNORECASE)
                        if match:
                            sku = match.group(1).upper()
                
                if sku and valid_sku_pattern.match(sku):
                    page_skus.add(sku)
            
            if not page_skus:
                break
            
            set_skus.update(page_skus)
            if verbose:
                print(f"    Page {page}: {len(page_skus)} SKUs")
            
            page += 1
            time.sleep(0.3)
            
        except requests.RequestException as e:
            if verbose:
                print(f"    Error on page {page}: {e}")
            break
    
    return set_skus


def fetch_set_skus(verbose: bool = False, force_refresh: bool = False) -> set:
    """
    Fetch all set SKUs from all product categories.
    Results are cached to disk for 24 hours.
    """
    global _SET_SKUS_CACHE
    
    if _SET_SKUS_CACHE and not force_refresh:
        return _SET_SKUS_CACHE
    
    # Try loading from cache file (valid for 24 hours)
    if SET_SKUS_CACHE_FILE.exists() and not force_refresh:
        try:
            cache_age = time.time() - SET_SKUS_CACHE_FILE.stat().st_mtime
            if cache_age < 86400:  # 24 hours
                with open(SET_SKUS_CACHE_FILE) as f:
                    cached = json.load(f)
                _SET_SKUS_CACHE = set(cached)
                if verbose:
                    print(f"Loaded {len(_SET_SKUS_CACHE)} set SKUs from cache")
                return _SET_SKUS_CACHE
        except (json.JSONDecodeError, IOError):
            pass
    
    if verbose:
        print("Fetching set/pack SKUs for exclusion...")
    
    all_set_skus = set()
    
    for base_url in SETS_BASE_URLS:
        if verbose:
            # Extract category name from URL
            category = base_url.rstrip('/').split('/')[-1]
            print(f"  Checking {category}...")
        
        skus = fetch_sets_from_url(base_url, verbose)
        all_set_skus.update(skus)
    
    if verbose:
        print(f"  Total set SKUs to exclude: {len(all_set_skus)}")
    
    # Save to cache file
    if all_set_skus:
        try:
            with open(SET_SKUS_CACHE_FILE, 'w') as f:
                json.dump(list(all_set_skus), f)
        except IOError:
            pass
    
    _SET_SKUS_CACHE = all_set_skus
    return all_set_skus


def is_set_sku(sku: str) -> bool:
    """Check if SKU is in the fetched sets list."""
    if not sku:
        return False
    return sku.upper() in _SET_SKUS_CACHE


def normalize_sku(sku: str) -> str:
    """Normalize SKU for matching - remove spaces, uppercase."""
    if not sku:
        return ''
    return re.sub(r'\s+', '', sku.upper())


def normalize_name(name: str) -> str:
    """Normalize paint name for fuzzy matching."""
    if not name:
        return ''
    # Lowercase, remove special chars, collapse spaces
    name = name.lower()
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    # Remove common filler words
    for word in ['ak', 'interactive', 'acrylic', 'paint', 'color', 'colour']:
        name = re.sub(rf'\b{word}\b', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def to_sentence_case(name: str) -> str:
    """Convert name to sentence case: 'WOOD BROWN – INK' -> 'Wood Brown – Ink'"""
    if not name:
        return name
    # Handle ALL CAPS or mixed case - convert to title case
    # But preserve certain words as lowercase (articles, prepositions)
    words = name.split()
    result = []
    for i, word in enumerate(words):
        # Keep dashes and special chars, just case the letters
        if word.upper() == word or word.lower() == word:
            # All caps or all lower - convert to title
            word = word.title()
        result.append(word)
    return ' '.join(result)


def clean_paint_name(name: str) -> str:
    """Clean paint name - remove range/category suffix after last dash and size suffix."""
    if not name:
        return name
    # Remove suffix after last en-dash or hyphen (often contains range name)
    # "Gold – Quick Gen Color" -> "Gold"
    # "Desert Uniform Base – Figures" -> "Desert Uniform Base"
    # "Ral 6003 – Afv" -> "Ral 6003"
    for sep in [' – ', '- ', ' - ', ' — ', '— ']:
        if sep in name:
            parts = name.rsplit(sep, 1)
            if len(parts) == 2:
                suffix_lower = parts[1].lower()
                # Strip if suffix is a range/category/marketing name
                strip_suffixes = [
                    'color', 'gen', 'shade', 'ink', 'wash', 'marker', 'real',
                    'figures', 'afv', 'air',  # 3gen categories
                    'standard', 'intense', 'metallic', 'pastel', 'auxiliary',
                    'efecto', 'lino', 'wargame',
                ]
                if any(x in suffix_lower for x in strip_suffixes):
                    name = parts[0].strip()
                    break
    # Remove size suffixes
    name = re.sub(r'\s*\(\d+\s*ml\)\s*$', '', name, flags=re.IGNORECASE).strip()
    name = re.sub(r'\s+\d+\s*ml\s*$', '', name, flags=re.IGNORECASE).strip()
    return name


def is_paint_product(paint: dict) -> bool:
    """Filter out non-paint products (sets, bundles, etc.)."""
    sku = (paint.get('sku') or '').upper()
    
    # Only allow valid SKU formats (must have digits)
    if not re.match(r'^(AK\d+|RCS\d+|RCM\d+|RC\d+|AKM\d+)$', sku, re.IGNORECASE):
        return False
    
    # Exclude if SKU is in the fetched sets list
    if is_set_sku(sku):
        return False
    
    return True


def get_base_name(name: str) -> str:
    """Get base name without size/range suffix for deduplication."""
    if not name:
        return ''
    # Use clean_paint_name to strip suffixes, then lowercase for comparison
    name = clean_paint_name(name)
    return name.lower()


def dedupe_by_name(paints: list) -> list:
    """Remove duplicate paints (same SKU appearing multiple times)."""
    seen_skus = set()
    result = []
    
    for paint in paints:
        sku = paint.get('sku') or ''
        
        if sku and sku in seen_skus:
            # Skip duplicate SKU
            continue
        
        if sku:
            seen_skus.add(sku)
        result.append(paint)
    
    return result


def get_page_url(color_range: str, page: int = 1) -> str:
    """Build URL for a specific color range and page number."""
    if color_range in OTHER_PRODUCTS:
        # Other product lines have their own base URLs
        base = OTHER_PRODUCTS[color_range]["url"]
        if page == 1:
            return base
        else:
            return f"{base}page/{page}/"
    else:
        # 3rd Gen ranges use filter parameter
        if page == 1:
            return f"{BASE_URL_3GEN}?pa_3rd-color-range={color_range}"
        else:
            return f"{BASE_URL_3GEN}page/{page}/?pa_3rd-color-range={color_range}"


def fetch_page(url: str) -> BeautifulSoup:
    """Fetch a page and return BeautifulSoup object."""
    print(f"    Fetching: {url}")
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def extract_paints_from_page(soup: BeautifulSoup) -> list:
    """Extract paint data from a category page."""
    paints = []
    seen_skus = set()
    
    # Pattern 1: List items with product links (WooCommerce standard)
    for item in soup.select('li.product'):
        try:
            link = item.select_one('a.woocommerce-LoopProduct-link, a[href*="/product/"]')
            title_elem = item.select_one('.woocommerce-loop-product__title, h2')
            sku_elem = item.select_one('.sku')
            img_elem = item.select_one('img')
            
            title = title_elem.get_text(strip=True) if title_elem else None
            if not title and img_elem:
                title = img_elem.get('alt')
            if title:
                title = html.unescape(title)
                title = to_sentence_case(title)
                title = clean_paint_name(title)
            product_url = link.get('href') if link else None
            img_url = img_elem.get('src') if img_elem else None
            
            sku = None
            if sku_elem:
                sku = sku_elem.get_text(strip=True)
            elif img_url:
                match = re.search(r'(AK\d+)', img_url, re.IGNORECASE)
                if match:
                    sku = match.group(1).upper()
            
            if sku and sku not in seen_skus:
                seen_skus.add(sku)
                paints.append({
                    'title': title,
                    'sku': sku,
                    'img_url': img_url,
                    'product_url': product_url
                })
        except Exception as e:
            print(f"      Warning: Error parsing product item: {e}")
    
    # Pattern 2: Custom AK theme structure (c-loop__enlace)
    for link in soup.select('a.c-loop__enlace'):
        try:
            title_elem = link.select_one('p.c-loop__title')
            sku_elem = link.select_one('p.c-loop__sku')
            img_elem = link.select_one('div.product-thumbnail img, img')
            
            # Prefer data-title attribute, fallback to text content, then image alt
            title = None
            if title_elem:
                title = title_elem.get('data-title') or title_elem.get_text(strip=True)
            if not title and img_elem:
                title = img_elem.get('alt')
            # Decode HTML entities and apply sentence case
            if title:
                title = html.unescape(title)
                title = to_sentence_case(title)
                title = clean_paint_name(title)
            sku = sku_elem.get_text(strip=True) if sku_elem else None
            img_url = img_elem.get('src') if img_elem else None
            product_url = link.get('href')
            
            if sku and sku not in seen_skus:
                seen_skus.add(sku)
                paints.append({
                    'title': title,
                    'sku': sku,
                    'img_url': img_url,
                    'product_url': product_url
                })
        except Exception as e:
            print(f"      Warning: Error parsing c-loop product: {e}")
    
    return paints


def has_next_page(soup: BeautifulSoup) -> bool:
    """Check if there's a next page of results."""
    return soup.select_one('a.next.page-numbers') is not None


def sample_color_from_image(img_url: str, verbose: bool = False, range_hint: str = '') -> str:
    """Download image and sample the paint color.
    
    Different ranges have different image layouts:
    - Standard paints: color in bottle cap/top area
    - Washes/Deep Shades: color in large background circle
    - Markers: color in marker body/tip area
    """
    try:
        response = requests.get(img_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        
        img = Image.open(BytesIO(response.content)).convert('RGB')
        width, height = img.size
        
        # Choose sampling regions based on range type
        if range_hint == 'acrylic-wash':
            # Washes: color is in the large background circle behind the bottle
            # Target around pixel (245, 610) area - left side of circle
            sample_regions = [
                (245, 610),                      # User-specified sweet spot
                (200, 580),                      # Nearby left
                (280, 620),                      # Nearby right
                (220, 550),                      # Upper left of circle
                (260, 650),                      # Lower right of circle
            ]
        elif range_hint == 'deep-shades':
            # Deep Shades: color is in the bottle, sample from lower portion
            # near the "FOR WARGAMERS" band
            sample_regions = [
                (width // 2, 4 * height // 5),   # Bottom center
                (width // 2, 3 * height // 4),   # Lower center
                (width // 3, 4 * height // 5),   # Bottom left
                (2 * width // 3, 4 * height // 5), # Bottom right
                (width // 2, 7 * height // 10),  # Mid-lower
            ]
        elif range_hint == 'playmarkers':
            # Playmarkers: color is in paint strokes on right side
            sample_regions = [
                (4 * width // 5, height // 2),   # Right side, center
                (7 * width // 8, height // 2),   # Far right, center
                (4 * width // 5, 2 * height // 5), # Right side, upper
                (7 * width // 8, 2 * height // 5), # Far right, upper
                (4 * width // 5, 3 * height // 5), # Right side, lower
                (7 * width // 8, 3 * height // 5), # Far right, lower
            ]
        elif range_hint == 'rc-markers':
            # RC Markers detail image: color swatch in center/upper area
            sample_regions = [
                (width // 2, height // 3),       # Center upper
                (width // 2, height // 4),       # Center top
                (width // 3, height // 3),       # Left upper
                (2 * width // 3, height // 3),   # Right upper
                (width // 2, height // 2),       # Center
            ]
        else:
            # Standard paints: color in bottle cap/top area
            sample_regions = [
                (width // 2, height // 5),
                (width // 3, height // 5),
                (2 * width // 3, height // 5),
                (width // 2, height // 4),
                (width // 3, height // 4),
                (2 * width // 3, height // 4),
                (width // 2, height // 3),
                (width // 3, height // 3),
                (2 * width // 3, height // 3),
            ]
        
        best_color = None
        best_score = -1
        
        for x, y in sample_regions:
            # Sample 10x10 region
            colors = []
            for dx in range(-5, 6, 2):
                for dy in range(-5, 6, 2):
                    px = max(0, min(x + dx, width - 1))
                    py = max(0, min(y + dy, height - 1))
                    colors.append(img.getpixel((px, py)))
            
            r = sum(c[0] for c in colors) // len(colors)
            g = sum(c[1] for c in colors) // len(colors)
            b = sum(c[2] for c in colors) // len(colors)
            
            # Score: prefer saturated, mid-brightness colors
            max_c = max(r, g, b)
            min_c = min(r, g, b)
            saturation = (max_c - min_c) / max(max_c, 1) if max_c > 0 else 0
            brightness = (r + g + b) / 3
            
            if brightness > 245 or brightness < 10:
                continue
            
            brightness_penalty = abs(brightness - 127) / 127
            score = saturation * (1 - brightness_penalty * 0.5)
            
            if score > best_score:
                best_score = score
                best_color = (r, g, b)
        
        if best_color:
            hex_color = "#{:02X}{:02X}{:02X}".format(*best_color)
            if verbose:
                print(f"        -> {hex_color} (score: {best_score:.3f})")
            return hex_color
        
        # Fallback
        r, g, b = img.getpixel((width // 2, height // 4))
        return "#{:02X}{:02X}{:02X}".format(r, g, b)
        
    except Exception as e:
        print(f"        Error: {e}")
        return None


def sample_paint_color(paint: dict, verbose: bool = False, range_hint: str = '') -> dict:
    """Sample color for a single paint. Returns the paint dict with hex added."""
    img_url = paint.get('img_url')
    
    # RC Markers have a separate detail image with the color swatch
    if range_hint == 'rc-markers' and paint.get('sku'):
        sku = paint['sku'].upper()
        img_url = f"https://ak-interactive.com/wp-content/uploads/2024/06/{sku}_detail.jpg"
    
    if img_url:
        paint['hex'] = sample_color_from_image(img_url, verbose, range_hint)
    return paint


def scrape_color_range(color_range: str, sample_colors: bool = True, verbose: bool = False, max_workers: int = 8) -> list:
    """Scrape all paints from a color range."""
    range_name = COLOR_RANGES.get(color_range, color_range)
    print(f"\n{'='*60}")
    print(f"Scraping: {range_name} ({color_range})")
    print('='*60)
    
    all_paints = []
    page = 1
    
    while page <= 50:  # Safety limit
        url = get_page_url(color_range, page)
        
        try:
            soup = fetch_page(url)
            paints = extract_paints_from_page(soup)
            
            # Filter out non-paint products
            before_filter = len(paints)
            filtered_out = [p for p in paints if not is_paint_product(p)]
            paints = [p for p in paints if is_paint_product(p)]
            
            # Dedupe size variants (keep first occurrence)
            before_dedupe = len(paints)
            paints = dedupe_by_name(paints)
            if len(paints) < before_dedupe and verbose:
                print(f"      Deduped {before_dedupe - len(paints)} size variants")
            
            if filtered_out and verbose:
                print(f"      Filtered out {len(filtered_out)}: {', '.join(p.get('sku', '?') for p in filtered_out)}")
            
            # Break only if page had no products at all (before filtering)
            if before_filter == 0:
                break
            
            if paints:
                print(f"    Page {page}: {len(paints)} paints")
                
                if sample_colors:
                    # Parallel color sampling
                    print(f"    Sampling colors ({max_workers} threads)...")
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = {executor.submit(sample_paint_color, paint, verbose, color_range): paint for paint in paints}
                        completed = 0
                        for future in as_completed(futures):
                            completed += 1
                            paint = future.result()
                            sku = paint.get('sku') or '?'
                            hex_val = paint.get('hex') or 'failed'
                            if verbose or completed % 10 == 0 or completed == len(paints):
                                print(f"      [{completed}/{len(paints)}] {sku}: {hex_val}")
                
                # Add category and type from range key
                category = get_category(color_range)
                range_type = RANGE_TO_TYPE.get(color_range, '')
                for paint in paints:
                    paint['category'] = category
                    paint['paint_type'] = get_paint_type(paint, range_type)
                
                all_paints.extend(paints)
            
            if not has_next_page(soup):
                break
            
            page += 1
            time.sleep(0.3)
            
        except Exception as e:
            print(f"    Error on page {page}: {e}")
            break
    
    print(f"  Total: {len(all_paints)} paints")
    return all_paints


def cross_reference_rc_markers(marker_paints: list, real_colors_paints: list) -> list:
    """Cross-reference RC Markers with Real Colors to get hex values by name match."""
    # Build name -> hex lookup from Real Colors
    rc_name_to_hex = {}
    for paint in real_colors_paints:
        name = (paint.get('title') or '').lower().strip()
        if name and paint.get('hex'):
            # Clean the name for matching
            name = clean_paint_name(name).lower()
            rc_name_to_hex[name] = paint['hex']
    
    matched = 0
    for marker in marker_paints:
        if not marker.get('hex'):
            marker_name = (marker.get('title') or '').lower().strip()
            marker_name = clean_paint_name(marker_name).lower()
            
            if marker_name in rc_name_to_hex:
                marker['hex'] = rc_name_to_hex[marker_name]
                matched += 1
    
    if matched:
        print(f"    Cross-referenced {matched} RC Markers with Real Colors")
    
    return marker_paints


def scrape_all_ranges(sample_colors: bool = True, verbose: bool = False, max_workers: int = 8, range_workers: int = 1) -> dict:
    """Scrape all color ranges, optionally in parallel."""
    all_data = {}
    
    if range_workers > 1:
        print(f"\nScraping {len(COLOR_RANGES)} ranges in parallel ({range_workers} concurrent)...")
        with ThreadPoolExecutor(max_workers=range_workers) as executor:
            futures = {
                executor.submit(scrape_color_range, range_key, sample_colors, verbose, max_workers): range_key 
                for range_key in COLOR_RANGES.keys()
            }
            for future in as_completed(futures):
                range_key = futures[future]
                try:
                    paints = future.result()
                    all_data[range_key] = {
                        'name': COLOR_RANGES[range_key],
                        'paints': paints
                    }
                except Exception as e:
                    print(f"  Error scraping {range_key}: {e}")
        
        # Cross-reference RC Markers with Real Colors
        if 'rc-markers' in all_data and 'real-colors' in all_data:
            print("\n  Cross-referencing RC Markers with Real Colors...")
            cross_reference_rc_markers(
                all_data['rc-markers']['paints'],
                all_data['real-colors']['paints']
            )
    else:
        for range_key, range_name in COLOR_RANGES.items():
            paints = scrape_color_range(range_key, sample_colors, verbose, max_workers)
            all_data[range_key] = {
                'name': range_name,
                'paints': paints
            }
            time.sleep(1)
    
    # Cross-reference RC Markers with Real Colors
    if 'rc-markers' in all_data and 'real-colors' in all_data:
        print("\n  Cross-referencing RC Markers with Real Colors...")
        cross_reference_rc_markers(
            all_data['rc-markers']['paints'],
            all_data['real-colors']['paints']
        )
    
    return all_data


def update_existing_json(json_path: str, scraped_data: list) -> list:
    """Update existing JSON with scraped hex colors by matching SKU."""
    with open(json_path, 'r') as f:
        existing = json.load(f)
    
    # Build SKU -> hex lookup
    sku_to_hex = {}
    for paint in scraped_data:
        if paint.get('sku') and paint.get('hex'):
            sku_to_hex[normalize_sku(paint['sku'])] = paint['hex']
    
    # Handle both formats: plain list or dict with 'paints' key
    if isinstance(existing, list):
        paint_list = existing
    elif isinstance(existing, dict) and 'paints' in existing:
        paint_list = existing['paints']
    else:
        print(f"  Unrecognized format")
        return existing
    
    # Update existing entries
    updated = 0
    for paint in paint_list:
        sku = normalize_sku(paint.get('sku', ''))
        if sku in sku_to_hex:
            paint['hex'] = sku_to_hex[sku]
            updated += 1
    
    print(f"  Updated {updated} paints with hex colors")
    return existing


def generate_catalogue(scraped_data: list, range_name: str) -> list:
    """Generate a fresh catalogue in standard format from scraped data."""
    catalogue = []
    seen_skus = {}  # sku -> index in catalogue
    
    for paint in scraped_data:
        sku = paint.get('sku', '')
        if not sku:
            continue
            
        # Normalize SKU (remove spaces)
        sku_clean = normalize_sku(sku)
        
        # Handle duplicates - prefer non-"General" category
        if sku_clean in seen_skus:
            existing_idx = seen_skus[sku_clean]
            existing_cat = catalogue[existing_idx].get('category', '')
            new_cat = paint.get('category', '')
            # If existing is "General" and new isn't, replace it
            if existing_cat == 'General' and new_cat and new_cat != 'General':
                # Update the existing entry's category
                catalogue[existing_idx]['category'] = new_cat
            continue
        
        # Get name from title, or extract from URL as fallback
        name = paint.get('title')
        if not name and paint.get('product_url'):
            # Extract from URL: /product/wood-brown-ink/ -> Wood Brown Ink
            url_parts = paint['product_url'].rstrip('/').split('/')
            if url_parts:
                name = url_parts[-1].replace('-', ' ').title()
        # Ensure sentence case and clean suffix
        if name:
            name = to_sentence_case(name)
            name = clean_paint_name(name)
        
        entry = {
            "brand": "AK Interactive",
            "brandData": {},
            "category": paint.get('category', ''),
            "discontinued": False,
            "hex": paint.get('hex', ''),
            "id": f"ak-interactive-{sku_clean.lower()}",
            "impcat": {
                "layerId": None,
                "shadeId": None
            },
            "name": name,
            "range": range_name,
            "sku": sku_clean,
            "type": paint.get('paint_type', ''),
            "url": paint.get('product_url', '')
        }
        seen_skus[sku_clean] = len(catalogue)
        catalogue.append(entry)
    
    # Sort by SKU
    catalogue.sort(key=lambda x: x['sku'])
    return catalogue


def batch_update_json_files(directory: str, scraped_data: list):
    """Update ALL JSON files in a directory with scraped hex colors."""
    # Build master SKU -> data lookup from all scraped data
    sku_to_data = {}
    name_to_data = {}
    for paint in scraped_data:
        if paint.get('sku') and paint.get('hex'):
            norm_sku = normalize_sku(paint['sku'])
            sku_to_data[norm_sku] = paint
            
            # Also build name lookup for fallback matching
            title = paint.get('title', '')
            if title:
                norm_name = normalize_name(title)
                if norm_name and norm_name not in name_to_data:
                    name_to_data[norm_name] = paint
    
    print(f"\nMaster lookup: {len(sku_to_data)} SKUs, {len(name_to_data)} names")
    print(f"Scanning directory: {directory}\n")
    
    json_files = list(Path(directory).glob('*.json'))
    total_updated = 0
    total_sku_updated = 0
    
    for json_path in json_files:
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            
            updated = 0
            sku_changes = 0
            not_found = []
            
            # Handle both formats: plain list or dict with 'paints' key
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
                match_type = None
                
                # First try SKU match
                if sku in sku_to_data:
                    matched_data = sku_to_data[sku]
                    match_type = 'sku'
                else:
                    # Fallback to name match
                    paint_name = paint.get('name', '')
                    norm_name = normalize_name(paint_name)
                    if norm_name and norm_name in name_to_data:
                        matched_data = name_to_data[norm_name]
                        match_type = 'name'
                
                if matched_data:
                    changed = False
                    if paint.get('hex') != matched_data['hex']:
                        paint['hex'] = matched_data['hex']
                        changed = True
                    
                    # If matched by name, update SKU to new value
                    if match_type == 'name':
                        old_sku = paint.get('sku', '')
                        new_sku = matched_data.get('sku', '')
                        if old_sku != new_sku and new_sku:
                            paint['sku'] = new_sku
                            # Also update URL if available
                            if matched_data.get('product_url'):
                                paint['url'] = matched_data['product_url']
                            sku_changes += 1
                            changed = True
                    
                    if changed:
                        updated += 1
                elif sku:
                    not_found.append(paint.get('sku', ''))
            
            if updated > 0:
                with open(json_path, 'w') as f:
                    json.dump(data, f, indent=2)
                msg = f"  {json_path.name}: {updated} paints updated"
                if sku_changes > 0:
                    msg += f" ({sku_changes} SKUs changed)"
                print(msg)
                total_updated += updated
                total_sku_updated += sku_changes
            else:
                print(f"  {json_path.name}: no changes")
            
            if not_found:
                # Group by prefix for compact display
                by_prefix = defaultdict(list)
                for sku in not_found:
                    match = re.match(r'([A-Z]+\d{0,3})', sku.upper())
                    prefix = match.group(1) if match else 'OTHER'
                    by_prefix[prefix].append(sku)
                
                parts = []
                for prefix in sorted(by_prefix.keys()):
                    skus = by_prefix[prefix]
                    if len(skus) == 1:
                        parts.append(skus[0])
                    else:
                        parts.append(f"{skus[0]}..{skus[-1]} ({len(skus)})")
                print(f"    Not in scrape: {', '.join(parts)}")
                
        except Exception as e:
            print(f"  {json_path.name}: skipped - {e}")
    
    print(f"\nTotal: {total_updated} paints updated across {len(json_files)} files")
    if total_sku_updated > 0:
        print(f"       {total_sku_updated} SKUs updated to new values")
    
    # Show scraped SKUs that weren't matched to any file
    all_json_skus = set()
    for json_path in json_files:
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            paint_list = data if isinstance(data, list) else data.get('paints', [])
            for paint in paint_list:
                sku = normalize_sku(paint.get('sku', ''))
                if sku:
                    all_json_skus.add(sku)
        except:
            pass
    
    unmatched_scraped = [p.get('sku') for p in scraped_data 
                         if normalize_sku(p.get('sku', '')) not in all_json_skus and p.get('sku')]
    if unmatched_scraped:
        # Group by prefix for readability
        by_prefix = defaultdict(list)
        for sku in unmatched_scraped:
            # Extract prefix like AK117, AK120, RC, etc.
            match = re.match(r'([A-Z]+\d{0,3})', sku.upper())
            prefix = match.group(1) if match else 'OTHER'
            by_prefix[prefix].append(sku)
        
        print(f"\nScraped but not in any JSON ({len(unmatched_scraped)} total):")
        for prefix in sorted(by_prefix.keys()):
            skus = by_prefix[prefix]
            print(f"  {prefix}*: {len(skus)} SKUs - {skus[0]} to {skus[-1]}")


def main():
    parser = argparse.ArgumentParser(
        description='Scrape AK Interactive paint data with hex colors',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available ranges:
  -- 3rd Gen sub-ranges --
  3gen-color-punch  Color Punch
  general           General/Core range
  afv               AFV series
  air               AIR series  
  figures           Figures series
  metallic          Metallic paints
  standard          Standard paints
  intense           Intense paints
  pastel            Pastel paints
  wargame           Wargame series
  
  -- Other product lines --
  quick-gen         Quick Gen
  real-colors       Real Colors
  rc-markers        Real Colors Markers
  playmarkers       Playmarkers
  deep-shades       Deep Shades
  the-inks          The Inks
  acrylic-wash      Acrylic Wash
  
  all               Scrape everything
        """
    )
    parser.add_argument('--range', '-r', default='all',
                       help='Color range to scrape (default: all)')
    parser.add_argument('--output', '-o', default='ak_3rdgen_paints.json',
                       help='Output JSON file')
    parser.add_argument('--update-json', '-u',
                       help='Update a single JSON file with scraped hex colors')
    parser.add_argument('--update-all', '-a', action='store_true',
                       help='Update ALL .json files in current directory with scraped hex colors')
    parser.add_argument('--no-colors', action='store_true',
                       help='Skip color sampling')
    parser.add_argument('--no-filter', action='store_true',
                       help='Include non-paint products (brushes, mediums, guides, etc.)')
    parser.add_argument('--refresh-sets', action='store_true',
                       help='Force refresh the sets exclusion cache (normally cached for 24h)')
    parser.add_argument('--workers', '-w', type=int, default=8,
                       help='Number of parallel threads for image sampling (default: 8)')
    parser.add_argument('--range-workers', '-rw', type=int, default=1,
                       help='Number of ranges to scrape in parallel (default: 1)')
    parser.add_argument('--generate', '-g', action='store_true',
                       help='Generate fresh catalogue files instead of updating existing ones')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    
    args = parser.parse_args()
    sample_colors = not args.no_colors
    
    # Fetch set SKUs for exclusion (unless --no-filter is set)
    if not args.no_filter:
        fetch_set_skus(verbose=args.verbose, force_refresh=args.refresh_sets)
    
    # Mapping of range keys to output filenames
    RANGE_TO_FILE = {
        'standard': 'ak_3gen.json',
        'general': 'ak_3gen.json',
        'afv': 'ak_3gen.json',
        'air': 'ak_3gen.json',
        'figures': 'ak_3gen.json',
        'metallic': 'ak_3gen.json',
        'intense': 'ak_3gen.json',
        'pastel': 'ak_3gen.json',
        'wargame': 'ak_3gen.json',
        '3gen-color-punch': 'ak_3gen.json',
        'acrylic-effect': 'ak_3gen.json',
        'fantasy': 'ak_3gen.json',
        'quick-gen': 'ak_quick_gen.json',
        'real-colors': 'ak_real_colors.json',
        'rc-markers': 'ak_real_colors_markers.json',
        'playmarkers': 'ak_playmarkers.json',
        'deep-shades': 'ak_deep_shades.json',
        'the-inks': 'ak_the_inks.json',
        'acrylic-wash': 'ak_acrylic_wash.json',
    }
    
    if args.range == 'all':
        print("Scraping ALL ranges (this may take a while)...")
        data = scrape_all_ranges(sample_colors, args.verbose, args.workers, args.range_workers)
        
        # Flatten all paints for update operations
        all_paints = []
        for range_data in data.values():
            all_paints.extend(range_data['paints'])
        
        if args.generate:
            # Generate fresh catalogue files grouped by output file
            file_paints = defaultdict(list)
            for range_key, range_data in data.items():
                output_file = RANGE_TO_FILE.get(range_key, f'ak_{range_key}.json')
                range_name = range_data['name']
                for paint in range_data['paints']:
                    paint['_range_name'] = range_name
                file_paints[output_file].extend(range_data['paints'])
            
            print(f"\nGenerating {len(file_paints)} catalogue files:")
            for filename, paints in file_paints.items():
                # Determine range name (use first paint's range or derive from filename)
                range_name = paints[0].get('_range_name', '') if paints else ''
                # For 3gen, use "3rd Generation"
                if filename == 'ak_3gen.json':
                    range_name = '3rd Generation'
                
                catalogue = generate_catalogue(paints, range_name)
                with open(filename, 'w') as f:
                    json.dump(catalogue, f, indent=2)
                print(f"  {filename}: {len(catalogue)} paints")
            print("\nDone!")
        elif args.update_all:
            # Update all JSON files in current directory
            batch_update_json_files('.', all_paints)
        elif args.update_json:
            # Update single file
            updated = update_existing_json(args.update_json, all_paints)
            with open(args.update_json, 'w') as f:
                json.dump(updated, f, indent=2)
            print(f"\nUpdated: {args.update_json}")
        else:
            # Save to new file
            with open(args.output, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"\nSaved: {args.output}")
    else:
        if args.range not in COLOR_RANGES:
            print(f"Unknown range: {args.range}")
            print(f"Available: {', '.join(COLOR_RANGES.keys())}")
            return
        
        paints = scrape_color_range(args.range, sample_colors, args.verbose, args.workers)
        
        if args.generate:
            # Generate fresh catalogue file
            output_file = RANGE_TO_FILE.get(args.range, f'ak_{args.range}.json')
            range_name = COLOR_RANGES[args.range]
            if args.range in COLOR_RANGES_3GEN:
                range_name = '3rd Generation'
            catalogue = generate_catalogue(paints, range_name)
            with open(output_file, 'w') as f:
                json.dump(catalogue, f, indent=2)
            print(f"\nGenerated {output_file}: {len(catalogue)} paints")
        elif args.update_all:
            batch_update_json_files('.', paints)
        elif args.update_json:
            updated = update_existing_json(args.update_json, paints)
            with open(args.update_json, 'w') as f:
                json.dump(updated, f, indent=2)
            print(f"\nUpdated: {args.update_json}")
        else:
            output_data = {
                'range': args.range,
                'name': COLOR_RANGES[args.range],
                'paints': paints
            }
            with open(args.output, 'w') as f:
                json.dump(output_data, f, indent=2)
            print(f"\nSaved: {args.output}")


if __name__ == '__main__':
    main()
