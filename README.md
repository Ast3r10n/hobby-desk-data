# Hobby Desk Data

A comprehensive database of hobby and miniature painting products, including paints from major brands used in the tabletop gaming and modeling community.

## View the Database

**[Browse paints online](https://alexparlett.github.io/hobby-desk-data/)**

## What's Included

- **3,140+ paints** across 7 major brands
- Color hex codes for digital reference
- Product categories (Base, Layer, Wash, Contrast, etc.)
- Price information where available

### Brands

| Brand | Paint Count |
|-------|-------------|
| Vallejo | 995 |
| AK Interactive | 862 |
| The Army Painter | 557 |
| Games Workshop | 334 |
| Two Thin Coats | 181 |
| Monument Hobbies | 179 |
| Colour Forge | 32 |

## Data Format

Paint data is stored as JSON files organized by brand. Each paint entry includes:

```json
{
  "id": "unique-identifier",
  "name": "Paint Name",
  "brand": "Brand Name",
  "range": "Product Range",
  "hex": "#RRGGBB",
  "category": "Base|Layer|Wash|etc",
  "discontinued": false
}
```

## Using the Data

The `manifest.json` file provides an index of all paint files with SHA256 hashes for integrity verification.

```javascript
const manifest = await fetch('https://alexparlett.github.io/hobby-desk-data/manifest.json').then(r => r.json());
```

## Contributing

Contributions are welcome. Please ensure paint data follows the existing JSON schema.

## License

Data is provided for reference purposes. Paint names, brands, and product information are trademarks of their respective owners.
