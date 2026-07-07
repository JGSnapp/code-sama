"""
Batch-generate sprites for crafting stations and world props.

Same machinery as generate_enemies.py; targets a different sprite list.
"""

import base64
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

STRUCTURES = [
    # Crafting stations
    {
        "sprite": "sprCampfire",
        "size": (24, 24),
        "model_size": "1024x1024",
        "prompt": "Pixel-art top-down campfire structure. Charred logs arranged in a small pile, bright orange and yellow flame on top, faint glowing embers. Strong silhouette.",
    },
    {
        "sprite": "sprSurvivorBench",
        "size": (32, 24),
        "model_size": "1536x1024",
        "prompt": "Pixel-art top-down workbench made of rough wooden planks with a few crude tools on top — a hammer head and a small saw. Dark brown wood with lighter highlights.",
    },
    {
        "sprite": "sprStoneFurnace",
        "size": (24, 32),
        "model_size": "1024x1536",
        "prompt": "Pixel-art top-down stone furnace built from grey rocks, with a black open mouth at the front glowing orange-red from inside. Some smoke wisps drawn as faint grey pixels rising from the chimney.",
    },
    {
        "sprite": "sprAlchemyTable",
        "size": (32, 24),
        "model_size": "1536x1024",
        "prompt": "Pixel-art top-down alchemy table — wooden table covered in small glass flasks of bright green and purple liquid, a small candle, a mortar and pestle.",
    },
    {
        "sprite": "sprWeaponsmithBench",
        "size": (32, 24),
        "model_size": "1536x1024",
        "prompt": "Pixel-art top-down weaponsmith bench — iron-clad workbench with an anvil on one side, a vice on the other, a hammer and tongs on top, a small grinding wheel.",
    },

    # Structures
    {
        "sprite": "sprBigBox",
        "size": (24, 24),
        "model_size": "1024x1024",
        "prompt": "Pixel-art top-down view of a sturdy wooden crate with iron corner reinforcements and visible plank seams. Warm brown wood, darker corners.",
    },
    {
        "sprite": "sprBox",
        "size": (20, 24),
        "model_size": "1024x1536",
        "prompt": "Pixel-art top-down old wooden barrel with two darker iron rings around it. Faded planks, slightly weathered.",
    },
    {
        "sprite": "sprPropMinecart",
        "size": (32, 24),
        "model_size": "1536x1024",
        "prompt": "Pixel-art side view of an old rusty steel minecart with two iron wheels and a dented open top. Brownish-grey iron palette.",
    },
    {
        "sprite": "sprBoneIdle",
        "size": (24, 20),
        "model_size": "1536x1024",
        "prompt": "Pixel-art top-down pile of yellowed bones with one cracked skull on top, eye sockets dark. A few ribs and femurs sticking out.",
    },
    {
        "sprite": "sprMushroomBig",
        "size": (24, 28),
        "model_size": "1024x1536",
        "prompt": "Pixel-art underground mushroom — thick pale stem and a large dark red cap with light spots. Subtle bioluminescent glow on the underside.",
    },
    {
        "sprite": "sprMineSign",
        "size": (20, 28),
        "model_size": "1024x1536",
        "prompt": "Pixel-art rusty mine sign — a wooden post with a small rectangular plank nailed near the top, slightly tilted, faded paint, vertical wood grain.",
    },
    {
        "sprite": "sprBush",
        "size": (24, 16),
        "model_size": "1536x1024",
        "prompt": "Pixel-art top-down small dense bush with dark green leaves and a few bright red berries scattered in the foliage.",
    },
    {
        "sprite": "sprIronLocker",
        "size": (20, 28),
        "model_size": "1024x1536",
        "prompt": "Pixel-art rusty steel locker with a vertical seam down the middle, a small yellow handle on the right door, a few bolt heads visible. Grey-brown rusted metal.",
    },
    {
        "sprite": "sprBigGenerator",
        "size": (28, 24),
        "model_size": "1536x1024",
        "prompt": "Pixel-art top-down view of an old electrical generator — a steel box with copper coils on the top, two ceramic insulators on the sides, faint cyan electric arcs between the coils.",
    },
]


OUTLINE_COLOR = (8, 8, 12, 255)
ALPHA_THRESHOLD = 32


def add_outline(image):
    image = image.convert("RGBA")
    w, h = image.size
    src = image.load()
    out = image.copy()
    dst = out.load()
    for y in range(h):
        for x in range(w):
            if src[x, y][3] >= ALPHA_THRESHOLD: continue
            adj = False
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0: continue
                    nx, ny = x + dx, y + dy
                    if nx < 0 or ny < 0 or nx >= w or ny >= h: continue
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
        if (x, y) in visited:
            continue
        visited.add((x, y))
        c = pixels[x, y]
        if not any(color_distance(c, b) <= tolerance for b in bg):
            continue
        pixels[x, y] = (c[0], c[1], c[2], 0)
        if x > 0: stack.append((x - 1, y))
        if x < width - 1: stack.append((x + 1, y))
        if y > 0: stack.append((x, y - 1))
        if y < height - 1: stack.append((x, y + 1))
    return image


def build_prompt(entry):
    w, h = entry["size"]
    return f"""
Create a pixel-art sprite at exactly {w}x{h} pixels.
{entry["prompt"].strip()}

The sprite must be drawn entirely on a single flat solid background color.
No shadows, gradients, scenery, text, border, or outline outside the silhouette.
Crisp hard-edged pixels, limited palette, strong silhouette, no anti-aliasing.
Center the object on the canvas with a small margin of background around it.
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
    print(f"[gen] {name}  ({w}x{h})")
    target_dir = SPRITES_OUT / name
    yy_path = target_dir / f"{name}.yy"
    if yy_path.exists() and "--force" not in sys.argv:
        print("      already exists, skipping (pass --force to regenerate)")
        return

    result = CLIENT.images.generate(
        model=MODEL,
        prompt=build_prompt(entry),
        quality="low",
        size=entry["model_size"],
    )
    image_b64 = result.data[0].b64_json
    image = Image.open(BytesIO(base64.b64decode(image_b64)))
    image = remove_edge_background(image)
    image = image.resize((w, h), Image.Resampling.NEAREST)
    image = add_outline(image)

    target_dir.mkdir(parents=True, exist_ok=True)
    for p in target_dir.glob("*"):
        if p.is_file():
            p.unlink()
    frame_uuid = str(uuid.uuid4())
    png_path = target_dir / f"{frame_uuid}.png"
    image.save(png_path, format="PNG")
    write_yy(target_dir, name, w, h, frame_uuid)
    print(f"      -> {png_path.name}")


def main():
    SPRITES_OUT.mkdir(parents=True, exist_ok=True)
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    for entry in STRUCTURES:
        if only and entry["sprite"] not in only:
            continue
        try:
            generate_one(entry)
        except Exception as exc:
            print(f"      FAILED: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
