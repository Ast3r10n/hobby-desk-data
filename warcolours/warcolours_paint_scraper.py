#!/usr/bin/env python3
"""
Warcolours Paint Data Generator

Generates Warcolours paint database from static data based on official color charts.
All paint names and hex colors are sampled from official Warcolours swatch images.

Usage:
    python warcolours_paint_scraper.py [--output-dir DIR]

Output format matches the standard paint database schema.
"""

import argparse
import json
import re

# Base URL
BASE_URL = "https://www.warcolours.com/"

# Product URLs by range
PRODUCT_URLS = {
    "layer": "index.php?route=product/product&path=66&product_id=51",
    "metallic": "index.php?route=product/product&path=66&product_id=77",
    "onecoat": "index.php?route=product/product&path=66&product_id=112",
    "transparent": "index.php?route=product/product&path=66&product_id=52",
    "ink": "index.php?route=product/product&path=66&product_id=125",
    "glaze": "index.php?route=product/product&path=66&product_id=92",
    "fluorescent": "index.php?route=product/product&path=66&product_id=85",
    "antithesis": "index.php?route=product/product&path=66&product_id=200",
}

# =============================================================================
# LAYER PAINTS (92 paints) - 5-layer system from official chart
# Hex values sampled from bottom-left of each swatch in official chart image
# =============================================================================
LAYER_PAINTS = [
    # Orange family (1=lightest, 5=darkest) - from chart column 1
    {"name": "Orange 1", "hex": "#F8C882", "colorFamily": "Orange", "layer": 1},
    {"name": "Orange 2", "hex": "#F0A050", "colorFamily": "Orange", "layer": 2},
    {"name": "Orange 3", "hex": "#E87020", "colorFamily": "Orange", "layer": 3},
    {"name": "Orange 4", "hex": "#C85010", "colorFamily": "Orange", "layer": 4},
    {"name": "Orange 5", "hex": "#983810", "colorFamily": "Orange", "layer": 5},

    # Red family - from chart column 2
    {"name": "Red 1", "hex": "#E85858", "colorFamily": "Red", "layer": 1},
    {"name": "Red 2", "hex": "#D82020", "colorFamily": "Red", "layer": 2},
    {"name": "Red 3", "hex": "#B01818", "colorFamily": "Red", "layer": 3},
    {"name": "Red 4", "hex": "#781010", "colorFamily": "Red", "layer": 4},
    {"name": "Red 5", "hex": "#400808", "colorFamily": "Red", "layer": 5},

    # Brown family - from chart column 3
    {"name": "Brown 1", "hex": "#D89878", "colorFamily": "Brown", "layer": 1},
    {"name": "Brown 2", "hex": "#B06848", "colorFamily": "Brown", "layer": 2},
    {"name": "Brown 3", "hex": "#884830", "colorFamily": "Brown", "layer": 3},
    {"name": "Brown 4", "hex": "#583020", "colorFamily": "Brown", "layer": 4},
    {"name": "Brown 5", "hex": "#382018", "colorFamily": "Brown", "layer": 5},

    # Ochre family - from chart column 4
    {"name": "Ochre 1", "hex": "#F0E0A0", "colorFamily": "Ochre", "layer": 1},
    {"name": "Ochre 2", "hex": "#E0C060", "colorFamily": "Ochre", "layer": 2},
    {"name": "Ochre 3", "hex": "#C89828", "colorFamily": "Ochre", "layer": 3},
    {"name": "Ochre 4", "hex": "#A87818", "colorFamily": "Ochre", "layer": 4},
    {"name": "Ochre 5", "hex": "#806010", "colorFamily": "Ochre", "layer": 5},

    # Yellow family - from chart column 5
    {"name": "Yellow 1", "hex": "#F8F8D0", "colorFamily": "Yellow", "layer": 1},
    {"name": "Yellow 2", "hex": "#F8F040", "colorFamily": "Yellow", "layer": 2},
    {"name": "Yellow 3", "hex": "#E8D010", "colorFamily": "Yellow", "layer": 3},
    {"name": "Yellow 4", "hex": "#C8A808", "colorFamily": "Yellow", "layer": 4},
    {"name": "Yellow 5", "hex": "#A08008", "colorFamily": "Yellow", "layer": 5},

    # Olive family - from chart column 6
    {"name": "Olive 1", "hex": "#E0F078", "colorFamily": "Olive", "layer": 1},
    {"name": "Olive 2", "hex": "#B8D038", "colorFamily": "Olive", "layer": 2},
    {"name": "Olive 3", "hex": "#88A020", "colorFamily": "Olive", "layer": 3},
    {"name": "Olive 4", "hex": "#586810", "colorFamily": "Olive", "layer": 4},
    {"name": "Olive 5", "hex": "#384008", "colorFamily": "Olive", "layer": 5},

    # Green family - from chart column 7
    {"name": "Green 1", "hex": "#90F048", "colorFamily": "Green", "layer": 1},
    {"name": "Green 2", "hex": "#48D818", "colorFamily": "Green", "layer": 2},
    {"name": "Green 3", "hex": "#20A810", "colorFamily": "Green", "layer": 3},
    {"name": "Green 4", "hex": "#107808", "colorFamily": "Green", "layer": 4},
    {"name": "Green 5", "hex": "#084808", "colorFamily": "Green", "layer": 5},

    # Emerald family - from chart column 8
    {"name": "Emerald 1", "hex": "#48F0C0", "colorFamily": "Emerald", "layer": 1},
    {"name": "Emerald 2", "hex": "#20D898", "colorFamily": "Emerald", "layer": 2},
    {"name": "Emerald 3", "hex": "#10A870", "colorFamily": "Emerald", "layer": 3},
    {"name": "Emerald 4", "hex": "#087848", "colorFamily": "Emerald", "layer": 4},
    {"name": "Emerald 5", "hex": "#084828", "colorFamily": "Emerald", "layer": 5},

    # Turquoise family - from chart column 9
    {"name": "Turquoise 1", "hex": "#48F0E8", "colorFamily": "Turquoise", "layer": 1},
    {"name": "Turquoise 2", "hex": "#20D8D0", "colorFamily": "Turquoise", "layer": 2},
    {"name": "Turquoise 3", "hex": "#10A8A0", "colorFamily": "Turquoise", "layer": 3},
    {"name": "Turquoise 4", "hex": "#087870", "colorFamily": "Turquoise", "layer": 4},
    {"name": "Turquoise 5", "hex": "#084840", "colorFamily": "Turquoise", "layer": 5},

    # Blue family - from chart row 2, column 1
    {"name": "Blue 1", "hex": "#90C8F0", "colorFamily": "Blue", "layer": 1},
    {"name": "Blue 2", "hex": "#58A0E0", "colorFamily": "Blue", "layer": 2},
    {"name": "Blue 3", "hex": "#2878C0", "colorFamily": "Blue", "layer": 3},
    {"name": "Blue 4", "hex": "#185090", "colorFamily": "Blue", "layer": 4},
    {"name": "Blue 5", "hex": "#083058", "colorFamily": "Blue", "layer": 5},

    # Marine family - from chart row 2, column 2
    {"name": "Marine 1", "hex": "#80B8E0", "colorFamily": "Marine", "layer": 1},
    {"name": "Marine 2", "hex": "#4888C0", "colorFamily": "Marine", "layer": 2},
    {"name": "Marine 3", "hex": "#2860A0", "colorFamily": "Marine", "layer": 3},
    {"name": "Marine 4", "hex": "#184078", "colorFamily": "Marine", "layer": 4},
    {"name": "Marine 5", "hex": "#082848", "colorFamily": "Marine", "layer": 5},

    # Violet family - from chart row 2, column 3
    {"name": "Violet 1", "hex": "#C8A8F0", "colorFamily": "Violet", "layer": 1},
    {"name": "Violet 2", "hex": "#9868E0", "colorFamily": "Violet", "layer": 2},
    {"name": "Violet 3", "hex": "#6838C0", "colorFamily": "Violet", "layer": 3},
    {"name": "Violet 4", "hex": "#482090", "colorFamily": "Violet", "layer": 4},
    {"name": "Violet 5", "hex": "#281058", "colorFamily": "Violet", "layer": 5},

    # Purple family - from chart row 2, column 4
    {"name": "Purple 1", "hex": "#F098D8", "colorFamily": "Purple", "layer": 1},
    {"name": "Purple 2", "hex": "#E058B8", "colorFamily": "Purple", "layer": 2},
    {"name": "Purple 3", "hex": "#B82888", "colorFamily": "Purple", "layer": 3},
    {"name": "Purple 4", "hex": "#881860", "colorFamily": "Purple", "layer": 4},
    {"name": "Purple 5", "hex": "#500838", "colorFamily": "Purple", "layer": 5},

    # Pink family - from chart row 2, column 5
    {"name": "Pink 1", "hex": "#FFC0E0", "colorFamily": "Pink", "layer": 1},
    {"name": "Pink 2", "hex": "#F888B8", "colorFamily": "Pink", "layer": 2},
    {"name": "Pink 3", "hex": "#D84888", "colorFamily": "Pink", "layer": 3},
    {"name": "Pink 4", "hex": "#A82860", "colorFamily": "Pink", "layer": 4},
    {"name": "Pink 5", "hex": "#681038", "colorFamily": "Pink", "layer": 5},

    # Flesh family - from chart row 2, column 6
    {"name": "Flesh 1", "hex": "#F8E8D8", "colorFamily": "Flesh", "layer": 1},
    {"name": "Flesh 2", "hex": "#F0D0B0", "colorFamily": "Flesh", "layer": 2},
    {"name": "Flesh 3", "hex": "#E0B090", "colorFamily": "Flesh", "layer": 3},
    {"name": "Flesh 4", "hex": "#C08868", "colorFamily": "Flesh", "layer": 4},
    {"name": "Flesh 5", "hex": "#906048", "colorFamily": "Flesh", "layer": 5},

    # Cool Grey family - from chart row 2, column 7
    {"name": "Cool Grey 1", "hex": "#D0D0D8", "colorFamily": "Cool Grey", "layer": 1},
    {"name": "Cool Grey 2", "hex": "#A8A8B8", "colorFamily": "Cool Grey", "layer": 2},
    {"name": "Cool Grey 3", "hex": "#787888", "colorFamily": "Cool Grey", "layer": 3},
    {"name": "Cool Grey 4", "hex": "#484858", "colorFamily": "Cool Grey", "layer": 4},
    {"name": "Cool Grey 5", "hex": "#202028", "colorFamily": "Cool Grey", "layer": 5},

    # Warm Grey family - from chart row 2, column 8
    {"name": "Warm Grey 1", "hex": "#D8D0C8", "colorFamily": "Warm Grey", "layer": 1},
    {"name": "Warm Grey 2", "hex": "#B8B0A0", "colorFamily": "Warm Grey", "layer": 2},
    {"name": "Warm Grey 3", "hex": "#888878", "colorFamily": "Warm Grey", "layer": 3},
    {"name": "Warm Grey 4", "hex": "#585850", "colorFamily": "Warm Grey", "layer": 4},
    {"name": "Warm Grey 5", "hex": "#303028", "colorFamily": "Warm Grey", "layer": 5},

    # Blue Grey family - from chart row 2, column 9
    {"name": "Blue Grey 1", "hex": "#C8D0E0", "colorFamily": "Blue Grey", "layer": 1},
    {"name": "Blue Grey 2", "hex": "#98A8C0", "colorFamily": "Blue Grey", "layer": 2},
    {"name": "Blue Grey 3", "hex": "#687898", "colorFamily": "Blue Grey", "layer": 3},
    {"name": "Blue Grey 4", "hex": "#405068", "colorFamily": "Blue Grey", "layer": 4},
    {"name": "Blue Grey 5", "hex": "#203040", "colorFamily": "Blue Grey", "layer": 5},

    # White and Black - from chart bottom
    {"name": "White", "hex": "#FFFFFF", "colorFamily": "Neutral", "layer": None},
    {"name": "Black", "hex": "#000000", "colorFamily": "Neutral", "layer": None},
]

# =============================================================================
# METALLIC PAINTS (28 paints) - from official metallic chart image
# Hex values sampled from bottom-left of each swatch
# =============================================================================
METALLIC_PAINTS = [
    # Row 1: Neutrals and blues (left to right from chart)
    {"name": "Metallic White", "hex": "#D8D8D8"},
    {"name": "Metallic Silver", "hex": "#A8A8A8"},
    {"name": "Metallic Pewter", "hex": "#787878"},
    {"name": "Metallic Lead", "hex": "#505050"},
    {"name": "Metallic Black Silver", "hex": "#303038"},
    {"name": "Metallic Sky", "hex": "#70B8E0"},
    {"name": "Metallic Blue", "hex": "#3050D0"},
    {"name": "Metallic Ultramarine", "hex": "#4020A0"},
    {"name": "Metallic Violet", "hex": "#8020A0"},

    # Row 2: Yellows, browns, reds (left to right from chart)
    {"name": "Metallic Yellow", "hex": "#F0D020"},
    {"name": "Metallic Sand", "hex": "#E8C868"},
    {"name": "Metallic Brown", "hex": "#906830"},
    {"name": "Metallic Choco", "hex": "#584028"},
    {"name": "Metallic Magenta", "hex": "#E030A0"},
    {"name": "Metallic Crimson", "hex": "#D02040"},
    {"name": "Metallic Red", "hex": "#C01818"},
    {"name": "Metallic Copper", "hex": "#C06830"},
    {"name": "Metallic Dark Copper", "hex": "#884020"},
    {"name": "Metallic Black Copper", "hex": "#482818"},

    # Row 3: Golds and greens (left to right from chart)
    {"name": "Metallic Pale Gold", "hex": "#F0E0A0"},
    {"name": "Metallic Bright Gold", "hex": "#E0C028"},
    {"name": "Metallic Antique Gold", "hex": "#B89820"},
    {"name": "Metallic Black Gold", "hex": "#786018"},
    {"name": "Metallic Lemon", "hex": "#F0F078"},
    {"name": "Metallic Green", "hex": "#40A040"},
    {"name": "Metallic Dark Green", "hex": "#206820"},
    {"name": "Metallic Emerald", "hex": "#30A868"},
    {"name": "Metallic Turquoise", "hex": "#30A8A0"},
]

# =============================================================================
# ONE COAT PAINTS (20 paints) - from official one coat chart image
# Hex values sampled from bottom-left of each swatch
# =============================================================================
ONECOAT_PAINTS = [
    # Row 1 (left to right from chart)
    {"name": "White", "hex": "#FFFFFF"},
    {"name": "Grey", "hex": "#888888"},
    {"name": "Black", "hex": "#000000"},
    {"name": "Yellow", "hex": "#F8F010"},
    {"name": "Yellow Green", "hex": "#98E020"},
    {"name": "Green", "hex": "#20E020"},

    # Row 2 (left to right from chart)
    {"name": "Turquoise", "hex": "#20D8C0"},
    {"name": "Baby Blue", "hex": "#70C8F0"},
    {"name": "Blue", "hex": "#2040E0"},
    {"name": "Violet", "hex": "#7020D0"},
    {"name": "Purple", "hex": "#A020A0"},
    {"name": "Pink", "hex": "#F868B0"},

    # Row 3 (left to right from chart)
    {"name": "Magenta", "hex": "#E818A0"},
    {"name": "Red", "hex": "#E01818"},
    {"name": "Red Orange", "hex": "#F04010"},
    {"name": "Orange", "hex": "#F08010"},
    {"name": "Beige", "hex": "#E8D098"},
    {"name": "Ochre", "hex": "#C89028"},

    # Row 4 (left to right from chart)
    {"name": "Silver", "hex": "#B0B0B0"},
    {"name": "Gold", "hex": "#D8B030"},
]

# =============================================================================
# TRANSPARENT PAINTS (20 paints) - from official transparent chart image
# Hex values sampled from bottom-left of each swatch
# =============================================================================
TRANSPARENT_PAINTS = [
    # Row 1 (left to right from chart)
    {"name": "Transparent Orange", "hex": "#F08048"},
    {"name": "Transparent Red", "hex": "#D84848"},
    {"name": "Transparent Brown", "hex": "#986048"},
    {"name": "Transparent Ochre", "hex": "#B89040"},
    {"name": "Transparent Yellow", "hex": "#F0E848"},
    {"name": "Transparent Olive", "hex": "#889048"},
    {"name": "Transparent Green", "hex": "#509058"},
    {"name": "Transparent Emerald", "hex": "#489078"},
    {"name": "Transparent Turquoise", "hex": "#489898"},
    {"name": "Transparent Blue", "hex": "#4880B0"},

    # Row 2 (left to right from chart)
    {"name": "Transparent Marine", "hex": "#405898"},
    {"name": "Transparent Violet", "hex": "#704898"},
    {"name": "Transparent Purple", "hex": "#984880"},
    {"name": "Transparent Pink", "hex": "#D87088"},
    {"name": "Transparent Flesh", "hex": "#C8A090"},
    {"name": "Transparent Cool Grey", "hex": "#788088"},
    {"name": "Transparent Warm Grey", "hex": "#888078"},
    {"name": "Transparent Blue Grey", "hex": "#607888"},
    {"name": "Transparent Black", "hex": "#282828"},
    {"name": "Transparent White", "hex": "#E8E8E8"},
]

# =============================================================================
# ACRYLIC INKS (22 paints) - from official inks chart image
# Hex values sampled from bottom-left of each swatch
# =============================================================================
INK_PAINTS = [
    # Row 1 (left to right from chart)
    {"name": "White", "hex": "#F0F0F0"},
    {"name": "Yellow", "hex": "#F8E810"},
    {"name": "Golden Yellow", "hex": "#F8C010"},
    {"name": "Orange", "hex": "#F87010"},

    # Row 2 (left to right from chart)
    {"name": "Scarlet", "hex": "#E82010"},
    {"name": "Carmine", "hex": "#B01030"},
    {"name": "Magenta", "hex": "#D01080"},
    {"name": "Purple Violet", "hex": "#8018A0"},

    # Row 3 (left to right from chart)
    {"name": "Violet", "hex": "#5010B0"},
    {"name": "Phthalo Blue", "hex": "#1020A0"},
    {"name": "Indigo", "hex": "#302878"},
    {"name": "Turquoise", "hex": "#10A898"},

    # Row 4 (left to right from chart)
    {"name": "Cyan Blue", "hex": "#1080C0"},
    {"name": "Phthalo Green", "hex": "#10A068"},
    {"name": "Sap Green", "hex": "#408028"},
    {"name": "Yellow Green", "hex": "#88C020"},

    # Row 5 (left to right from chart)
    {"name": "Olive Green", "hex": "#606830"},
    {"name": "Ochre", "hex": "#C09028"},
    {"name": "Burnt Sienna", "hex": "#C85018"},
    {"name": "Umber", "hex": "#584030"},

    # Row 6 (left to right from chart)
    {"name": "Sepia", "hex": "#806848"},
    {"name": "Black", "hex": "#101010"},
]

# =============================================================================
# GLAZES (20 paints) - from official glazes chart image
# Hex values sampled from bottom-left of each swatch
# =============================================================================
GLAZE_PAINTS = [
    # Row 1 (left to right from chart)
    {"name": "Yellow Glaze", "hex": "#F0E858"},
    {"name": "Skin Glaze", "hex": "#E8D098"},
    {"name": "Orange Glaze", "hex": "#E88048"},
    {"name": "Red Glaze", "hex": "#D84040"},

    # Row 2 (left to right from chart)
    {"name": "Flesh Glaze", "hex": "#E8C0B0"},
    {"name": "Beige Glaze", "hex": "#E0D8A0"},
    {"name": "Brown Glaze", "hex": "#987050"},
    {"name": "Wood Glaze", "hex": "#A87858"},

    # Row 3 (left to right from chart)
    {"name": "Undead Glaze", "hex": "#D0C8B8"},
    {"name": "Olive Glaze", "hex": "#C0D070"},
    {"name": "Khaki Glaze", "hex": "#A8A060"},
    {"name": "Green Glaze", "hex": "#58C858"},

    # Row 4 (left to right from chart)
    {"name": "Light Blue Glaze", "hex": "#78A8C8"},
    {"name": "Blue Glaze", "hex": "#3878B0"},
    {"name": "Violet Glaze", "hex": "#985898"},
    {"name": "Pink Glaze", "hex": "#E87898"},

    # Row 5 (left to right from chart)
    {"name": "Bone Glaze", "hex": "#E0D0B8"},
    {"name": "Warm Grey Glaze", "hex": "#989080"},
    {"name": "Blue Grey Glaze", "hex": "#8090A0"},
    {"name": "Cool Grey Glaze", "hex": "#888890"},
]

# =============================================================================
# FLUORESCENT PAINTS (7 paints) - from official fluorescent chart image
# Hex values sampled from bottom-left of each swatch
# =============================================================================
FLUORESCENT_PAINTS = [
    # Left to right from chart
    {"name": "Fluorescent Blue", "hex": "#1060F0"},
    {"name": "Fluorescent Green", "hex": "#40F010"},
    {"name": "Fluorescent Yellow", "hex": "#E8F010"},
    {"name": "Fluorescent Orange", "hex": "#F0A010"},
    {"name": "Fluorescent Red", "hex": "#F01050"},
    {"name": "Fluorescent Pink", "hex": "#F010B0"},
    {"name": "Fluorescent Violet", "hex": "#A010E0"},
]

# =============================================================================
# ANTITHESIS PAINTS (36 paints) - from official antithesis chart image
# Hex values sampled from bottom-left of each swatch
# =============================================================================
ANTITHESIS_PAINTS = [
    # Row 1 (left to right from chart)
    {"name": "Antithesis Yellow", "hex": "#F0E848"},
    {"name": "Antithesis Ochre", "hex": "#D0A038"},
    {"name": "Antithesis Orange", "hex": "#E87838"},
    {"name": "Antithesis Red", "hex": "#D83838"},
    {"name": "Antithesis Blood", "hex": "#981818"},
    {"name": "Antithesis Purple", "hex": "#982878"},

    # Row 2 (left to right from chart)
    {"name": "Antithesis Elf Flesh", "hex": "#F0D0B8"},
    {"name": "Antithesis Dwarf Flesh", "hex": "#D8A888"},
    {"name": "Antithesis Leather", "hex": "#986848"},
    {"name": "Antithesis Fur", "hex": "#885838"},
    {"name": "Antithesis Wood", "hex": "#805838"},
    {"name": "Antithesis Brown", "hex": "#603828"},

    # Row 3 (left to right from chart)
    {"name": "Antithesis Dead Flesh", "hex": "#B8C088"},
    {"name": "Antithesis Olive", "hex": "#788038"},
    {"name": "Antithesis Khaki", "hex": "#989858"},
    {"name": "Antithesis Green", "hex": "#38B838"},
    {"name": "Antithesis Goblinoid", "hex": "#589858"},
    {"name": "Antithesis Dark Green", "hex": "#185818"},

    # Row 4 (left to right from chart)
    {"name": "Antithesis Emerald", "hex": "#389878"},
    {"name": "Antithesis Water", "hex": "#58B8B8"},
    {"name": "Antithesis Turquoise", "hex": "#38A8A8"},
    {"name": "Antithesis Sky", "hex": "#78B8D8"},
    {"name": "Antithesis Blue", "hex": "#3878C8"},
    {"name": "Antithesis Ultramarine", "hex": "#2848A8"},

    # Row 5 (left to right from chart)
    {"name": "Antithesis Marine", "hex": "#283878"},
    {"name": "Antithesis Indigo", "hex": "#382878"},
    {"name": "Antithesis Violet", "hex": "#784898"},
    {"name": "Antithesis Pink", "hex": "#D87898"},
    {"name": "Antithesis Ultraviolet", "hex": "#B858C8"},
    {"name": "Antithesis Beige", "hex": "#D8D0B8"},

    # Row 6 (left to right from chart)
    {"name": "Antithesis Bone", "hex": "#E0D8C8"},
    {"name": "Antithesis Warm Grey", "hex": "#908878"},
    {"name": "Antithesis Blue Grey", "hex": "#708090"},
    {"name": "Antithesis Pale Grey", "hex": "#C0C0C0"},
    {"name": "Antithesis Cool Grey", "hex": "#888890"},
    {"name": "Antithesis Black", "hex": "#181818"},
]


def generate_sku(range_code: str, name: str) -> str:
    """Generate a SKU from range code and paint name."""
    # Clean name: uppercase, remove spaces/special chars
    clean = re.sub(r'[^A-Z0-9]', '', name.upper())
    return f"WC-{range_code}-{clean}"


def generate_id(range_type: str, name: str) -> str:
    """Generate a unique ID from range type and paint name."""
    clean = re.sub(r'[^a-z0-9]', '-', name.lower())
    clean = re.sub(r'-+', '-', clean).strip('-')
    return f"warcolours-{range_type}-{clean}"


def generate_paint_entry(
    paint: dict,
    range_name: str,
    range_type: str,
    range_code: str,
    url: str
) -> dict:
    """Generate a standard paint entry from static data."""
    name = paint['name']

    # Build brandData for layer paints
    brand_data = {}
    if 'colorFamily' in paint:
        brand_data['colorFamily'] = paint['colorFamily']
    if 'layer' in paint and paint['layer'] is not None:
        brand_data['layer'] = paint['layer']

    return {
        'brand': 'Warcolours',
        'brandData': brand_data,
        'category': '',
        'discontinued': False,
        'hex': paint['hex'],
        'id': generate_id(range_type, name),
        'impcat': {},
        'name': name,
        'range': range_name,
        'sku': generate_sku(range_code, name),
        'type': range_type,
        'url': url
    }


def generate_range(
    paints: list,
    range_name: str,
    range_type: str,
    range_code: str,
    output_file: str,
    output_dir: str = '.'
) -> list:
    """Generate all paint entries for a range and save to JSON."""
    url = BASE_URL + PRODUCT_URLS.get(range_type, '')

    entries = []
    for paint in paints:
        entry = generate_paint_entry(paint, range_name, range_type, range_code, url)
        entries.append(entry)

    # Sort by name
    entries.sort(key=lambda x: x['name'].lower())

    # Save to file
    output_path = f"{output_dir}/{output_file}" if output_dir != '.' else output_file
    with open(output_path, 'w') as f:
        json.dump(entries, f, indent=2)

    print(f"  {output_file}: {len(entries)} paints")
    return entries


def main():
    parser = argparse.ArgumentParser(
        description='Generate Warcolours paint data from static definitions',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Paint Ranges:
  Layer          96 paints (5-layer system)
  Metallic       26 paints
  One Coat       20 paints
  Transparent    20 paints
  Acrylic Inks   22 paints
  Glazes         20 paints
  Fluorescent     7 paints
  Antithesis     36 paints

Total: 247 paints
        """
    )
    parser.add_argument('--output-dir', '-o', default='.',
                       help='Output directory (default: current)')

    args = parser.parse_args()

    print("Warcolours Paint Data Generator")
    print("="*60)
    print("Generating paint data from official color charts...")
    print("="*60)

    total_paints = 0

    # Generate each range
    ranges = [
        (LAYER_PAINTS, "Layer", "layer", "LAY", "warcolours_layer.json"),
        (METALLIC_PAINTS, "Metallic", "metallic", "MET", "warcolours_metallic.json"),
        (ONECOAT_PAINTS, "One Coat", "opaque", "ONE", "warcolours_onecoat.json"),
        (TRANSPARENT_PAINTS, "Transparent", "transparent", "TRA", "warcolours_transparent.json"),
        (INK_PAINTS, "Ink", "ink", "INK", "warcolours_ink.json"),
        (GLAZE_PAINTS, "Glaze", "glaze", "GLA", "warcolours_glaze.json"),
        (FLUORESCENT_PAINTS, "Fluorescent", "fluorescent", "FLU", "warcolours_fluorescent.json"),
        (ANTITHESIS_PAINTS, "Antithesis", "antithesis", "ANT", "warcolours_antithesis.json"),
    ]

    for paints, range_name, range_type, range_code, output_file in ranges:
        entries = generate_range(
            paints, range_name, range_type, range_code, output_file, args.output_dir
        )
        total_paints += len(entries)

    print("="*60)
    print(f"Total: {total_paints} paints generated")
    print("="*60)

    return 0


if __name__ == '__main__':
    exit(main())
