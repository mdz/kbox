# kbox branding

- `brand.html` - the brand reference: the mark, palette, type, and applied
  mockups (favicon, home screen, GitHub card). Open it directly in a browser.
- `generate_icons.py` - regenerates the app icon files
  (`apple-touch-icon.png`, `apple-touch-icon-precomposed.png`,
  `favicon.ico`) into `kbox/web/static/`. Run with
  `uv run python docs/branding/generate_icons.py` from the repo root.
- `assets/` - a static copy of the current shipped icon files, for reference
  outside of `kbox/web/static/`.

## The mark

A symmetrical K - one continuous crossbar with two equal-angle legs
branching from its center - canted 35 degrees, cradling the mic head with a
small gap between them. All geometry is defined in a 64x64 unit grid at the
top of `generate_icons.py`.

## Palette

| Name           | Hex       | Use                                  |
| -------------- | --------- | ------------------------------------- |
| Marquee violet | `#B06BFF` | Primary brand color, icon background |
| Hot mic pink   | `#FF3FA4` | Accent, used sparingly               |
| Stage          | `#180F26` | Dark surface                         |
| Void           | `#100819` | Darkest background                   |
| Spotlight      | `#F3ECFF` | Light text / mark fill               |

## Type

Baloo 2 (headlines, wordmark only) paired with Manrope (UI text) and Space
Mono (data/timestamps). See `brand.html` for the full specimen.
