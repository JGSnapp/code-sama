"""Generate per-biome wall and floor tiles + the forest tree sprite.

The generated sprWall{n}Bot.png is also copied as sprWall{n}Top.png so the
TerrainAtlas in ChunkView.cs has both. Floor tiles are 32x32, walls 16x16
(matching the project's mskFloor / mskWall size convention).
"""

import base64
import shutil
import sys
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image
from openai import OpenAI

SCRIPT_DIR = Path(__file__).resolve().parent
SPRITES_OUT = SCRIPT_DIR / "Assets" / "StreamingAssets" / "sprites"

CLIENT = OpenAI(
    api_key="sk-lirm4GErQzCo6eOVH2oR4XGfaiQq7vrD",
    base_url="https://api.proxyapi.ru/openai/v1",
)
MODEL = "gpt-image-2"
BACKGROUND_TOLERANCE = 35

ITEMS = [
    # Walls — Bot variant only; we copy it as Top to keep the atlas happy.
    {
        "sprite": "sprWall1Bot",
        "size": (16, 16),
        "model_size": "1024x1024",
        "prompt": "Seamless top-down 16x16 pixel-art tile of loose underground earth: warm brown soil with small dark pebbles and tiny visible roots. Slight color variation, no centerpiece, tileable, no border.",
    },
    {
        "sprite": "sprWall2Bot",
        "size": (16, 16),
        "model_size": "1024x1024",
        "prompt": "Seamless top-down 16x16 pixel-art tile of root-laced underground wall: dark brown packed earth with thick interweaving tree roots in lighter brown, a few green moss specks. Tileable, no border.",
    },
    {
        "sprite": "sprWall3Bot",
        "size": (16, 16),
        "model_size": "1024x1024",
        "prompt": "Seamless top-down 16x16 pixel-art tile of grey cave stone: cracked rocky surface, two or three darker fissures, a few lighter highlight specks. Tileable, no border.",
    },
    {
        "sprite": "sprWall4Bot",
        "size": (16, 16),
        "model_size": "1024x1024",
        "prompt": "Seamless top-down 16x16 pixel-art tile of dark hard stone with ore: deep slate-grey rock with small rust-orange and copper-orange ore flecks embedded. Tileable, no border.",
    },
    {
        "sprite": "sprWall5Bot",
        "size": (16, 16),
        "model_size": "1024x1024",
        "prompt": "Seamless top-down 16x16 pixel-art tile of indestructible obsidian bedrock: very dark almost-black volcanic rock with thin glowing red-magenta crystalline veins crossing through. Sharp angular cracks. Tileable, no border, no centerpiece.",
    },
    # Floors — 32x32 per project convention.
    {
        "sprite": "sprFloor1",
        "size": (32, 32),
        "model_size": "1024x1024",
        "prompt": "Seamless top-down 32x32 pixel-art floor tile of damp dark underground forest soil: VERY dark brown almost black earth with scattered small grey pebbles and a few sparse darker leaves. The base color must be much darker than typical dirt tiles, near-black brown. Tileable, no border, no centerpiece.",
    },
    {
        "sprite": "sprFloor2",
        "size": (32, 32),
        "model_size": "1024x1024",
        "prompt": "Seamless top-down 32x32 pixel-art floor tile of deep forest mossy ground: VERY dark brown soil base with patches of muted green moss and a few small dark mushroom dots. Floor must be significantly darker than the loose-earth wall tile. Tileable, no border.",
    },
    {
        "sprite": "sprFloor3",
        "size": (32, 32),
        "model_size": "1024x1024",
        "prompt": "Seamless top-down 32x32 pixel-art floor tile of cave stone floor: grey stone slabs with cracks and faint glow mushroom dots in cyan-green. Tileable, no border.",
    },
    {
        "sprite": "sprFloor4",
        "size": (32, 32),
        "model_size": "1024x1024",
        "prompt": "Seamless top-down 32x32 pixel-art floor tile of mine shaft floor: weathered wooden planks running horizontally with iron nail dots and a thin layer of grey dust on top. Tileable, no border.",
    },
    # Forest tree (proper tree, not a mushroom).
    {
        "sprite": "sprForestTree",
        "size": (24, 32),
        "model_size": "1024x1536",
        "prompt": "Top-down pixel-art view of an underground forest tree: a thick gnarled dark brown trunk in the lower third, dense round canopy of dark green leaves in the upper two thirds with a few brighter green highlights. Visible exposed roots at the base. Strong silhouette.",
    },
]


OUTLINE_COLOR = (8, 8, 12, 255)
ALPHA_THRESHOLD = 32


def add_outline(image):
    """Plant a black outline pixel around every silhouette pixel."""
    image = image.convert("RGBA")
    w, h = image.size
    src = image.load()
    out = image.copy()
    dst = out.load()
    for y in range(h):
        for x in range(w):
            if src[x, y][3] >= ALPHA_THRESHOLD:
                continue
            adj = False
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if nx < 0 or ny < 0 or nx >= w or ny >= h:
                        continue
                    if src[nx, ny][3] >= ALPHA_THRESHOLD:
                        adj = True; break
                if adj: break
            if adj:
                dst[x, y] = OUTLINE_COLOR
    return out


def color_distance(a, b):
    return sum(abs(a[i] - b[i]) for i in range(3))


def remove_edge_background(image, tolerance=BACKGROUND_TOLERANCE):
    image = image.convert("RGBA")
    pixels = image.load()
    width, height = image.size
    bg = [pixels[0, 0], pixels[width - 1, 0], pixels[0, height - 1], pixels[width - 1, height - 1]]
    stack = []
    visited = set()
    for x in range(width):
        stack.append((x, 0)); stack.append((x, height - 1))
    for y in range(height):
        stack.append((0, y)); stack.append((width - 1, y))
    while stack:
        x, y = stack.pop()
        if (x, y) in visited: continue
        visited.add((x, y))
        c = pixels[x, y]
        if not any(color_distance(c, b) <= tolerance for b in bg): continue
        pixels[x, y] = (c[0], c[1], c[2], 0)
        if x > 0: stack.append((x - 1, y))
        if x < width - 1: stack.append((x + 1, y))
        if y > 0: stack.append((x, y - 1))
        if y < height - 1: stack.append((x, y + 1))
    return image


def build_prompt(entry):
    w, h = entry["size"]
    is_tileable = "Seamless" in entry["prompt"]
    if is_tileable:
        # tileable: do not strip background
        return f"""
{entry["prompt"].strip()}

Output exactly a {w}x{h} pixel-art tile. The image must be tileable when repeated:
no border, no vignette, no centered object. Limited palette, crisp hard-edged pixels,
no anti-aliasing or blur, no text or watermark.
""".strip()
    else:
        return f"""
Create a pixel-art sprite at exactly {w}x{h} pixels.
{entry["prompt"].strip()}

Drawn entirely on a single flat solid background color (no shadows, gradients, scenery,
text, border, or outline outside the silhouette). Crisp hard-edged pixels, limited
palette, strong silhouette, no anti-aliasing. Center on canvas with a small margin.
""".strip()


def write_yy(target_dir, name, w, h, frame_uuid):
    yy = f'''{{
  "$GMSprite":"v2",
  "%Name":"{name}",
  "bboxMode":0,
  "bbox_bottom":{h - 1},
  "bbox_left":0,
  "bbox_right":{w - 1},
  "bbox_top":0,
  "collisionKind":1,
  "collisionTolerance":0,
  "DynamicTexturePage":false,
  "edgeFiltering":false,
  "For3D":false,
  "frames":[
    {{"$GMSpriteFrame":"v2","%Name":"{frame_uuid}","name":"{frame_uuid}","resourceVersion":"2.0","tags":[]}}
  ],
  "gridX":0,
  "gridY":0,
  "height":{h},
  "HTile":false,
  "layers":[],
  "name":"{name}",
  "origin":4,
  "parent":{{"name":"Sprites","path":"folders/Sprites.yy"}},
  "preMultiplyAlpha":false,
  "resourceType":"GMSprite",
  "resourceVersion":"2.0",
  "swatchColours":null,
  "swfPrecision":2.525,
  "tags":[],
  "textureGroupId":{{"name":"Default","path":"texturegroups/Default"}},
  "type":0,
  "VTile":false,
  "width":{w},
  "xorigin":{w // 2},
  "yorigin":{h // 2}
}}'''
    (target_dir / f"{name}.yy").write_text(yy, encoding="utf-8")


def generate_one(entry):
    name = entry["sprite"]
    w, h = entry["size"]
    target_dir = SPRITES_OUT / name
    yy_path = target_dir / f"{name}.yy"
    if yy_path.exists() and "--force" not in sys.argv:
        print(f"[skip] {name}")
        return
    print(f"[gen] {name}  ({w}x{h})")

    result = CLIENT.images.generate(
        model=MODEL,
        prompt=build_prompt(entry),
        quality="low",
        size=entry["model_size"],
    )
    image = Image.open(BytesIO(base64.b64decode(result.data[0].b64_json)))
    is_tile = "Seamless" in entry["prompt"]
    if not is_tile:
        image = remove_edge_background(image)
    image = image.resize((w, h), Image.Resampling.NEAREST)
    if not is_tile:
        image = add_outline(image)

    target_dir.mkdir(parents=True, exist_ok=True)
    for p in target_dir.glob("*"):
        if p.is_file(): p.unlink()
    frame_uuid = str(uuid.uuid4())
    image.save(target_dir / f"{frame_uuid}.png", format="PNG")
    write_yy(target_dir, name, w, h, frame_uuid)

    # For wall sprites we also need a Top variant; duplicate the Bot.
    if name.endswith("Bot"):
        top_name = name[:-3] + "Top"
        top_dir = SPRITES_OUT / top_name
        top_dir.mkdir(parents=True, exist_ok=True)
        for p in top_dir.glob("*"):
            if p.is_file(): p.unlink()
        top_uuid = str(uuid.uuid4())
        image.save(top_dir / f"{top_uuid}.png", format="PNG")
        write_yy(top_dir, top_name, w, h, top_uuid)
        print(f"      also wrote {top_name}")


def main():
    SPRITES_OUT.mkdir(parents=True, exist_ok=True)
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    for entry in ITEMS:
        if only and entry["sprite"] not in only:
            continue
        try:
            generate_one(entry)
        except Exception as exc:
            print(f"      FAILED: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
