# SPDX-License-Identifier: GPL-3.0-or-later

"""Rope dynamics for live wires: Verlet integration with rope constraints,
bending stiffness, damping, collisions and a settle pull toward the rest shape.

Pure Python on [x, y, z] lists so it can be tested without Blender.
Gravity points down the world Z axis.
"""

import math

GRAVITY = 9.81
# Bending force per unit of curvature offset at stiffness 1, in 1/s^2.
BEND_STRENGTH = 2000.0
# Share of sliding speed lost per substep while touching a collider.
CONTACT_FRICTION = 0.3
# Soft guide pull at strength 1, per second.
GUIDE_PULL = 30.0


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def segment_lengths(points):
    return [_dist(a, b) for a, b in zip(points, points[1:])]


class Rope:
    def __init__(self, points):
        self.reset(points)

    def reset(self, points):
        self.pos = [list(p) for p in points]
        self.prev = [list(p) for p in points]

    @property
    def count(self):
        return len(self.pos)

    def points(self):
        return [tuple(p) for p in self.pos]

    def speed(self, dt):
        """Fastest point speed during the last step, in units per second."""
        if dt <= 0.0:
            return 0.0
        return max(_dist(p, q) for p, q in zip(self.pos, self.prev)) / dt

    def mean_speed(self, dt):
        """Average point speed during the last step. Less sensitive than
        speed() to single points jittering against a collider."""
        if dt <= 0.0:
            return 0.0
        return sum(_dist(p, q) for p, q in zip(self.pos, self.prev)) / (len(self.pos) * dt)

    def distance_to(self, target):
        return max(_dist(p, t) for p, t in zip(self.pos, target))

    def snap(self, target):
        self.reset(target)

    def blend(self, target, factor, nearest=None, gap=0.0):
        """Ease toward `target` without physics; used once the swing has died
        down, so the wire comes to rest exactly on its solved shape. Points
        are kept out of colliders on the way."""
        n = len(self.pos)
        for i, (p, t) in enumerate(zip(self.pos, target)):
            for k in range(3):
                p[k] += (t[k] - p[k]) * factor
            if nearest is not None and 0 < i < n - 1:
                hit = nearest(tuple(p))
                if hit is not None:
                    surface, normal = hit
                    side = sum((p[k] - surface[k]) * normal[k] for k in range(3))
                    if side < gap:
                        p[:] = [surface[k] + normal[k] * gap for k in range(3)]
            self.prev[i][:] = p

    def step(
        self,
        dt,
        end_a,
        end_b,
        segment,
        damping=0.6,
        stiffness=0.2,
        substeps=4,
        iterations=8,
        nearest=None,
        cast=None,
        gap=0.0,
        target=None,
        settle=0.0,
        pins=None,
        guides=None,
    ):
        """Advance by `dt` seconds with the ends pinned at end_a and end_b.

        `pins` maps more point indices to fixed positions (anchors and
        supports in the middle of the curve). `guides` are (index,
        position, strength) soft pulls toward a point.

        `segment` is the rest length between neighbouring points, one value
        or a list per segment (use segment_lengths(target) so the rest shape
        satisfies the constraints exactly). `damping`
        is the share of velocity lost per second (0..1). `stiffness` (0..1)
        resists bending. `settle` (per second) pulls points toward `target`
        without adding velocity, so the rope comes to rest exactly there.
        """
        n = len(self.pos)
        if n < 2 or dt <= 0.0:
            return
        segments = [segment] * (n - 1) if isinstance(segment, (int, float)) else list(segment)
        h = dt / substeps
        keep = (1.0 - min(max(damping, 0.0), 0.999)) ** h
        drop = GRAVITY * h * h
        # Bending acts as a force toward the rest shape's own curvature, so it
        # resists deformation without fighting the hanging shape.
        bend = min(max(stiffness, 0.0), 1.0) * BEND_STRENGTH * h * h
        rest_curve = self._curvature(target) if (bend > 0.0 and target is not None) else None
        pull = min(settle * h, 1.0) if target is not None else 0.0
        pos, prev = self.pos, self.prev
        pinned = dict(pins or {})
        pinned[0] = end_a
        pinned[n - 1] = end_b
        chains = list(zip(sorted(pinned), sorted(pinned)[1:]))
        guide_pull = [
            (i, g, min(max(s, 0.0), 1.0) * GUIDE_PULL * h)
            for i, g, s in (guides or ()) if 0 <= i < n and i not in pinned
        ]

        for _ in range(substeps):
            before = [tuple(p) for p in pos]
            curve = self._curvature(pos) if bend > 0.0 else None
            for i in range(1, n - 1):
                p, q = pos[i], prev[i]
                vx = (p[0] - q[0]) * keep
                vy = (p[1] - q[1]) * keep
                vz = (p[2] - q[2]) * keep - drop
                if curve is not None:
                    c = curve[i]
                    r = rest_curve[i] if rest_curve is not None else (0.0, 0.0, 0.0)
                    vx += (c[0] - r[0]) * bend
                    vy += (c[1] - r[1]) * bend
                    vz += (c[2] - r[2]) * bend
                q[0], q[1], q[2] = p[0], p[1], p[2]
                p[0] += vx
                p[1] += vy
                p[2] += vz

            for i, at in pinned.items():
                pos[i][:] = at
                prev[i][:] = at

            for i, g, k in guide_pull:
                p = pos[i]
                k = min(k, 1.0)
                for c in range(3):
                    p[c] += (g[c] - p[c]) * k

            for it in range(iterations):
                self._constrain(segments, forward=(it % 2 == 0), chains=chains)

            if cast is not None:
                for i in range(1, n - 1):
                    if i in pinned:
                        continue
                    hit = cast(before[i], tuple(pos[i]))
                    if hit is not None:
                        surface, normal = hit
                        self._place(i, surface, normal, gap)
            if nearest is not None:
                for i in range(1, n - 1):
                    if i in pinned:
                        continue
                    hit = nearest(tuple(pos[i]))
                    if hit is None:
                        continue
                    surface, normal = hit
                    side = sum((pos[i][k] - surface[k]) * normal[k] for k in range(3))
                    if side < gap:
                        self._place(i, surface, normal, gap)

            if pull > 0.0:
                for i in range(1, n - 1):
                    if i in pinned:
                        continue
                    t = target[i]
                    for k in range(3):
                        d = (t[k] - pos[i][k]) * pull
                        pos[i][k] += d
                        prev[i][k] += d

    def _place(self, i, surface, normal, gap):
        """Put point i on the collider surface, drop its velocity into the
        surface and slow its sliding with friction."""
        p, q = self.pos[i], self.prev[i]
        new = [surface[k] + normal[k] * gap for k in range(3)]
        v = [p[k] - q[k] for k in range(3)]
        into = sum(v[k] * normal[k] for k in range(3))
        if into < 0.0:
            v = [v[k] - normal[k] * into for k in range(3)]
        keep = 1.0 - CONTACT_FRICTION
        p[:] = new
        q[:] = [new[k] - v[k] * keep for k in range(3)]

    def _constrain(self, segments, forward, chains=None):
        """Keep each segment at its rest length, FABRIK style from one pinned
        end of each sub-chain between pinned points. Cables bend but don't
        compress, so this works both ways. segments[i] is the rest length
        between points i and i + 1."""
        pos = self.pos
        n = len(pos)
        pairs = []
        for c0, c1 in chains or [(0, n - 1)]:
            if forward:
                pairs.extend((i, i + 1, i) for i in range(c0, c1 - 1))
            else:
                pairs.extend((i, i - 1, i - 1) for i in range(c1, c0 + 1, -1))
        for anchor, moved, seg in pairs:
            a, b = pos[anchor], pos[moved]
            dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            if d < 1e-12:
                continue
            k = segments[seg] / d
            b[0] = a[0] + dx * k
            b[1] = a[1] + dy * k
            b[2] = a[2] + dz * k

    @staticmethod
    def _curvature(points):
        """Offset from each point to the midpoint of its neighbours."""
        out = [(0.0, 0.0, 0.0)] * len(points)
        for i in range(1, len(points) - 1):
            a, p, b = points[i - 1], points[i], points[i + 1]
            out[i] = tuple((a[k] + b[k]) * 0.5 - p[k] for k in range(3))
        return out
