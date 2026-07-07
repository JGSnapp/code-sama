"""Generate sprites for the gated mini-bosses introduced by the biome chain rework."""

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

BOSSES = [
    {
        "sprite": "sprEarthWormHead",
        "size": (16, 16),
        "model_size": "1024x1024",
        "prompt": "Top-down pixel-art worm head: round circular shape, fleshy pale pink body. Two tiny black eyes near the top, a large open circular mouth at the front with 4 small white triangular teeth around the rim. A few darker pink spots on the body for texture. Pointing toward the right.",
    },
    {
        "sprite": "sprEarthWormBody",
        "size": (16, 16),
        "model_size": "1024x1024",
        "prompt": "Top-down pixel-art worm body segment: a round circular bump, fleshy pale pink, slightly darker rim outline, two or three small darker spots scattered on top. Symmetric, no eyes, no mouth. Looks like a single chubby segment.",
    },
    {
        "sprite": "sprBoarLord",
        "size": (28, 20),
        "model_size": "1536x1024",
        "prompt": "Top-down pixel-art ancient giant wild boar with thick scarred grey-brown hide and a heavy bony ridge along the spine. Massive curved ivory tusks, glowing red eyes, four heavy hooves. A small skull trinket hangs from a collar. Looks dangerous.",
    },
    {
        "sprite": "sprStoneSentinel",
        "size": (28, 28),
        "model_size": "1024x1024",
        "prompt": "Top-down pixel-art stone sentinel: a tall narrow pyramid made of carved stone blocks with cyan glowing crystal cores embedded on four sides. A single glowing eye on top emitting a slight beam. No legs, sits on the ground. Ancient runes etched on the stone.",
    },
    {
        "sprite": "sprArmoredBrigadier",
        "size": (24, 28),
        "model_size": "1024x1536",
        "prompt": "Top-down pixel-art shielded zombie brigadier in heavy rusted steel plate armor with rivets. Holding a huge angled steel slab shield in front like a wall, pale undead face barely visible above the shield rim. Glowing yellow eyes. Bulky and slow.",
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

Drawn entirely on a single flat solid background color. No shadows, gradients, scenery,
text, border, or outline outside the silhouette. Crisp hard-edged pixels, limited palette,
strong silhouette, no anti-aliasing. Center on canvas with a small margin of background.
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
    if (target_dir / f"{name}.yy").exists() and "--force" not in sys.argv:
        print(f"[skip] {name} (use --force)")
        return
    print(f"[gen] {name}  ({w}x{h})")

    result = CLIENT.images.generate(
        model=MODEL,
        prompt=build_prompt(entry),
        quality="low",
        size=entry["model_size"],
    )
    image = Image.open(BytesIO(base64.b64decode(result.data[0].b64_json)))
    image = remove_edge_background(image)
    image = image.resize((w, h), Image.Resampling.NEAREST)
    image = add_outline(image)

    target_dir.mkdir(parents=True, exist_ok=True)
    for p in target_dir.glob("*"):
        if p.is_file():
            p.unlink()
    frame_uuid = str(uuid.uuid4())
    image.save(target_dir / f"{frame_uuid}.png", format="PNG")
    write_yy(target_dir, name, w, h, frame_uuid)


def main():
    SPRITES_OUT.mkdir(parents=True, exist_ok=True)
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    for entry in BOSSES:
        if only and entry["sprite"] not in only:
            continue
        try:
            generate_one(entry)
        except Exception as exc:
            print(f"      FAILED: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
