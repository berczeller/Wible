# SPDX-License-Identifier: GPL-3.0-or-later

"""Wire shape solver: catenary between two points plus an optional drape pass.

Pure Python on (x, y, z) tuples so it can be tested without Blender.
Gravity points down the world Z axis.
"""

import math

# Upper bound for h / (2a). Past this the wire is so slack that cosh overflows
# long before the result means anything.
U_MAX = 15.0


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _length(a):
    return math.sqrt(_dot(a, a))


def polyline_length(points):
    return sum(_length(_sub(b, a)) for a, b in zip(points, points[1:]))


def _lerp_points(p0, p1, count):
    return [_add(p0, _scale(_sub(p1, p0), i / (count - 1))) for i in range(count)]


def catenary_a_for_length(p0, p1, length):
    """Catenary parameter `a` whose curve between p0 and p1 has the given length.

    Returns math.inf when the length is not longer than the straight distance
    (the wire is taut), and None when the ends are vertically aligned.
    """
    d = _sub(p1, p0)
    h = math.hypot(d[0], d[1])
    v = d[2]
    if h < 1e-9:
        return None
    target = math.sqrt(max(length * length - v * v, 0.0))
    if target <= h * (1.0 + 1e-9):
        return math.inf
    # 2a sinh(h / 2a) = target. With u = h / 2a: sinh(u) / u = target / h,
    # which grows with u, so bisect.
    ratio = target / h
    lo, hi = 1e-9, U_MAX
    if math.sinh(hi) / hi <= ratio:
        return h / (2.0 * hi)
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if math.sinh(mid) / mid < ratio:
            lo = mid
        else:
            hi = mid
    return h / (lo + hi)


def catenary_points(p0, p1, a, count):
    """`count` points spaced evenly by arc length along the catenary from p0 to p1.

    Returns (points, length). Falls back to a straight line when the wire is
    vertical or `a` is infinite.
    """
    count = max(count, 2)
    d = _sub(p1, p0)
    h = math.hypot(d[0], d[1])
    v = d[2]
    if h < 1e-9 or a is None or not math.isfinite(a) or a <= 0.0:
        return _lerp_points(p0, p1, count), _length(d)

    a = max(a, h / (2.0 * U_MAX))
    ux, uy = d[0] / h, d[1] / h
    half = h / (2.0 * a)
    x0 = h / 2.0 - a * math.asinh(v / (2.0 * a * math.sinh(half)))
    sinh0 = math.sinh(x0 / a)
    cosh0 = math.cosh(x0 / a)

    length = a * (math.sinh((h - x0) / a) + sinh0)
    points = []
    for i in range(count):
        s = length * i / (count - 1)
        x = x0 + a * math.asinh(s / a - sinh0)
        z = a * (math.cosh((x - x0) / a) - cosh0)
        points.append((p0[0] + ux * x, p0[1] + uy * x, p0[2] + z))
    points[0] = tuple(p0)
    points[-1] = tuple(p1)
    return points, length


def _segment_list(rest_length, n):
    if isinstance(rest_length, (int, float)):
        return [float(rest_length)] * (n - 1)
    return [float(r) for r in rest_length]


def _chains(n, pins):
    """Sub-chains (start, end) between consecutive pinned indices."""
    fixed = sorted(set(pins) | {0, n - 1})
    return list(zip(fixed, fixed[1:]))


def drape(points, rest_length, iterations, nearest=None, gap=0.0, cast=None, drop=None,
          pins=(), guides=()):
    """Relax a hanging wire under gravity with its ends (and `pins`) fixed.

    `rest_length` is one length for every segment or a list per segment.
    Segments behave like rope: they resist stretching but may go slack.
    `nearest(p)` returns (surface_point, outward_normal) for the closest
    collider, or None; points closer than `gap` get pushed out. `cast(start,
    end)` returns (hit_point, normal facing start) for the first collider
    between two positions, or None; it stops points passing through thin
    objects in one step. `drop` is roughly how far the wire has to fall;
    gravity steps are sized so the run covers it. `guides` are (index,
    position, strength) soft pulls toward a point.
    """
    pts = [list(p) for p in points]
    n = len(pts)
    if n < 3 or iterations <= 0:
        return [tuple(p) for p in pts]
    segs = _segment_list(rest_length, n)
    fixed = set(pins) | {0, n - 1}
    chains = _chains(n, fixed)
    sweeps = 16

    def constrain(forward):
        """One pass over every sub-chain. Returns the largest stretch found."""
        worst = 0.0
        for c0, c1 in chains:
            # FABRIK style: walk from one fixed end of the sub-chain and pull
            # each next point back within reach of the previous one.
            if forward:
                order = [(i, i + 1, i) for i in range(c0, c1 - 1)]
                last = (c1 - 1, c1, c1 - 1)
            else:
                order = [(i, i - 1, i - 1) for i in range(c1, c0 + 1, -1)]
                last = (c0 + 1, c0, c0)
            for anchor, moved, seg in order:
                a, b = pts[anchor], pts[moved]
                dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
                dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                rest = segs[seg]
                if dist <= rest or dist < 1e-12:
                    continue
                worst = max(worst, dist - rest)
                k = rest / dist
                b[0] = a[0] + dx * k
                b[1] = a[1] + dy * k
                b[2] = a[2] + dz * k
            # The segment touching the far fixed end isn't corrected this pass.
            i, j, seg = last
            worst = max(worst, math.dist(pts[i], pts[j]) - segs[seg])
        return worst

    reach = max(sum(segs) / len(segs) * 8.0, gap * 4.0, 1e-3)

    def outward(*neighbours):
        """Average outward direction at points that are outside colliders."""
        total = (0.0, 0.0, 0.0)
        for p in neighbours:
            hit = nearest(tuple(p))
            if hit is None:
                continue
            surface, normal = hit
            if _dot(_sub(p, surface), normal) >= 0.0:
                total = _add(total, normal)
        length = _length(total)
        return _scale(total, 1.0 / length) if length > 1e-9 else None

    def resolve(point, before, hint=None):
        """Where to put `point` if it's inside or too close to a collider.

        Inside, the way out is back toward where it came from (`before`), or
        toward the side its neighbours are on (`hint`): the nearest surface
        can be the far side of a thin object, and pushing out there would let
        the curve pass through it."""
        if cast is not None and before is not None:
            hit = cast(before, point)
            if hit is not None:
                surface, normal = hit
                return _add(surface, _scale(normal, gap))
        hit = nearest(point)
        if hit is None:
            return None
        surface, normal = hit
        side = _dot(_sub(point, surface), normal)
        if side >= gap:
            return None
        if side >= 0.0:
            return _add(surface, _scale(normal, gap))
        # Inside. Leave toward the neighbours' side if known.
        direction = hint() if callable(hint) else hint
        if direction is not None and cast is not None:
            outside = _add(point, _scale(direction, reach))
            hit = cast(outside, point)
            if hit is not None:
                surface, normal = hit
                return _add(surface, _scale(normal, gap))
        if before is not None and _dot(_sub(before, surface), normal) < 0.0:
            return None  # no safe way out known; try again next step
        return _add(surface, _scale(normal, gap))

    def collide(previous=None):
        if nearest is None:
            return
        for i in range(1, n - 1):
            if i in fixed:
                continue
            before = previous[i] if previous is not None else None
            moved = resolve(tuple(pts[i]), before, lambda i=i: outward(pts[i - 1], pts[i + 1]))
            if moved is not None:
                pts[i] = list(moved)
        # Segment midpoints too, so segments can't cut across corners.
        for i in range(n - 1):
            a, b = pts[i], pts[i + 1]
            mid = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, (a[2] + b[2]) * 0.5)
            before = None
            if previous is not None:
                pa, pb = previous[i], previous[i + 1]
                before = ((pa[0] + pb[0]) * 0.5, (pa[1] + pb[1]) * 0.5, (pa[2] + pb[2]) * 0.5)
            moved = resolve(mid, before, lambda a=a, b=b: outward(a, b))
            if moved is None:
                continue
            delta = _sub(moved, mid)
            for j in (i, i + 1):
                if j in fixed:
                    continue
                old = tuple(pts[j])
                new = _add(old, delta)
                # Never push an end point into a collider on the way.
                if cast is not None:
                    hit = cast(old, new)
                    if hit is not None:
                        surface, normal = hit
                        new = _add(surface, _scale(normal, gap))
                pts[j] = list(new)

    def block(previous):
        if cast is None:
            return
        for i in range(1, n - 1):
            if i in fixed:
                continue
            hit = cast(previous[i], tuple(pts[i]))
            if hit is not None:
                surface, normal = hit
                pts[i] = list(_add(surface, _scale(normal, gap)))

    def pull(fade):
        for index, target, strength in guides:
            if 0 < index < n - 1 and index not in fixed:
                p = pts[index]
                k = min(max(strength, 0.0), 1.0) * (0.5 + 0.5 * (1.0 - fade))
                for c in range(3):
                    p[c] += (target[c] - p[c]) * k

    mean = sum(segs) / len(segs)
    # Steps stay under half a segment so points never sink deep into a
    # collider, where the closest face could be the wrong one. Add iterations
    # when the wire has further to fall than `iterations` steps can cover.
    step = max(mean * 0.5, 1e-9)  # coincident points: no division by zero
    if drop is not None:
        iterations = max(iterations, int(math.ceil(1.5 * drop / (step * 0.75))))
    tolerance = mean * 1e-3

    for it in range(iterations):
        fade = 1.0 - 0.5 * it / iterations
        previous = [tuple(p) for p in pts]
        for i in range(1, n - 1):
            if i not in fixed:
                pts[i][2] -= step * fade
        pull(fade)
        for s in range(sweeps):
            worst = constrain((s + it) % 2 == 0)
            if worst < tolerance and s % 2 == 1:
                break
        block(previous)
        collide(previous)

    # Settle: constraints, guides and collisions only.
    for s in range(200):
        previous = [tuple(p) for p in pts]
        if guides:
            pull(0.0)
        worst = constrain(s % 2 == 0)
        block(previous)
        if s % 2 == 1:
            collide(previous)
            if worst < tolerance and not guides:
                break
        if guides and s >= 60 and worst < tolerance:
            break

    return [tuple(p) for p in pts]


def _contacts(points, nearest, gap, tolerance):
    """Indices of inner points resting on a collider."""
    if nearest is None:
        return []
    found = []
    for i in range(1, len(points) - 1):
        hit = nearest(points[i])
        if hit is None:
            continue
        surface, normal = hit
        if _dot(_sub(points[i], surface), normal) <= gap + tolerance:
            found.append(i)
    return found


def refit_free_spans(points, rest_length, nearest=None, gap=0.0, pins=()):
    """Replace each span between contacts, pins and the ends with an exact
    catenary of the same length, unless that catenary would cut into a
    collider."""
    pts = list(points)
    n = len(pts)
    segs = _segment_list(rest_length, n)
    tolerance = (sum(segs) / len(segs)) * 0.05
    anchors = sorted(set([0, n - 1]) | set(pins) | set(_contacts(pts, nearest, gap, tolerance)))
    for i0, i1 in zip(anchors, anchors[1:]):
        count = i1 - i0
        if count < 2:
            continue
        p0, p1 = pts[i0], pts[i1]
        a = catenary_a_for_length(p0, p1, sum(segs[i0:i1]))
        span, _ = catenary_points(p0, p1, a, count + 1)
        if nearest is not None:
            clear = True
            for p in span[1:-1]:
                hit = nearest(p)
                if hit is not None and _dot(_sub(p, hit[0]), hit[1]) < gap - tolerance:
                    clear = False
                    break
            if not clear:
                continue
        pts[i0:i1 + 1] = span
    return pts


DRAPE_MIN_POINTS = 64


def settle_from(start, p0, p1, a, drape_iterations, nearest=None, gap=0.0, cast=None):
    """Drape starting from an existing shape (for example where the live rope
    came to rest), so the result is the resting shape nearest to it rather
    than the one reached by dropping from above."""
    count = len(start)
    reference, _length = catenary_points(p0, p1, a, count)
    rest = polyline_length(reference) / (count - 1)
    pts = [tuple(p) for p in start]
    pts[0], pts[-1] = tuple(p0), tuple(p1)
    pts = drape(pts, rest, max(drape_iterations // 2, 1), nearest, gap, cast)
    return refit_free_spans(pts, rest, nearest, gap)


def solve(p0, p1, a, count, drape_iterations=0, nearest=None, gap=0.0, cast=None):
    """Full wire shape: catenary, or a drape that drops from the straight line
    between the ends onto colliders and keeps the catenary's length.

    Draping uses at least DRAPE_MIN_POINTS points and returns all of them, so
    `count` acts as a minimum in that case.
    """
    if drape_iterations > 0:
        count = max(count, DRAPE_MIN_POINTS)
    points, _length = catenary_points(p0, p1, a, count)
    if drape_iterations > 0 and len(points) > 2:
        # Chord length, not arc length, so a free wire matches the catenary.
        rest = polyline_length(points) / (len(points) - 1)
        start = _lerp_points(tuple(p0), tuple(p1), len(points))
        drop = max(s[2] - c[2] for s, c in zip(start, points))
        points = drape(start, rest, drape_iterations, nearest, gap, cast, drop=max(drop, rest))
        points = refit_free_spans(points, rest, nearest, gap)
    return points


# Multi-point curves
#
# A curve runs through a list of points of three kinds:
#   ANCHOR  pinned; ends a "run". Each run keeps its own length, so slack
#           never moves across an anchor.
#   SUPPORT the cable passes over it and slack slides across it: every span
#           in a run shares one catenary parameter (one horizontal pull).
#   GUIDE   soft: pulls the cable toward it with a strength from 0 to 1.
# The first and last points are always anchors.

ANCHOR, SUPPORT, GUIDE = 0, 1, 2
A_MIN, A_MAX = 1e-4, 1e6


def normalize_kinds(kinds):
    kinds = list(kinds)
    if kinds:
        kinds[0] = ANCHOR
        kinds[-1] = ANCHOR
    return kinds


def runs(kinds):
    """(first, last) point index of each run between consecutive anchors."""
    kinds = normalize_kinds(kinds)
    anchors = [i for i, k in enumerate(kinds) if k == ANCHOR]
    return list(zip(anchors, anchors[1:]))


def hard_indices(kinds, i0, i1):
    """Anchors and supports of the run from i0 to i1, in order."""
    return [i0] + [i for i in range(i0 + 1, i1) if kinds[i] == SUPPORT] + [i1]


def _span_length(p0, p1, a):
    _pts, length = catenary_points(p0, p1, a, 2)
    return length


def run_length(points, hard, a):
    """Rope length of a run when every span uses catenary parameter `a`."""
    return sum(_span_length(points[i], points[j], a) for i, j in zip(hard, hard[1:]))


def chord_length(points, hard):
    return sum(_length(_sub(points[j], points[i])) for i, j in zip(hard, hard[1:]))


def a_for_run(points, hard, length):
    """Catenary parameter shared by a run's spans so they add up to `length`.
    math.inf when the run is taut."""
    if length <= chord_length(points, hard) * (1.0 + 1e-9):
        return math.inf
    lo, hi = math.log(A_MIN), math.log(A_MAX)
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if run_length(points, hard, math.exp(mid)) > length:
            lo = mid  # too long: needs a larger parameter (tighter)
        else:
            hi = mid
    return math.exp(0.5 * (lo + hi))


def hard_spans(kinds):
    """Every span between consecutive anchors or supports, in order."""
    kinds = normalize_kinds(kinds)
    hard = [i for i, k in enumerate(kinds) if k != GUIDE]
    return list(zip(hard, hard[1:]))


def sample_layout(points, kinds, samples_per_span):
    """Where each point lands in the sampled polyline.

    Returns (total samples, {point index: sample index}). Each span between
    hard points gets `samples_per_span` segments; guides sit at their
    fraction along the path through the points between those hard points.
    """
    kinds = normalize_kinds(kinds)
    spans = hard_spans(kinds)
    s = max(int(samples_per_span), 2)
    where = {}
    for k, (h0, h1) in enumerate(spans):
        where[h0] = k * s
        where[h1] = (k + 1) * s
        between = list(range(h0, h1 + 1))
        dists = [0.0]
        for i, j in zip(between, between[1:]):
            dists.append(dists[-1] + _length(_sub(points[j], points[i])))
        total = dists[-1] or 1.0
        for local, i in enumerate(between[1:-1], start=1):
            index = k * s + int(round(dists[local] / total * s))
            where[i] = min(max(index, k * s + 1), (k + 1) * s - 1)
    return len(spans) * s + 1, where


def solve_path(points, kinds, strengths, run_lengths, samples_per_span,
               drape_iterations=0, nearest=None, gap=0.0, cast=None, guide_iterations=40,
               start=None):
    """Rest shape of a multi-point curve, sampled.

    `run_lengths` has one rope length per run. Returns the sampled points;
    sample_layout() tells where each input point sits in them.
    """
    kinds = normalize_kinds(kinds)
    points = [tuple(p) for p in points]
    count, where = sample_layout(points, kinds, samples_per_span)
    s = max(int(samples_per_span), 2)

    out = [points[0]]
    for (i0, i1), length in zip(runs(kinds), run_lengths):
        hard = hard_indices(kinds, i0, i1)
        a = a_for_run(points, hard, length)
        for h0, h1 in zip(hard, hard[1:]):
            span, _ = catenary_points(points[h0], points[h1], a, s + 1)
            out.extend(span[1:])

    pins = sorted(where[i] for i, k in enumerate(kinds) if k != GUIDE)
    guides = [(where[i], points[i], strengths[i] if i < len(strengths) else 1.0)
              for i, k in enumerate(kinds) if k == GUIDE]
    rest = [_length(_sub(b, a)) for a, b in zip(out, out[1:])]

    if drape_iterations > 0 and nearest is not None:
        if start is not None and len(start) == len(out):
            # Settle from a given shape (where the live rope came to rest):
            # gives the resting shape nearest to it.
            shape = [tuple(p) for p in start]
            for i in pins:
                shape[i] = out[i]
            drop = None
            iterations = max(drape_iterations // 2, 1)
        else:
            # Drop from straight lines between the hard points onto colliders.
            shape = list(out)
            for p0, p1 in zip(pins, pins[1:]):
                shape[p0:p1 + 1] = _lerp_points(out[p0], out[p1], p1 - p0 + 1)
            drop = max(max((st[2] - o[2] for st, o in zip(shape, out)), default=0.0), sum(rest) / len(rest))
            iterations = drape_iterations
        out = drape(shape, rest, iterations, nearest, gap, cast, drop=drop, pins=pins, guides=guides)
        if not guides:
            out = refit_free_spans(out, rest, nearest, gap, pins=pins)
    elif guides:
        out = drape(out, rest, guide_iterations, pins=pins, guides=guides)
    return out
