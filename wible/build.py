# SPDX-License-Identifier: GPL-3.0-or-later

"""Turn stored points into Wible curve objects."""

import math

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from . import solver

COLLECTION_NAME = "Wible"
STROKE_KEY = "wible_stroke"

# Collider BVH trees in world space, keyed by object name. Entries hold the
# matrix they were built with; geometry edits clear them from the depsgraph handler.
_collider_cache = {}


def ensure_collection(scene):
    coll = bpy.data.collections.get(COLLECTION_NAME)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION_NAME)
    if coll not in scene.collection.children_recursive:
        scene.collection.children.link(coll)
    return coll


def resample(points, count):
    """Return `count` points spaced evenly by arc length along the polyline."""
    if len(points) < 2 or count < 2:
        return [p.copy() for p in points]
    lengths = [0.0]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + (b - a).length)
    total = lengths[-1]
    if total <= 1e-9:
        return [points[0].copy() for _ in range(count)]

    out = []
    seg = 0
    for i in range(count):
        target = total * i / (count - 1)
        while seg < len(points) - 2 and lengths[seg + 1] < target:
            seg += 1
        span = lengths[seg + 1] - lengths[seg]
        t = 0.0 if span <= 1e-12 else (target - lengths[seg]) / span
        out.append(points[seg].lerp(points[seg + 1], min(max(t, 0.0), 1.0)))
    return out


def smooth(points, iterations):
    """Laplacian smoothing that keeps both ends fixed."""
    pts = [p.copy() for p in points]
    for _ in range(iterations):
        if len(pts) < 3:
            break
        pts = [pts[0]] + [(pts[i - 1] + pts[i] * 2.0 + pts[i + 1]) * 0.25 for i in range(1, len(pts) - 1)] + [pts[-1]]
    return pts


def smoothed_stroke(points, settings):
    if len(points) < 2:
        return points
    dense = resample(points, max(settings.point_count * 4, len(points)))
    return smooth(dense, settings.smoothing * 4)


def store_stroke(obj, local_points):
    obj[STROKE_KEY] = [c for p in local_points for c in p]


def read_stroke(obj):
    flat = obj.get(STROKE_KEY)
    if not flat:
        return []
    flat = list(flat)
    return [Vector(flat[i:i + 3]) for i in range(0, len(flat) - 2, 3)]


def catenary_a(settings):
    return settings.tension / max(settings.weight, 1e-6)


# Colliders

def clear_collider_cache(names=None):
    if names is None:
        _collider_cache.clear()
    else:
        for name in names:
            _collider_cache.pop(name, None)


def _collider_tree(obj, depsgraph):
    key = tuple(c for row in obj.matrix_world for c in row)
    cached = _collider_cache.get(obj.name)
    if cached is not None and cached[0] == key:
        return cached[1]

    import numpy as np

    obj_eval = obj.evaluated_get(depsgraph)
    try:
        mesh = obj_eval.to_mesh()
    except RuntimeError:
        return None
    tree = None
    if mesh is not None and len(mesh.polygons):
        # Bulk copies instead of a Python loop per vertex: this runs again
        # every time a collider moves.
        count = len(mesh.vertices)
        co = np.empty(count * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        mw = np.array(obj_eval.matrix_world, dtype=np.float64)
        co = co.reshape(count, 3) @ mw[:3, :3].T + mw[:3, 3]
        mesh.calc_loop_triangles()
        tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", tris)
        tree = BVHTree.FromPolygons(co.tolist(), tris.reshape(-1, 3).tolist())
    obj_eval.to_mesh_clear()
    _collider_cache[obj.name] = (key, tree)
    return tree


COLLIDER_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


def wire_bounds(obj):
    """World box the curve can reach: around its points, extended down
    and sideways by how far its slack lets it hang or swing."""
    points = point_positions(obj)
    if not points:
        return None
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    lengths = list(obj.get(RUNS_KEY, [])) or [obj.wible.length]
    reach = max(lengths) * 0.5 + obj.wible.depth + obj.wible.drape_gap
    margin = Vector((reach, reach, reach))
    return lo - margin, hi + margin


def _world_box(o):
    corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
    return (
        Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners))),
        Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners))),
    )


def _overlaps(a, b):
    return all(a[0][i] <= b[1][i] and b[0][i] <= a[1][i] for i in range(3))


def collider_objects(settings, exclude):
    """Objects the curve collides with: its Drape Over collection, or,
    with none set, every visible object near it. Other curves and
    Wible's own helpers never count."""
    coll = settings.drape_collection
    if coll is not None:
        candidates = list(coll.all_objects)
    else:
        scene = scene_of(exclude) if exclude is not None else bpy.context.scene
        candidates = [o for o in scene.objects if o.visible_get()]
    bounds = wire_bounds(exclude) if exclude is not None else None
    out = []
    for o in candidates:
        if o == exclude or o.wible.is_wire or o.type not in COLLIDER_TYPES:
            continue
        if o.get(HANDLE_KEY) is not None:
            continue
        if bounds is not None and not _overlaps(bounds, _world_box(o)):
            continue
        out.append(o)
    return out


def _make_nearest(trees):
    if not trees:
        return None

    def nearest(p):
        point = Vector(p)
        best = None
        for tree in trees:
            loc, normal, _index, dist = tree.find_nearest(point)
            if loc is not None and (best is None or dist < best[2]):
                best = (loc, normal, dist)
        if best is None:
            return None
        loc, normal, dist = best
        if dist <= 1e-6:
            return tuple(loc), tuple(normal)
        direction = (point - loc) / dist
        # Inside the mesh the offset runs straight against the face normal.
        # Anything else is outside, near an edge or corner: there the face
        # normal says nothing about which side the point is on, so push along
        # the offset instead and let the wire slide over the edge.
        if direction.dot(normal) < -0.9:
            return tuple(loc), tuple(normal)
        return tuple(loc), tuple(direction)

    return nearest


def _make_cast(trees):
    if not trees:
        return None

    def cast(start, end):
        a = Vector(start)
        direction = Vector(end) - a
        distance = direction.length
        if distance < 1e-9:
            return None
        direction /= distance
        best = None
        for tree in trees:
            loc, normal, _index, dist = tree.ray_cast(a, direction, distance)
            if loc is not None and (best is None or dist < best[2]):
                best = (loc, normal, dist)
        if best is None:
            return None
        loc, normal, _dist = best
        if normal.dot(direction) > 0.0:
            normal = -normal
        return tuple(loc), tuple(normal)

    return cast


# Points
#
# A curve runs through a list of points stored on the object:
#   STROKE_KEY    their positions in object space
#   KINDS_KEY     solver.ANCHOR / SUPPORT / GUIDE per point
#   STRENGTH_KEY  guide strength per point (0..1)
#   RUNS_KEY      rope length of each run between anchors
# Each point has a handle (a child empty) while the curve is live; the
# handles are where the points are, and dragging one moves its point.
# Curves from the Grease Pencil version stored the whole drawn stroke;
# they are converted on first use (ends become the two points, the drawn
# shape is kept in SHAPE_KEY for Gravity off).

KINDS_KEY = "wible_kinds"
STRENGTH_KEY = "wible_strengths"
RUNS_KEY = "wible_runs"
SHAPE_KEY = "wible_shape"
HANDLE_KEY = "wible_end"
KIND_NAMES = ("ANCHOR", "SUPPORT", "GUIDE")
HANDLE_DISPLAY = {solver.ANCHOR: "SPHERE", solver.SUPPORT: "CUBE", solver.GUIDE: "PLAIN_AXES"}


def migrate(obj):
    """Convert a curve made with the Grease Pencil version."""
    if KINDS_KEY in obj or STROKE_KEY not in obj:
        return
    stroke = read_stroke(obj)
    if len(stroke) < 2:
        return
    obj[SHAPE_KEY] = [c for p in stroke for c in p]
    store_stroke(obj, [stroke[0], stroke[-1]])
    obj[KINDS_KEY] = [solver.ANCHOR, solver.ANCHOR]
    obj[STRENGTH_KEY] = [1.0, 1.0]
    if obj.wible.length > 0.0:
        obj[RUNS_KEY] = [obj.wible.length]
    for child in obj.children:
        side = child.get(HANDLE_KEY)
        if side == "A":
            child[HANDLE_KEY] = 0
        elif side == "B":
            child[HANDLE_KEY] = 1


def point_kinds(obj):
    migrate(obj)
    stroke = read_stroke(obj)
    kinds = list(obj.get(KINDS_KEY, []))
    if len(kinds) != len(stroke):
        kinds = [solver.ANCHOR] * len(stroke)
    return solver.normalize_kinds(kinds)


def point_strengths(obj):
    n = len(read_stroke(obj))
    strengths = list(obj.get(STRENGTH_KEY, []))
    return (strengths + [1.0] * n)[:n]


# Handles

def handles(obj):
    """One handle per point (None where missing), in point order."""
    n = len(read_stroke(obj)) if STROKE_KEY in obj else 0
    out = [None] * n
    for child in obj.children:
        index = child.get(HANDLE_KEY)
        if index == "A":
            index = 0
        elif index == "B":
            index = n - 1
        if isinstance(index, int) and 0 <= index < n and out[index] is None:
            out[index] = child
    return out


def _mean_scale(matrix):
    sx, sy, sz = matrix.to_scale()
    return max(abs(sx * sy * sz), 1e-12) ** (1.0 / 3.0)


def _handle_size(obj):
    return max(obj.wible.depth * 4.0, 0.02) / _mean_scale(obj.matrix_world)


def _style_handle(h, kind, index, count):
    h.empty_display_type = HANDLE_DISPLAY.get(kind, "SPHERE")


def ensure_handles(obj):
    """Create any missing point handle at its stored position."""
    migrate(obj)
    existing = handles(obj)
    if all(h is not None for h in existing):
        return existing
    stroke = read_stroke(obj)
    kinds = point_kinds(obj)
    mw = obj.matrix_world
    colls = obj.users_collection
    inv = mw.inverted_safe()
    out = []
    for i, (h, local) in enumerate(zip(existing, stroke)):
        if h is None:
            h = bpy.data.objects.new(f"{obj.name}.{i + 1}", None)
            h.empty_display_size = _handle_size(obj)
            h.show_in_front = True
            h.hide_render = True
            h[HANDLE_KEY] = i
            for coll in colls:
                coll.objects.link(h)
            h.parent = obj
            h.matrix_parent_inverse = Matrix.Identity(4)
            h.location = inv @ (mw @ local)
        _style_handle(h, kinds[i], i, len(stroke))
        out.append(h)
    return out


def restyle_handles(obj):
    kinds = point_kinds(obj)
    for i, h in enumerate(handles(obj)):
        if h is not None:
            _style_handle(h, kinds[i], i, len(kinds))


def remove_handles(obj):
    """Remove every point handle, including any left over from a point
    that no longer exists."""
    for child in list(obj.children):
        if child.get(HANDLE_KEY) is not None:
            bpy.data.objects.remove(child)


def update_handle_size(obj):
    size = _handle_size(obj)
    for h in handles(obj):
        if h is not None and abs(h.empty_display_size - size) > 1e-6:
            h.empty_display_size = size


def handle_index(h):
    index = h.get(HANDLE_KEY)
    if isinstance(index, int):
        return index
    if h.parent is not None and index in ("A", "B"):
        return 0 if index == "A" else len(read_stroke(h.parent)) - 1
    return None


# Positions and lengths

def _handle_world(h):
    # matrix_world of a child is refreshed on evaluation; build it from the
    # parent so positions are right even mid-update.
    parent = h.parent
    local = h.matrix_basis
    if parent is not None:
        return (parent.matrix_world @ h.matrix_parent_inverse @ local).translation
    return local.translation


def point_positions(obj):
    """World-space positions of the points: the handles when present, else
    the stored positions."""
    migrate(obj)
    stroke = read_stroke(obj)
    if len(stroke) < 2:
        return []
    mw = obj.matrix_world
    return [
        _handle_world(h) if h is not None else mw @ p
        for h, p in zip(handles(obj), stroke)
    ]


def stroke_ends(obj):
    pts = point_positions(obj)
    return (pts[0], pts[-1]) if len(pts) >= 2 else None


def wire_ends(obj):
    """World-space first and last point."""
    return stroke_ends(obj)


def _run_hards(obj, points=None):
    kinds = point_kinds(obj)
    return kinds, [solver.hard_indices(kinds, i0, i1) for i0, i1 in solver.runs(kinds)]


def run_lengths(obj):
    """Rope length per run, computed from tension when not stored yet."""
    points = [tuple(p) for p in point_positions(obj)]
    _kinds, hards = _run_hards(obj)
    stored = list(obj.get(RUNS_KEY, []))
    if len(stored) == len(hards) and all(length > 0.0 for length in stored):
        return stored
    a = catenary_a(obj.wible)
    lengths = [solver.run_length(points, hard, a) for hard in hards]
    obj[RUNS_KEY] = lengths
    return lengths


def _reset_runs(obj):
    if RUNS_KEY in obj:
        del obj[RUNS_KEY]


def length_for_tension(obj):
    """Set every run's length from the current tension; returns the total."""
    _reset_runs(obj)
    return sum(run_lengths(obj))


TENSION_DISPLAY_MAX = 10000.0


def tension_for_length(obj):
    """Tension shown for the curve's current lengths: that of its widest run."""
    s = obj.wible
    points = [tuple(p) for p in point_positions(obj)]
    if len(points) < 2:
        return None
    _kinds, hards = _run_hards(obj)
    best = None
    for hard, length in zip(hards, run_lengths(obj)):
        span = max(math.hypot(points[j][0] - points[i][0], points[j][1] - points[i][1])
                   for i, j in zip(hard, hard[1:]))
        a = solver.a_for_run(points, hard, length)
        a = min(a, max(span * 20.0, 0.01))  # taut: same cap as new wires
        if best is None or span > best[0]:
            best = (span, a)
    return min(best[1] * s.weight, TENSION_DISPLAY_MAX) if best else None


def scale_runs_to_length(obj, total):
    """Spread a new total length over the runs, keeping their proportions."""
    lengths = run_lengths(obj)
    current = sum(lengths) or 1.0
    obj[RUNS_KEY] = [length * total / current for length in lengths]


def ensure_length(obj):
    """Fill in run lengths and the total Length if missing."""
    from . import props
    total = sum(run_lengths(obj))
    if abs(obj.wible.length - total) > 1e-9:
        props.set_silent(obj.wible, "length", total)


def keep_length(obj):
    """After points move: every run keeps its length unless its points were
    pulled further apart than it is long (then it goes taut). Updates the
    shown Length and Tension."""
    from . import props
    points = [tuple(p) for p in point_positions(obj)]
    if len(points) < 2:
        return
    _kinds, hards = _run_hards(obj)
    lengths = run_lengths(obj)
    grown = [max(length, solver.chord_length(points, hard)) for length, hard in zip(lengths, hards)]
    if grown != lengths:
        obj[RUNS_KEY] = grown
    props.set_silent(obj.wible, "length", sum(grown))
    tension = tension_for_length(obj)
    if tension is not None:
        props.set_silent(obj.wible, "tension", tension)


# Shape

# Samples per span, per curve, for a short while: the live physics asks
# every frame and finding colliders isn't free.
_samples_cache = {}
SAMPLES_CACHE_SECONDS = 0.25
# Safety limits on how finely a curve is sampled near colliders: points
# per span, and in total for one curve (physics and collision checks
# grow with the number of points).
MAX_SAMPLES_PER_SPAN = 96
MAX_SAMPLES_TOTAL = 600


def samples_per_span(obj):
    """Points per span of the sampled curve. With colliders around, the
    spacing shrinks to about a third of the thinnest one, so several points
    rest on it; a single point on a round object would slide off and let the
    segments either side pass through."""
    import time
    s = obj.wible
    base = max(s.point_count, 2)
    if not (s.gravity and s.drape):
        return base
    now = time.perf_counter()
    cached = _samples_cache.get(obj.name)
    if cached is not None and now - cached[0] < SAMPLES_CACHE_SECONDS and cached[1] == base:
        return cached[2]
    count = max(base, solver.DRAPE_MIN_POINTS // 2)
    colliders = collider_objects(s, obj)
    if colliders:
        thinnest = min(max(min(o.dimensions), 1e-3) for o in colliders)
        target = max(thinnest / 3.0, s.depth * 2.0, 0.01)
        longest = max(list(obj.get(RUNS_KEY, [])) or [s.length] or [1.0])
        count = max(count, int(math.ceil(longest / target)))
    spans = max(len(solver.hard_spans(point_kinds(obj))), 1)
    count = min(count, MAX_SAMPLES_PER_SPAN, max(base, MAX_SAMPLES_TOTAL // spans))
    _samples_cache[obj.name] = (now, base, count)
    return count


def sample_layout(obj):
    """(sample count, {point index: sample index}) for the curve."""
    points = [tuple(p) for p in point_positions(obj)]
    return solver.sample_layout(points, point_kinds(obj), samples_per_span(obj))


def point_total(obj):
    return sample_layout(obj)[0]


def collision_funcs(obj, depsgraph=None):
    """(nearest, cast) for the wire's colliders, or (None, None)."""
    s = obj.wible
    if not s.drape:
        return None, None
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    trees = [t for t in (_collider_tree(o, depsgraph) for o in collider_objects(s, obj)) if t is not None]
    if not trees:
        return None, None
    return _make_nearest(trees), _make_cast(trees)


def hanging_points(obj, count=None):
    """Rest shape without colliders, world space. Cheap enough per frame."""
    points = [tuple(p) for p in point_positions(obj)]
    if len(points) < 2:
        return []
    ensure_length(obj)
    return solver.solve_path(
        points, point_kinds(obj), point_strengths(obj), run_lengths(obj),
        samples_per_span(obj),
    )


def rest_points(obj, depsgraph=None, start=None, max_frames=None):
    """The curve's settled shape in world space. With `start` (a shape with
    the same sample count) a draped curve settles from there instead of
    dropping from above. `max_frames` caps the physics frames of a draped
    solve (a quick preview while dragging)."""
    s = obj.wible
    points = [tuple(p) for p in point_positions(obj)]
    if len(points) < 2:
        return []

    if not s.gravity:
        return _drawn_points(obj, points)

    ensure_length(obj)
    kinds = point_kinds(obj)
    strengths = point_strengths(obj)
    per = samples_per_span(obj)
    hang = solver.solve_path(points, kinds, strengths, run_lengths(obj), per)
    nearest, cast = collision_funcs(obj, depsgraph)
    if nearest is None:
        return hang
    return _settle_on_colliders(obj, points, kinds, strengths, per, hang, nearest, cast, start, max_frames)


def _settle_on_colliders(obj, points, kinds, strengths, per, hang, nearest, cast, start=None, max_frames=None):
    """Resting shape among colliders: run the rope physics (which checks
    collisions every small step, so thin objects can't be skipped) until the
    curve stops, from `start` or from straight lines between its fixed
    points, then fit exact catenaries to the spans that hang free."""
    from . import sim

    s = obj.wible
    gap = s.depth + s.drape_gap
    _count, where = solver.sample_layout(points, kinds, per)
    fixed = sorted(where[i] for i, k in enumerate(kinds) if k != solver.GUIDE)
    pins = {where[i]: points[i] for i, k in enumerate(kinds) if k != solver.GUIDE and 0 < where[i] < len(hang) - 1}
    guides = [(where[i], points[i], strengths[i]) for i, k in enumerate(kinds) if k == solver.GUIDE]
    segments = sim.segment_lengths(hang)

    payout = 0
    if start is not None and len(start) == len(hang):
        shape = [tuple(p) for p in start]
        initial = segments
    else:
        # Start taut along straight lines between the fixed points and pay
        # the cable out to its real length over the first frames, so it
        # lowers onto what's below. (Starting straight at full length would
        # squash every segment and the rope would spring apart.)
        shape = list(hang)
        for a, b in zip(fixed, fixed[1:]):
            shape[a:b + 1] = solver._lerp_points(hang[a], hang[b], b - a + 1)
        initial = sim.segment_lengths(shape)
        payout = PAYOUT_FRAMES

    rope = sim.Rope(shape)
    dt = 1.0 / 60.0
    span = max(sum(segments), 1.0)
    frames = max(s.drape_iterations, 1) * SETTLE_FRAMES_PER_ITERATION
    if max_frames is not None:
        frames = min(frames, max_frames)
    for frame in range(frames):
        if frame < payout:
            t = (frame + 1) / payout
            current = [a + (b - a) * t for a, b in zip(initial, segments)]
        else:
            current = segments
        rope.step(dt, points[0], points[-1], current, damping=0.9, stiffness=0.0,
                  nearest=nearest, cast=cast, gap=gap, pins=pins, guides=guides)
        if frame > payout + 30 and rope.mean_speed(dt) < 0.002 * span:
            break
    out = rope.points()
    if guides:
        return out
    return solver.refit_free_spans(out, segments, nearest, gap, pins=fixed)


# Drape Iterations (default 40) times this is the most physics frames a
# static drape solve runs; it usually stops earlier, once the rope is still.
SETTLE_FRAMES_PER_ITERATION = 10
# Frames over which a static drape solve lengthens the cable to its real
# length, so it lowers gently onto colliders.
PAYOUT_FRAMES = 40


def _drawn_points(obj, points):
    """Gravity off: a smooth path through the points. A converted two-point
    curve keeps its drawn shape, fitted between its ends."""
    s = obj.wible
    if SHAPE_KEY in obj and len(points) == 2:
        flat = list(obj[SHAPE_KEY])
        mw = obj.matrix_world
        drawn = [mw @ Vector(flat[i:i + 3]) for i in range(0, len(flat) - 2, 3)]
        drawn = resample(smoothed_stroke(drawn, s), s.point_count)
        old_a, old_b = drawn[0], drawn[-1]
        new_a, new_b = Vector(points[0]), Vector(points[-1])
        old_dir, new_dir = old_b - old_a, new_b - new_a
        if old_dir.length < 1e-9 or new_dir.length < 1e-9:
            return [tuple(p + (new_a - old_a)) for p in drawn]
        rot = old_dir.rotation_difference(new_dir)
        scale = new_dir.length / old_dir.length
        return [tuple(new_a + (rot @ (p - old_a)) * scale) for p in drawn]
    return _smooth_through(points, max(s.point_count, 2))


def _smooth_through(points, per_span):
    """Catmull-Rom curve through the points."""
    pts = [Vector(p) for p in points]
    if len(pts) == 2:
        return [tuple(pts[0].lerp(pts[1], i / per_span)) for i in range(per_span + 1)]
    out = [tuple(pts[0])]
    padded = [pts[0] * 2 - pts[1]] + pts + [pts[-1] * 2 - pts[-2]]
    for k in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[k - 1], padded[k], padded[k + 1], padded[k + 2]
        for i in range(1, per_span + 1):
            t = i / per_span
            t2, t3 = t * t, t * t * t
            p = 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                       + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)
            out.append(tuple(p))
    return out


def _curve_resolution(obj, sample_count):
    """Resolution so the curve has about as many segments as Points x
    Resolution would give: where colliders add points, each gets fewer
    segments, keeping the polygon count (and the mesh) light."""
    s = obj.wible
    spans = max(len(solver.hard_spans(point_kinds(obj))), 1) if STROKE_KEY in obj else 1
    target = s.point_count * s.resolution * spans
    return max(1, min(s.resolution, int(math.ceil(target / max(sample_count - 1, 1)))))


def write_curve(obj, world_points):
    """Set the curve's geometry settings and control points (world space)."""
    if obj.type != "CURVE":
        return
    s = obj.wible
    curve = obj.data
    if curve.users > 1:
        # A linked duplicate (Alt+D) shares curve data with another curve;
        # give it its own so the two don't overwrite each other's shape.
        curve = obj.data = curve.copy()
    mw = obj.matrix_world

    curve.dimensions = "3D"
    curve.twist_mode = "MINIMUM"
    curve.fill_mode = "FULL"
    curve.bevel_mode = "ROUND"
    curve.resolution_u = _curve_resolution(obj, len(world_points))
    curve.render_resolution_u = 0
    # Scaling the object keeps the wire's thickness.
    curve.bevel_depth = s.depth / _mean_scale(mw)
    curve.bevel_resolution = s.bevel_resolution
    curve.use_fill_caps = s.fill_caps

    from . import live
    live.remember(obj.name, world_points)

    inv = mw.inverted_safe()
    pts = [inv @ Vector(p) for p in world_points]
    splines = curve.splines
    spline = splines[0] if len(splines) == 1 and splines[0].type == "BEZIER" else None
    if spline is None or len(spline.bezier_points) != len(pts):
        splines.clear()
        if len(pts) < 2:
            return
        spline = splines.new("BEZIER")
        spline.bezier_points.add(len(pts) - 1)
        for bp in spline.bezier_points:
            bp.handle_left_type = "AUTO"
            bp.handle_right_type = "AUTO"
    # Assigning .co (unlike foreach_set) recalculates the auto handles and
    # tags the curve data for redraw. Don't tag the object: that reports a
    # transform change, which the move handler would react to.
    for bp, co in zip(spline.bezier_points, pts):
        bp.co = co


def uses_physics(obj):
    s = obj.wible
    return obj.type == "CURVE" and s.is_wire and not s.accepted and s.gravity and s.physics


def rebuild(obj, depsgraph=None, animate=False):
    """Refresh the wire after a shape change. With `animate`, a wire with
    physics swings into its new shape instead of jumping."""
    if obj.type != "CURVE":
        return
    from . import live
    if animate and uses_physics(obj):
        live.wake(obj)
        return
    live.forget(obj)
    write_curve(obj, rest_points(obj, depsgraph))
    if not obj.wible.accepted:
        update_handle_size(obj)


def refresh(obj):
    """Redraw after a geometry change (thickness, resolution), keeping the
    wire's current shape, mid-swing or not."""
    if obj.type != "CURVE":
        return
    pts = shown_points(obj)
    write_curve(obj, pts if pts else rest_points(obj))
    if not obj.wible.accepted:
        update_handle_size(obj)


def bake_scale(obj):
    """Fold the object's scale into the points and handles so scale reads 1
    again, without moving any point."""
    mw = obj.matrix_world.copy()
    loc, rot, scale = mw.decompose()
    if all(abs(c - 1.0) < 1e-6 for c in scale):
        return False
    world = point_positions(obj)
    shape_world = None
    if SHAPE_KEY in obj:
        flat = list(obj[SHAPE_KEY])
        shape_world = [mw @ Vector(flat[i:i + 3]) for i in range(0, len(flat) - 2, 3)]
    new_mw = Matrix.LocRotScale(loc, rot, Vector((1.0, 1.0, 1.0)))
    obj.matrix_world = new_mw
    inv = new_mw.inverted_safe()
    store_stroke(obj, [inv @ p for p in world])
    if shape_world is not None:
        obj[SHAPE_KEY] = [c for p in shape_world for c in (inv @ p)]
    for h, p in zip(handles(obj), world):
        if h is not None:
            h.location = (new_mw @ h.matrix_parent_inverse).inverted_safe() @ p
            h.empty_display_size = _handle_size(obj)
    return True


def scene_of(obj):
    scenes = obj.users_scene
    return scenes[0] if scenes else bpy.context.scene


def trim_layout(obj):
    """(SlotLayout, None) for a curve using the trim sheet, or
    (None, reason) when it can't (no slots, slot out of range, slot too small)."""
    from . import trim

    s = obj.wible
    if not s.use_trim:
        return None, "Trim Sheet is off"
    settings = scene_of(obj).wible
    slots = settings.trim_slots
    if not slots:
        return None, "No slots captured yet"
    if s.trim_slot > len(slots):
        return None, f"Slot {s.trim_slot} doesn't exist ({len(slots)} captured)"
    rect = slots[s.trim_slot - 1].rect
    try:
        layout = trim.slot_layout(
            rect, settings.texture_width, settings.texture_height,
            settings.texel_density, settings.trim_padding,
        )
    except ValueError as exc:
        return None, str(exc)
    return layout, None


def apply_trim(obj):
    """Set the curve's thickness from its trim slot, if it uses one."""
    if obj.type != "CURVE" or not obj.wible.is_wire:
        return
    layout, _reason = trim_layout(obj)
    if layout is None:
        return
    from . import props
    if abs(obj.wible.depth - layout.radius) > 1e-9:
        props.set_silent(obj.wible, "depth", layout.radius)
        refresh(obj)


def commit_points(obj):
    """Store where the handles are, so the curve keeps its points once
    the handles are gone."""
    world = point_positions(obj)
    if len(world) < 2:
        return
    mw = obj.matrix_world
    inv = mw.inverted_safe()
    if SHAPE_KEY in obj and len(world) == 2:
        # Keep a converted curve's drawn shape fitted between its ends.
        fitted = _drawn_points(obj, [tuple(p) for p in world])
        obj[SHAPE_KEY] = [c for p in fitted for c in (inv @ Vector(p))]
    store_stroke(obj, [inv @ p for p in world])


def _polyline(points, start, end):
    return sum((Vector(points[i + 1]) - Vector(points[i])).length for i in range(start, end))


def insert_point(obj, position, samples=None, sample_index=None):
    """Add a point at `position` (world space) on the curve, splitting
    the span it lies on. `samples` and `sample_index` (a float into them) say
    where along the drawn curve it is, so the two new spans get lengths that
    keep the cable's shape. Returns the new point's index."""
    position = Vector(position)
    positions = point_positions(obj)
    n = len(positions)
    if n < 2:
        return None
    kinds = point_kinds(obj)
    all_anchors = all(k == solver.ANCHOR for k in kinds)
    runs = run_lengths(obj)
    count, where = sample_layout(obj)

    if samples is not None and sample_index is not None and len(samples) == count:
        k = max((i for i in range(n - 1) if where[i] <= sample_index), default=0)
        k = min(k, n - 2)
        s0, s1 = where[k], where[k + 1]
        j = min(max(int(sample_index), s0), s1 - 1)
        frac = sample_index - j
        before = _polyline(samples, s0, j) + frac * (Vector(samples[j + 1]) - Vector(samples[j])).length
        total = _polyline(samples, s0, s1) or 1.0
        share = min(max(before / total, 0.0), 1.0)
    else:
        def chord_distance(i):
            a, b = positions[i], positions[i + 1]
            ab = b - a
            t = min(max((position - a).dot(ab) / (ab.length_squared or 1.0), 0.0), 1.0)
            return (a + ab * t - position).length
        k = min(range(n - 1), key=chord_distance)
        da = (position - positions[k]).length
        db = (positions[k + 1] - position).length
        share = da / ((da + db) or 1.0)

    new_positions = positions[:k + 1] + [position] + positions[k + 1:]
    mw = obj.matrix_world
    inv = mw.inverted_safe()
    store_stroke(obj, [inv @ p for p in new_positions])
    obj[KINDS_KEY] = kinds[:k + 1] + [solver.ANCHOR] + kinds[k + 1:]
    strengths = point_strengths(obj)
    obj[STRENGTH_KEY] = strengths[:k + 1] + [1.0] + strengths[k + 1:]
    if all_anchors and len(runs) == n - 1:
        length = runs[k]
        chord_a = (position - positions[k]).length
        chord_b = (positions[k + 1] - position).length
        obj[RUNS_KEY] = runs[:k] + [max(length * share, chord_a), max(length * (1.0 - share), chord_b)] + runs[k + 1:]
    else:
        _reset_runs(obj)

    from . import live
    live.forget(obj)
    remove_handles(obj)
    ensure_handles(obj)
    ensure_length(obj)
    rebuild(obj)
    live.settle_at_rest(obj)
    return k + 1


def shown_points(obj):
    """The shape on screen right now (world space): mid-swing or at rest.
    None if it hasn't been drawn yet."""
    from . import live
    current = live.current_points(obj)
    if current:
        return current
    last = live._last_drawn.get(obj.name)
    return list(last) if last and len(last) >= 2 else None


def finalize(obj):
    """Stop the simulation and keep the curve exactly as shown, with
    scale folded in and no handles. Re-solving here could settle it into a
    slightly different rest (for example a different spot on a rod), so the
    saved result wouldn't match what was on screen."""
    from . import live
    shown = shown_points(obj)
    live.forget(obj)
    ensure_length(obj)
    bake_scale(obj)
    commit_points(obj)
    remove_handles(obj)
    write_curve(obj, shown if shown else rest_points(obj))


def create_wire(context, world_points, settings_src, kinds=None, strengths=None, name="Curve"):
    """Create a curve through world-space points. `kinds` gives each
    point's kind (solver.ANCHOR, SUPPORT or GUIDE; ends are always anchors)
    and `strengths` each guide's pull. Origin sits on the first point."""
    from . import props

    scene = context.scene
    world_points = [Vector(p) for p in world_points]
    n = len(world_points)
    origin = world_points[0].copy()
    curve = bpy.data.curves.new(name, "CURVE")
    obj = bpy.data.objects.new(name, curve)
    obj.location = origin
    ensure_collection(scene).objects.link(obj)
    # matrix_world is only refreshed on depsgraph evaluation; set it so the
    # first solve uses the right points.
    obj.matrix_world = Matrix.Translation(origin)

    store_stroke(obj, [p - origin for p in world_points])
    obj[KINDS_KEY] = solver.normalize_kinds(kinds if kinds is not None else [solver.ANCHOR] * n)
    obj[STRENGTH_KEY] = list(strengths) if strengths is not None else [1.0] * n

    props.copy_settings(settings_src, obj.wible)
    props.set_silent(obj.wible, "length", 0.0)
    obj.wible.is_wire = True
    obj.wible.accepted = False
    ensure_length(obj)
    ensure_handles(obj)
    write_curve(obj, rest_points(obj))
    return obj
