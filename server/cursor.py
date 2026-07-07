"""Human-like cursor path + typing rhythm helpers.

The frontend does the actual interpolation, but the backend decides where the
path goes, how many segments it has, and how long each leg lasts. Long jumps
are broken into 2–3 short Bezier hops with a tiny overshoot so the motion
looks alive instead of robotic.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class CursorLeg:
    """One Bezier hop the client animates."""

    from_x: float
    from_y: float
    to_x: float
    to_y: float
    ctrl1_x: float
    ctrl1_y: float
    ctrl2_x: float
    ctrl2_y: float
    duration_ms: int


def plan_path(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    speed_px_per_s: float = 1200.0,
    min_leg_ms: int = 90,
    max_leg_ms: int = 480,
) -> list[CursorLeg]:
    """Break the journey into 1-3 organic Bezier legs."""

    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    dist = math.hypot(dx, dy)
    if dist < 1.0:
        return []

    # Decide leg count from distance — short hops stay one leg, long jumps get
    # an arc and a settle.
    if dist < 120:
        n_legs = 1
    elif dist < 420:
        n_legs = 2
    else:
        n_legs = 3

    # Generate waypoints along the straight line then nudge them sideways for
    # an arc.
    nx, ny = -dy / dist, dx / dist  # unit normal
    waypoints: list[tuple[float, float]] = [start]
    for i in range(1, n_legs):
        t = i / n_legs
        bx = sx + dx * t
        by = sy + dy * t
        # arc amplitude scales with distance, alternates side
        amp = (random.random() * 0.18 + 0.05) * dist * (1 if i % 2 else -1)
        waypoints.append((bx + nx * amp, by + ny * amp))
    # Slight overshoot then settle on the long final hop.
    if n_legs >= 2 and dist > 200 and random.random() < 0.55:
        overshoot = random.uniform(0.04, 0.09)
        ox = ex + dx / dist * overshoot * 18
        oy = ey + dy / dist * overshoot * 18
        waypoints.append((ox, oy))
    waypoints.append(end)

    legs: list[CursorLeg] = []
    for a, b in zip(waypoints, waypoints[1:]):
        seg_dist = math.hypot(b[0] - a[0], b[1] - a[1])
        dur = int(max(min_leg_ms, min(max_leg_ms, seg_dist / speed_px_per_s * 1000)))
        # Control points: 30%/70% along the segment, nudged orthogonally.
        seg_dx, seg_dy = b[0] - a[0], b[1] - a[1]
        seg_len = max(1.0, math.hypot(seg_dx, seg_dy))
        nnx, nny = -seg_dy / seg_len, seg_dx / seg_len
        wobble = random.uniform(0.04, 0.22) * seg_len
        sign = random.choice((-1, 1))
        c1 = (
            a[0] + seg_dx * 0.30 + nnx * wobble * sign,
            a[1] + seg_dy * 0.30 + nny * wobble * sign,
        )
        c2 = (
            a[0] + seg_dx * 0.70 + nnx * wobble * sign * 0.6,
            a[1] + seg_dy * 0.70 + nny * wobble * sign * 0.6,
        )
        legs.append(
            CursorLeg(
                from_x=a[0],
                from_y=a[1],
                to_x=b[0],
                to_y=b[1],
                ctrl1_x=c1[0],
                ctrl1_y=c1[1],
                ctrl2_x=c2[0],
                ctrl2_y=c2[1],
                duration_ms=dur,
            )
        )
    return legs


def typing_delays(text: str) -> list[int]:
    """Per-character delays in ms. Mimics burst-then-pause keyboard rhythm."""

    delays: list[int] = []
    burst = 0
    for ch in text:
        if ch == "\n":
            delays.append(random.randint(180, 320))
            burst = 0
            continue
        if ch == " ":
            delays.append(random.randint(60, 130))
            burst = 0
            continue
        base = random.randint(45, 110)
        # Occasional thinking pause.
        if random.random() < 0.04:
            base += random.randint(150, 380)
        # Burst rhythm: long runs slow down slightly.
        burst += 1
        if burst > 6:
            base += random.randint(20, 60)
        if ch.isupper():
            base += random.randint(20, 50)
        delays.append(base)
    return delays
