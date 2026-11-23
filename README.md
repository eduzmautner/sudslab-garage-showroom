# SudsLab Virtual Garage

Interactive 3D showroom for suds-lab.com (Shopify). The garage is built in
Blender, its lighting is baked into textures, and the browser displays the
result with three.js.

## Run the web viewer

```bash
npm install
npm run dev      # http://localhost:5183
npm run build    # dist/ for the Shopify theme
```

## Pipeline

```
blender/build_garage.py  ->  blender/garage.blend          (scene, generated from code)
blender/bake_export.py   ->  public/garage/*                (what the browser loads)
```

```bash
# 1. (re)generate the scene - overwrites garage.blend, ~30 s
blender -b --factory-startup -P blender/build_garage.py -- --render

# 2. bake lighting and export for the web
blender -b --factory-startup -P blender/bake_export.py -- --res=5472 --samples=128   # about 1 h 45 min
#    quick look: --res=1024 --samples=12 (about 30 s)
```

`bake_export.py` only reads `garage.blend`, so it also works on a hand-edited
file as long as the collection names (Shell, Furniture, Props, Live props, Lights,
Exterior, Products, Slots) and the `slot_<id>` empties are kept.

## Files

| File | What it is |
| --- | --- |
| `blender/build_garage.py` | Builds the whole scene. Layout is in viewer coordinates (x right, y up, z toward the door, metres). |
| `blender/bake_export.py` | Joins the static scene into three meshes, bakes light into atlases, exports GLBs + environment capture. |
| `blender/3D assets/` | Supplied models (spray bottle, stacked tyres). |
| `blender/slots.json` | The 18 product positions. Copied to `public/garage/`. |
| `public/garage/static.glb` | Room, furniture, props, exterior with baked lighting (displayed unlit). |
| `public/garage/products.glb` | Products with ordinary materials, lit in the browser by `env.hdr`. |
| `public/garage/static-lite.glb`, `products-lite.glb` | The same two files with every picture shrunk, for phones and tablets. |
| `src/garage.js` | Loads the files above. |
| `src/showroom.js` | Renderer, camera (drag-look + floor waypoints), render-on-demand loop. |
| `src/ui.js`, `src/showroom.css` | Hotspot buttons, product panel, fullscreen/zoom. All CSS scoped under `.sl-showroom`. |
| `src/products.js` | Dev snapshot of 18 real SudsLab products. On Shopify, live data replaces it. |
| `shopify/sections/virtual-showroom.liquid` | Theme section; merchants assign a product to each slot in the theme editor. |
| `index.html` | Local stand-in for the storefront page around the section. |

## Lighting

Cycles path tracing: a sun lamp at 21 degrees elevation through the 75%-open
door, a sky-blue world, and three pendant bulbs. The bake keeps diffuse light
only (direct + bounced + emission) with the scene exposure and AgX look
applied, so surfaces have no live reflections and metals are baked as plain
grey. Products are not baked; they cast baked shadows but are lit live from a
360 capture of the room.

## Phones and tablets

The full files unpack to about 1.2 GB of graphics memory, nearly all of it
textures, and a phone's browser closes the page before that. `bake_export.py`
therefore also writes a light set (baked sheets at 3/8 size, product artwork
at most 1024 px, about 200 MB in memory), and `pickQuality` in `src/garage.js`
loads it on touch-only devices, on devices reporting 4 GB of memory or less,
and on graphics chips that cannot hold a full-size sheet. Add `?garage=lite`
or `?garage=full` to the address to force one. To write the files again
without rebaking anything:

```bash
blender -b --factory-startup -P blender/bake_export.py -- --res=5472 --only=none
```

## Not in this repository

`blender/3D assets/` (the supplied spray-bottle, tyre and cart models) and the
reference images are kept out of version control. `build_garage.py` needs the
three OBJ files in that folder to regenerate `garage.blend`; the committed
`garage.blend` and `public/garage/*` already contain the processed results, so
the web viewer and `bake_export.py` work without them.
