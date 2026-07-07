"""Generate the Kawaii hacker room used as the backdrop behind the VRM webcam.

Same pattern as ``generate_art_example.py`` — gpt-image-2 via ProxyAPI, save
locally, cache on disk so we don't regenerate on every start. Run once:

    .venv\\Scripts\\python.exe tools\\gen_room_bg.py            # generate if missing
    .venv\\Scripts\\python.exe tools\\gen_room_bg.py --force    # always regenerate
"""

from __future__ import annotations

import base64
import os
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

try:
    from openai import OpenAI
except ImportError:
    sys.exit("Please `pip install openai pillow` first.")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

API_KEY = os.environ.get("PROXY_API_KEY") or "sk-lirm4GErQzCo6eOVH2oR4XGfaiQq7vrD"
BASE_URL = os.environ.get(
    "PROXY_IMAGE_BASE_URL", "https://api.proxyapi.ru/openai/v1"
)
MODEL = os.environ.get("PROXY_IMAGE_MODEL", "gpt-image-2")

OUT = Path(__file__).resolve().parent.parent / "web" / "assets" / "room_bg.png"
TARGET_SIZE = (640, 480)
MODEL_SIZE = "1024x1024"

PROMPT = (
    "Cozy bedroom of a young female AI streamer-programmer, viewed straight on as "
    "the background of a webcam shot — empty center of frame for the streamer, "
    "no character in the image. Warm dim light from a pink neon sign on the back "
    "wall reading nothing, faint magenta glow on the walls. A small CRT-style "
    "monitor on a wooden desk in the lower left, glowing softly. Tall white shelves "
    "on the right packed with manga, plush rabbits, and tiny mechanical figures, "
    "fairy-lights wrapped around the books. Soft pastel pink and lilac color "
    "palette, hints of dark teal in the corners, cinematic shallow depth of field, "
    "very slightly blurry to keep the avatar in focus. Cute kawaii hacker aesthetic, "
    "no text, no people, no logos, no faces."
)


def main() -> None:
    if OUT.exists() and "--force" not in sys.argv:
        print(f"[skip] {OUT} already exists. Pass --force to regenerate.")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    print(f"[gen] requesting {MODEL} @ {MODEL_SIZE}")
    result = client.images.generate(
        model=MODEL,
        prompt=PROMPT,
        quality="medium",
        size=MODEL_SIZE,
    )
    raw = result.data[0].b64_json
    image = Image.open(BytesIO(base64.b64decode(raw))).convert("RGB")
    image = image.resize(TARGET_SIZE, Image.Resampling.LANCZOS)
    image.save(OUT, format="PNG", optimize=True)
    print(f"[ok] wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
