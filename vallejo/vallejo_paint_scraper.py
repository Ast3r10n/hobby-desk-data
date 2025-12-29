#!/usr/bin/env python3
"""
Vallejo Paint Scraper

Scrapes acrylicosvallejo.com to build a paint database with hex colors.

*** IMPORTANT: Run this script locally - the Vallejo website has bot protection ***
*** that blocks requests from cloud/proxy environments.                         ***

Requirements:
    pip install requests beautifulsoup4 pillow

Usage:
    python vallejo_paint_scraper.py [--range RANGE_NAME] [--output OUTPUT_FILE]
    
Examples:
    # Scrape a single range
    python vallejo_paint_scraper.py --range xpress-color-en
    
    # Scrape all ranges and generate individual JSON files
    python vallejo_paint_scraper.py --range all --generate
    
    # Scrape without color sampling (faster, for testing)
    python vallejo_paint_scraper.py --range model-color-en --no-colors
    
    # Update existing JSON with new hex colors
    python vallejo_paint_scraper.py --range all --update-json vallejo_model_color.json

Output format matches the standard paint database schema:
{
    "brand": "Vallejo",
    "brandData": {},
    "category": "",
    "discontinued": false,
    "hex": "#8B4513",
    "id": "vallejo-72-402",
    "impcat": {"layerId": null, "shadeId": null},
    "name": "Dwarf Skin",
    "range": "Xpress Color",
    "sku": "72.402",
    "type": "contrast",
    "url": "https://acrylicosvallejo.com/en/product/..."
}
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

# Vallejo paint ranges from the website menu
# Format: url_slug -> (Display Name, Range Name for JSON, paint type)
VALLEJO_RANGES = {
    # Core acrylic ranges
    "model-color-en": {
        "name": "Model Color",
        "range": "Model Color",
        "type": "opaque",
        "url": "https://acrylicosvallejo.com/en/category/hobby/model-color-en/"
    },
    "model-air-en": {
        "name": "Model Air",
        "range": "Model Air",
        "type": "air",
        "url": "https://acrylicosvallejo.com/en/category/model-air-en/"
    },
    "game-color-en": {
        "name": "Game Color",
        "range": "Game Color",
        "type": "opaque",
        "url": "https://acrylicosvallejo.com/en/category/hobby/game-color-en/"
    },
    "game-air-en": {
        "name": "Game Air",
        "range": "Game Air",
        "type": "air",
        "url": "https://acrylicosvallejo.com/en/category/hobby/game-air-en/"
    },
    "xpress-color-en": {
        "name": "Xpress Color",
        "range": "Xpress Color",
        "type": "contrast",
        "url": "https://acrylicosvallejo.com/en/category/hobby/xpress-color-en/"
    },
    "mecha-color-en": {
        "name": "Mecha Color",
        "range": "Mecha Color",
        "type": "opaque",
        "url": "https://acrylicosvallejo.com/en/category/hobby/mecha-color-en/"
    },
    
    # Metallic ranges
    "metal-color-en": {
        "name": "Metal Color",
        "range": "Metal Color",
        "type": "metallic",
        "url": "https://acrylicosvallejo.com/en/category/hobby/metal-color-en/"
    },
    "liquid-metal-en": {
        "name": "Liquid Metal",
        "range": "Liquid Metal",
        "type": "metallic",
        "url": "https://acrylicosvallejo.com/en/category/hobby/liquid-metal-en/"
    },
    "true-metallic-metal-en": {
        "name": "True Metallic Metal",
        "range": "True Metallic Metal",
        "type": "metallic",
        "url": "https://acrylicosvallejo.com/en/category/hobby/true-metallic-metal-en/"
    },
    
    # Effects and washes
    "wash-fx-en": {
        "name": "Wash FX",
        "range": "Wash FX",
        "type": "wash",
        "url": "https://acrylicosvallejo.com/en/category/hobby/wash-fx-en/"
    },
    # Other ranges
    "primers-en": {
        "name": "Primers",
        "range": "Primers",
        "type": "primer",
        "url": "https://acrylicosvallejo.com/en/category/hobby/primers-en/"
    },
    "premium-color-en": {
        "name": "Premium Color",
        "range": "Premium Color",
        "type": "opaque",
        "url": "https://acrylicosvallejo.com/en/category/hobby/premium-color-en/"
    },
    "hobby-paint": {
        "name": "Hobby Paint",
        "range": "Hobby Paint",
        "type": "opaque",
        "url": "https://acrylicosvallejo.com/en/category/hobby/hobby-paint/"
    },
}

# Type overrides based on name keywords
TYPE_OVERRIDES = {
    'varnish': 'varnish',
    'barniz': 'varnish',
    'thinner': 'thinner',
    'diluyente': 'thinner',
    'primer': 'primer',
    'imprimación': 'primer',
    'medium': 'technical',
    'retarder': 'technical',
    'retardante': 'technical',
    'glaze': 'transparent',
    'ink': 'ink',
    'tinta': 'ink',
    'wash': 'wash',
    'metallic': 'metallic',
    'metal': 'metallic',
    'chrome': 'metallic',
    'gold': 'metallic',
    'silver': 'metallic',
    'copper': 'metallic',
    'bronze': 'metallic',
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Words that indicate non-paint products - exclude these
EXCLUDE_KEYWORDS = [
    'brush', 'pincel', 'guide', 'guía', 'cleaner', 'limpiador',
    ' set', 'pack', 'bundle', 'kit', 'book', 'magazine', 'revista',
    'full range', 'combo', 'collection', 'colección', 'colors set',
    'colours set', 'paint set', 'color set', 'colour set', 'range box',
    'case', 'suitcase', 'maleta', 'all colors', 'all colours', 'complete range',
    'super pack', 'display', 'expositor', 'stand', 'rack',
    'airbrush', 'aerógrafo', 'compressor', 'stencil', 'plantilla',
    'tool', 'herramienta', 'knife', 'cutter', 'tweezer', 'pinza',
    'scenery', 'scenics', 'grass', 'hierba', 'flock', 'tuft'
]

# SKUs to exclude from specific ranges (to avoid duplicates where product belongs to one range)
# Format: 'range-key': ['sku1', 'sku2', ...]
RANGE_SKU_EXCLUSIONS = {
    # Gloss Black 77.660 is a primer, not a metal color
    'metal-color-en': ['77.660'],
}


def normalize_sku(sku: str) -> str:
    """Normalize SKU for matching - remove spaces, force XX.XXX format."""
    if not sku:
        return ''
    # Remove spaces
    sku = re.sub(r'\s+', '', sku).strip()
    
    # Force XX.XXX format - if we have 5 digits without a dot, insert one
    # e.g., "76109" -> "76.109"
    if re.match(r'^\d{5}$', sku):
        sku = f"{sku[:2]}.{sku[2:]}"
    
    return sku


def normalize_name(name: str) -> str:
    """Normalize paint name for fuzzy matching."""
    if not name:
        return ''
    name = name.lower()
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    for word in ['vallejo', 'acrylic', 'paint', 'color', 'colour']:
        name = re.sub(rf'\b{word}\b', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def to_sentence_case(name: str) -> str:
    """Convert name to sentence case: 'WOOD BROWN' -> 'Wood Brown'"""
    if not name:
        return name
    words = name.split()
    result = []
    for word in words:
        if word.upper() == word or word.lower() == word:
            word = word.title()
        result.append(word)
    return ' '.join(result)


def clean_paint_name(name: str) -> str:
    """Clean paint name - remove range suffix and size."""
    if not name:
        return name
    # Remove suffix after last dash
    for sep in [' – ', ' - ', ' — ']:
        if sep in name:
            parts = name.rsplit(sep, 1)
            # Only remove if suffix looks like a range name
            if len(parts) == 2 and any(r in parts[1].lower() for r in ['color', 'air', 'metal', 'xpress', 'game', 'model']):
                name = parts[0].strip()
                break
    # Remove size suffixes
    name = re.sub(r'\s*\(\d+\s*ml\)\s*$', '', name, flags=re.IGNORECASE).strip()
    name = re.sub(r'\s+\d+\s*ml\s*$', '', name, flags=re.IGNORECASE).strip()
    return name


def get_paint_type(paint: dict, default_type: str) -> str:
    """Determine paint type, checking name for overrides."""
    name = (paint.get('title') or paint.get('name', '')).lower()
    
    for keyword, paint_type in TYPE_OVERRIDES.items():
        if keyword in name:
            # For metallic, only override if not already metallic type
            if paint_type == 'metallic' and default_type != 'metallic':
                # Be more careful - gold/silver/etc might be color names
                # Only override if it's clearly a metallic paint
                if any(m in name for m in ['metallic', 'metal color', 'liquid metal', 'chrome']):
                    return 'metallic'
            else:
                return paint_type
    
    return default_type


def is_paint_product(paint: dict) -> bool:
    """Filter out non-paint products like brushes, sets, tools."""
    title = (paint.get('title') or '').lower()
    sku = (paint.get('sku') or '')
    url = (paint.get('product_url') or '').lower()
    
    for keyword in EXCLUDE_KEYWORDS:
        if keyword in title or keyword in url:
            return False
    
    # Vallejo paint SKUs follow patterns like 70.XXX, 72.XXX, 73.XXX, 77.XXX
    # Non-paint items often have different SKU patterns
    if sku and not re.match(r'^\d{2}\.\d{3}', sku):
        # Some valid items might not match, so just warn
        pass
    
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
    """Extract paint data from a Vallejo category page."""
    paints = []
    seen_skus = set()
    
    # Vallejo uses li.product items
    for item in soup.select('li.product'):
        try:
            # Get link to product page
            link = item.select_one('a.featured-image, a[href*="/product/"]')
            if not link:
                continue
            
            product_url = link.get('href')
            
            # Get SKU from .referencia element
            sku_elem = item.select_one('.referencia')
            sku = sku_elem.get_text(strip=True) if sku_elem else None
            
            # Get name from title
            title_elem = item.select_one('.woocommerce-loop-product__title, h2')
            title = title_elem.get_text(strip=True) if title_elem else None
            
            # Get image URL
            img_elem = item.select_one('img')
            img_url = None
            if img_elem:
                # Prefer srcset for higher res, fallback to src
                srcset = img_elem.get('srcset', '')
                src = img_elem.get('src') or img_elem.get('data-src')
                
                # Parse srcset to get highest resolution
                if srcset:
                    # srcset format: "url1 300w, url2 600w, ..."
                    parts = srcset.split(',')
                    best_url = None
                    best_width = 0
                    for part in parts:
                        part = part.strip()
                        match = re.match(r'(\S+)\s+(\d+)w', part)
                        if match:
                            url, width = match.groups()
                            if int(width) > best_width:
                                best_width = int(width)
                                best_url = url
                    if best_url:
                        img_url = best_url
                
                if not img_url:
                    img_url = src
            
            # Clean up title
            if title:
                title = html.unescape(title)
                title = to_sentence_case(title)
                title = clean_paint_name(title)
            
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
    
    return paints


def has_next_page(soup: BeautifulSoup) -> bool:
    """Check if there's a next page of results."""
    # Look for pagination next link
    next_link = soup.select_one('a.next.page-numbers, a.next')
    return next_link is not None


def get_next_page_url(soup: BeautifulSoup) -> str:
    """Get the URL for the next page."""
    next_link = soup.select_one('a.next.page-numbers, a.next')
    if next_link:
        return next_link.get('href')
    return None


def sample_color_from_image(img_url: str, verbose: bool = False, range_hint: str = '') -> str:
    """Download image and sample the paint color from the triangular swatch.
    
    Vallejo images have a triangular color swatch on the left side.
    The triangle covers roughly the left 1/3 of the image.
    """
    try:
        response = requests.get(img_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        
        img = Image.open(BytesIO(response.content)).convert('RGB')
        width, height = img.size
        
        # Vallejo images have a triangular swatch on the left
        # The triangle's color area is roughly:
        # - Horizontal: from 5% to 25% of width
        # - Vertical: from 30% to 70% of height (middle section)
        # We sample multiple points and pick the most representative color
        
        sample_regions = [
            # Core triangle area - middle
            (int(width * 0.10), int(height * 0.50)),
            (int(width * 0.15), int(height * 0.50)),
            (int(width * 0.12), int(height * 0.45)),
            (int(width * 0.12), int(height * 0.55)),
            # Slightly different positions
            (int(width * 0.08), int(height * 0.48)),
            (int(width * 0.18), int(height * 0.52)),
            (int(width * 0.10), int(height * 0.40)),
            (int(width * 0.10), int(height * 0.60)),
        ]
        
        best_color = None
        best_score = -1
        
        for x, y in sample_regions:
            # Sample a small region around the point
            colors = []
            for dx in range(-5, 6, 2):
                for dy in range(-5, 6, 2):
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
            
            # Skip near-white or near-black
            if brightness > 245 or brightness < 10:
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
        
        # Fallback: sample from typical triangle location
        x, y = int(width * 0.12), int(height * 0.50)
        r, g, b = img.getpixel((x, y))
        return "#{:02X}{:02X}{:02X}".format(r, g, b)
        
    except Exception as e:
        print(f"        Error sampling color: {e}")
        return None


def sample_paint_color(paint: dict, verbose: bool = False, range_hint: str = '') -> dict:
    """Sample color for a single paint. Returns the paint dict with hex added."""
    img_url = paint.get('img_url')
    if img_url:
        paint['hex'] = sample_color_from_image(img_url, verbose, range_hint)
    return paint


def scrape_range(range_key: str, sample_colors: bool = True, verbose: bool = False, max_workers: int = 8, filter_products: bool = True) -> list:
    """Scrape all paints from a Vallejo range."""
    if range_key not in VALLEJO_RANGES:
        print(f"Unknown range: {range_key}")
        return []
    
    range_info = VALLEJO_RANGES[range_key]
    range_name = range_info['name']
    base_url = range_info['url']
    default_type = range_info['type']
    
    print(f"\n{'='*60}")
    print(f"Scraping: {range_name} ({range_key})")
    print('='*60)
    
    all_paints = []
    page = 1
    current_url = base_url
    
    while current_url:
        try:
            soup = fetch_page(current_url)
            paints = extract_paints_from_page(soup)
            
            # Normalize SKUs (force XX.XXX format)
            for paint in paints:
                if paint.get('sku'):
                    paint['sku'] = normalize_sku(paint['sku'])
            
            # Filter out non-paint products (unless disabled)
            if filter_products:
                before_filter = len(paints)
                filtered_out = [p for p in paints if not is_paint_product(p)]
                paints = [p for p in paints if is_paint_product(p)]
                
                if filtered_out and verbose:
                    print(f"      Filtered out {len(filtered_out)}: {', '.join(p.get('sku', '?') for p in filtered_out)}")
            
            # Filter out SKUs that should be excluded from this range
            excluded_skus = RANGE_SKU_EXCLUSIONS.get(range_key, [])
            if excluded_skus:
                before_exclude = len(paints)
                paints = [p for p in paints if p.get('sku') not in excluded_skus]
                if len(paints) < before_exclude and verbose:
                    print(f"      Excluded {before_exclude - len(paints)} SKUs from this range")
            
            if not paints:
                if page == 1:
                    print(f"    No paints found for: {range_key}")
                break
            
            print(f"    Page {page}: {len(paints)} paints")
            
            if sample_colors:
                print(f"    Sampling colors ({max_workers} threads)...")
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(sample_paint_color, paint, verbose, range_key): paint for paint in paints}
                    completed = 0
                    for future in as_completed(futures):
                        completed += 1
                        paint = future.result()
                        sku = paint.get('sku') or '?'
                        hex_val = paint.get('hex') or 'failed'
                        if verbose or completed % 10 == 0 or completed == len(paints):
                            print(f"      [{completed}/{len(paints)}] {sku}: {hex_val}")
            
            # Add type to each paint
            for paint in paints:
                paint['paint_type'] = get_paint_type(paint, default_type)
                paint['range_name'] = range_info['range']
            
            all_paints.extend(paints)
            
            # Check for next page
            next_url = get_next_page_url(soup)
            if next_url:
                current_url = next_url
                page += 1
                time.sleep(0.5)  # Be polite
            else:
                break
                
        except Exception as e:
            print(f"    Error on page {page}: {e}")
            break
    
    print(f"  Total: {len(all_paints)} paints")
    return all_paints


def scrape_all_ranges(sample_colors: bool = True, verbose: bool = False, max_workers: int = 8, range_workers: int = 1, filter_products: bool = True) -> dict:
    """Scrape all Vallejo ranges, optionally in parallel."""
    all_data = {}
    
    if range_workers > 1:
        print(f"\nScraping {len(VALLEJO_RANGES)} ranges in parallel ({range_workers} concurrent)...")
        with ThreadPoolExecutor(max_workers=range_workers) as executor:
            futures = {
                executor.submit(scrape_range, range_key, sample_colors, verbose, max_workers, filter_products): range_key 
                for range_key in VALLEJO_RANGES.keys()
            }
            for future in as_completed(futures):
                range_key = futures[future]
                try:
                    paints = future.result()
                    all_data[range_key] = {
                        'name': VALLEJO_RANGES[range_key]['name'],
                        'range': VALLEJO_RANGES[range_key]['range'],
                        'paints': paints
                    }
                except Exception as e:
                    print(f"  Error scraping {range_key}: {e}")
    else:
        for range_key in VALLEJO_RANGES.keys():
            paints = scrape_range(range_key, sample_colors, verbose, max_workers, filter_products)
            all_data[range_key] = {
                'name': VALLEJO_RANGES[range_key]['name'],
                'range': VALLEJO_RANGES[range_key]['range'],
                'paints': paints
            }
            time.sleep(1)  # Be polite between ranges
    
    return all_data


def generate_catalogue(scraped_data: list, range_name: str) -> list:
    """Generate a fresh catalogue in standard format from scraped data."""
    catalogue = []
    seen_skus = {}
    
    for paint in scraped_data:
        sku = paint.get('sku', '')
        if not sku:
            continue
        
        # Normalize SKU (force XX.XXX format)
        sku_clean = normalize_sku(sku)
        
        # Skip duplicates
        if sku_clean in seen_skus:
            continue
        
        name = paint.get('title')
        if not name and paint.get('product_url'):
            url_parts = paint['product_url'].rstrip('/').split('/')
            if url_parts:
                name = url_parts[-1].replace('-', ' ').title()
        
        if name:
            name = to_sentence_case(name)
            name = clean_paint_name(name)
        
        entry = {
            "brand": "Vallejo",
            "brandData": {},
            "category": "",  # Vallejo doesn't use categories like Two Thin Coats
            "discontinued": False,
            "hex": paint.get('hex', ''),
            "id": f"vallejo-{sku_clean.replace('.', '-').lower()}",
            "impcat": {
                "layerId": None,
                "shadeId": None
            },
            "name": name,
            "range": paint.get('range_name', range_name),
            "sku": sku_clean,
            "type": paint.get('paint_type', 'opaque'),
            "url": paint.get('product_url', '')
        }
        seen_skus[sku_clean] = len(catalogue)
        catalogue.append(entry)
    
    # Sort by SKU
    catalogue.sort(key=lambda x: x['sku'])
    return catalogue


def update_existing_json(json_path: str, scraped_data: list) -> list:
    """Update existing JSON with scraped hex colors by matching SKU."""
    with open(json_path, 'r') as f:
        existing = json.load(f)
    
    # Build SKU -> hex lookup
    sku_to_data = {}
    for paint in scraped_data:
        if paint.get('sku') and paint.get('hex'):
            sku_to_data[normalize_sku(paint['sku'])] = paint
    
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
            if scraped.get('hex'):
                paint['hex'] = scraped['hex']
                updated += 1
            if scraped.get('product_url') and not paint.get('url'):
                paint['url'] = scraped['product_url']
    
    print(f"  Updated {updated} paints with hex colors")
    return existing


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
                    if paint.get('hex') != matched_data.get('hex'):
                        paint['hex'] = matched_data.get('hex')
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
                    match = re.match(r'(\d{2})', sku)
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


# Mapping of range keys to output filenames
RANGE_TO_FILE = {
    'model-color-en': 'vallejo_model_color.json',
    'model-air-en': 'vallejo_model_air.json',
    'game-color-en': 'vallejo_game_color.json',
    'game-air-en': 'vallejo_game_air.json',
    'xpress-color-en': 'vallejo_xpress_color.json',
    'mecha-color-en': 'vallejo_mecha_color.json',
    'metal-color-en': 'vallejo_metal_color.json',
    'liquid-metal-en': 'vallejo_liquid_metal.json',
    'true-metallic-metal-en': 'vallejo_true_metallic_metal.json',
    'wash-fx-en': 'vallejo_wash_fx.json',
    'primers-en': 'vallejo_primers.json',
    'premium-color-en': 'vallejo_premium_color.json',
    'hobby-paint': 'vallejo_hobby_paint.json',
}


def main():
    parser = argparse.ArgumentParser(
        description='Scrape Vallejo paint data with hex colors',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available ranges:
  -- Core Acrylics --
  model-color-en        Model Color
  model-air-en          Model Air
  game-color-en         Game Color
  game-air-en           Game Air
  xpress-color-en       Xpress Color
  mecha-color-en        Mecha Color
  
  -- Metallics --
  metal-color-en        Metal Color
  liquid-metal-en       Liquid Metal
  true-metallic-metal-en True Metallic Metal
  
  -- Effects & Washes --
  wash-fx-en            Wash FX
  
  -- Other --
  primers-en            Primers
  premium-color-en      Premium Color
  hobby-paint           Hobby Paint
  
  all                   Scrape everything
        """
    )
    parser.add_argument('--range', '-r', default='all',
                       help='Range to scrape (default: all)')
    parser.add_argument('--output', '-o', default='vallejo_paints.json',
                       help='Output JSON file')
    parser.add_argument('--update-json', '-u',
                       help='Update a single JSON file with scraped hex colors')
    parser.add_argument('--update-all', '-a', action='store_true',
                       help='Update ALL .json files in current directory with scraped hex colors')
    parser.add_argument('--no-colors', action='store_true',
                       help='Skip color sampling')
    parser.add_argument('--no-filter', action='store_true',
                       help='Include non-paint products (sets, tools, etc.)')
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
    filter_products = not args.no_filter
    
    if args.range == 'all':
        print("Scraping ALL Vallejo ranges...")
        data = scrape_all_ranges(sample_colors, args.verbose, args.workers, args.range_workers, filter_products)
        
        # Flatten all paints
        all_paints = []
        for range_data in data.values():
            all_paints.extend(range_data['paints'])
        
        if args.generate:
            # Generate separate files per range
            print(f"\nGenerating {len(data)} catalogue files:")
            for range_key, range_data in data.items():
                output_file = RANGE_TO_FILE.get(range_key, f'vallejo_{range_key}.json')
                catalogue = generate_catalogue(range_data['paints'], range_data['range'])
                with open(output_file, 'w') as f:
                    json.dump(catalogue, f, indent=2)
                print(f"  {output_file}: {len(catalogue)} paints")
            print("\nDone!")
        elif args.update_all:
            # Update all JSON files in current directory
            batch_update_json_files('.', all_paints)
        elif args.update_json:
            updated = update_existing_json(args.update_json, all_paints)
            with open(args.update_json, 'w') as f:
                json.dump(updated, f, indent=2)
            print(f"\nUpdated: {args.update_json}")
        else:
            with open(args.output, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"\nSaved: {args.output}")
    else:
        if args.range not in VALLEJO_RANGES:
            print(f"Unknown range: {args.range}")
            print(f"Available: {', '.join(VALLEJO_RANGES.keys())}")
            return
        
        paints = scrape_range(args.range, sample_colors, args.verbose, args.workers, filter_products)
        
        if args.generate:
            output_file = RANGE_TO_FILE.get(args.range, f'vallejo_{args.range}.json')
            range_name = VALLEJO_RANGES[args.range]['range']
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
                'name': VALLEJO_RANGES[args.range]['name'],
                'paints': paints
            }
            with open(args.output, 'w') as f:
                json.dump(output_data, f, indent=2)
            print(f"\nSaved: {args.output}")


if __name__ == '__main__':
    main()
