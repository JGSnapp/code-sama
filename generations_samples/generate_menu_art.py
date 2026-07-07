"""Generate the main-menu background and the game logo banner.

The background is rendered at a wide-ish ratio and saved as a tileable-ish PNG that
MainMenu stretches to fit the screen. The logo is a short banner with the game title.
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

ITEMS = [
    {
        "sprite": "sprMenuBackground",
        "size": (480, 270),                # 16:9 downscaled
        "model_size": "1536x1024",
        "prompt": "Wide cinematic pixel-art scene of a dark underground cavern entrance. A jagged stone archway in the foreground leads into deep blackness, illuminated only by two glowing yellow lantern bugs and a faint magenta crystalline vein running along the wall. Distant silhouettes of mossy trees with twisted roots, scattered bones and broken minecart parts on the ground. Atmospheric, moody, slightly foggy. Subdued palette of dark earth tones, blacks, greens and warm lantern highlights. Detailed retro pixel art, strong shape language, no text.",
    },
    {
        "sprite": "sprGameLogo",
        "size": (320, 96),
        "model_size": "1536x1024",
        "prompt": "Pixel-art game logo banner reading 'DIGGER PROTOTYPE' in chunky retro letters. Warm yellow gold lettering with a subtle stone-grey outline, slightly weathered. The letters could rest on a thin pickaxe-and-shovel crossed motif beneath them. Centered on a flat dark background so the letters pop. No background scene, no extra text.",
    },
]


def color_distance(a, b):
    return sum(abs(a[i] - b[i]) for i in range(3))


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
        print(f"[skip] {name}")
        return
    print(f"[gen] {name}  ({w}x{h})")

    result = CLIENT.images.generate(
        model=MODEL,
        prompt=entry["prompt"],
        quality="low",
        size=entry["model_size"],
    )
    image = Image.open(BytesIO(base64.b64decode(result.data[0].b64_json)))
    # Keep aspect; resize directly (background is intended to be stretched anyway).
    image = image.resize((w, h), Image.Resampling.NEAREST)
    image = image.convert("RGBA")

    target_dir.mkdir(parents=True, exist_ok=True)
    for p in target_dir.glob("*"):
        if p.is_file(): p.unlink()
    frame_uuid = str(uuid.uuid4())
    image.save(target_dir / f"{frame_uuid}.png", format="PNG")
    write_yy(target_dir, name, w, h, frame_uuid)


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
