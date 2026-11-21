# SPDX-License-Identifier: GPL-3.0-or-later

"""Build a clean tube mesh for a wire: rings without twist, straight UVs,
cap islands, smooth or flat shading, hard or beveled cap edges."""

import math
from dataclasses import dataclass

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.geometry import interpolate_bezier

UV_NAME = "UVMap"

# UV radius of a cap island at the tube's texel density: r / (2 pi r).
CAP_UV_RADIUS = 1.0 / (2.0 * math.pi)


@dataclass
class MeshOptions:
    shade_smooth: bool = True
    cap_edges: str = "HARD"  # HARD or BEVEL
    bevel_width: float = 0.002
    bevel_segments: int = 2
    bevel_profile: float = 0.5
    harden_normals: bool = False
    length_along_u: bool = False
    uv_padding: float = 0.01
    # Trim sheet slot (trim.SlotLayout) to fit the UVs into, or None.
    trim: object = None


def ring_sides(bevel_resolution):
    """Same vertex count per ring as Blender's round curve bevel."""
    return 4 + 2 * bevel_resolution


def centerline(curve):
    """Evaluated centerline of the first spline in object space, matching the
    curve's own resolution."""
    if not curve.splines:
        return []
    spline = curve.splines[0]
    res = max(curve.resolution_u, 1)
    if spline.type == "BEZIER":
        bps = spline.bezier_points
        pts = []
        for a, b in zip(bps, bps[1:]):
            seg = interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, res + 1)
            pts.extend(seg if not pts else seg[1:])
    else:
        pts = [Vector(p.co[:3]) for p in spline.points]
    out = [pts[0]] if pts else []
    for p in pts[1:]:
        if (p - out[-1]).length > 1e-7:
            out.append(p)
    return out


def _tangents(points):
    n = len(points)
    out = []
    for i in range(n):
        a = points[max(i - 1, 0)]
        b = points[min(i + 1, n - 1)]
        t = b - a
        out.append(t.normalized() if t.length > 1e-12 else Vector((1, 0, 0)))
    return out


def _initial_normal(tangent, world_rot):
    """Perpendicular to the tangent that points as far down in world space as
    possible, so the UV seam starts on the underside."""
    down = world_rot.inverted_safe() @ Vector((0.0, 0.0, -1.0))
    n = down - tangent * down.dot(tangent)
    if n.length < 1e-6:
        n = tangent.orthogonal()
    return n.normalized()


def frames(points, world_rot):
    """Rotation minimizing frames (no twist) along the polyline."""
    tangents = _tangents(points)
    normal = _initial_normal(tangents[0], world_rot)
    out = []
    prev_t = tangents[0]
    for t in tangents:
        rot = prev_t.rotation_difference(t)
        normal = (rot @ normal)
        normal = (normal - t * normal.dot(t)).normalized()
        out.append((t, normal, t.cross(normal)))
        prev_t = t
    return out


def uv_layout(options, cap_count, total_length):
    """Place UV islands. Returns (place, cap_centers, tube_offset).

    Layout space: `across` runs 0..1 around the curve and `length` runs
    along it in circumference units, so texels stay square. `place(across,
    length)` maps layout space to UVs; the side island starts at
    `tube_offset` along the length.

    Without a trim slot the side island sits at the origin with the caps
    beside it, across on U and length on V (swapped with length_along_u).
    With a slot (options.trim), across spans the slot's short side exactly,
    length follows its long side and repeats past it, and the caps sit at the
    start of the strip, before the side island, all `pad_layout` apart.
    """
    cap_d = CAP_UV_RADIUS * 2.0
    trim = options.trim

    if trim is not None:
        pad = trim.pad_layout
        cap_centers = [(0.5, pad + CAP_UV_RADIUS + i * (cap_d + pad)) for i in range(cap_count)]
        tube_offset = (pad + cap_count * (cap_d + pad)) if cap_count else 0.0
        u0, v0, u1, v1 = trim.rect
        a_scale, l_scale = trim.across_scale, trim.length_scale
        if trim.along_u:
            # Length on U, across on V, flipped across so islands keep their
            # winding (a plain swap would mirror them).
            def place(across, length):
                return (u0 + length * l_scale, v1 - across * a_scale)
        else:
            def place(across, length):
                return (u0 + across * a_scale, v0 + length * l_scale)
        return place, cap_centers, tube_offset

    pad = options.uv_padding
    cap_centers = [
        (1.0 + pad + CAP_UV_RADIUS, CAP_UV_RADIUS + i * (cap_d + pad))
        for i in range(cap_count)
    ]
    if options.length_along_u:
        # Swapping axes mirrors the islands; running length backwards undoes it.
        span = max(total_length, cap_centers[-1][1] + CAP_UV_RADIUS if cap_centers else 0.0)

        def place(across, length):
            return (span - length, across)
    else:
        def place(across, length):
            return (across, length)
    return place, cap_centers, 0.0


def bevel_profile(width, segments, shape):
    """Cap bevel profile from the side to the cap, like the Bevel modifier.

    Returns (axial, radial, normal) per point: `axial` is the distance past
    the start of the bevel along the curve, `radial` the change in
    radius, and `normal` the outward profile normal as (axial, radial).
    `shape` follows the Bevel modifier's Shape: 0.25 is a flat chamfer, 0.5
    round, values toward 1 approach a square corner.
    """
    k = 2.0 ** (4.0 * shape - 1.0)  # superellipse exponent: 1 flat, 2 round
    out = []
    for i in range(segments + 1):
        theta = 0.5 * math.pi * i / segments
        # Superellipse quarter around the bevel's inner corner.
        x = abs(math.cos(theta)) ** (2.0 / k)  # radial part, 1 at the side
        y = abs(math.sin(theta)) ** (2.0 / k)  # axial part, 1 at the cap
        nx = x ** (k - 1.0) if x > 0.0 else 0.0
        ny = y ** (k - 1.0) if y > 0.0 else 0.0
        length = math.hypot(nx, ny) or 1.0
        out.append((y * width, (x - 1.0) * width, (ny / length, nx / length)))
    return out


def _cut_ends(points, distance):
    """Shorten the polyline by `distance` (arc length) at both ends.
    Returns (points, distance actually cut)."""
    lengths = [0.0]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + (b - a).length)
    total = lengths[-1]
    distance = min(distance, total * 0.45)
    if distance <= 1e-9:
        return list(points), 0.0

    def at(s):
        for i in range(len(points) - 1):
            if lengths[i + 1] >= s:
                span = lengths[i + 1] - lengths[i]
                t = 0.0 if span <= 1e-12 else (s - lengths[i]) / span
                return points[i].lerp(points[i + 1], t)
        return points[-1].copy()

    inner = [p for p, l in zip(points, lengths) if distance + 1e-9 < l < total - distance - 1e-9]
    return [at(distance)] + inner + [at(total - distance)], distance


def build_bmesh(points, radius, sides, fill_caps, options, world_rot=Matrix.Identity(3)):
    """Tube mesh with UVs. Returns (bm, loop_normals).

    loop_normals holds one custom normal per loop, in face order, when exact
    normals are wanted (Harden Normals on beveled caps), else None.
    Beveled caps are built here rather than with a Bevel modifier, so the
    bevel rings continue the side's UV island instead of stretching across
    to the cap islands.
    """
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new(UV_NAME)
    if len(points) < 2 or radius <= 0.0:
        return bm, None

    bevel = fill_caps and options.cap_edges == "BEVEL" and options.bevel_width > 0.0
    width = min(options.bevel_width, radius * 0.95) if bevel else 0.0
    if bevel:
        # The bevel eats into the side, so the overall length stays the same.
        points, width = _cut_ends(points, width)
        bevel = width > 0.0

    fr = frames(points, world_rot)
    circumference = 2.0 * math.pi * radius

    # Rings in order along the curve: (center, radius, t, n, b, normal),
    # `normal` being the outward surface normal as (axial, radial) parts.
    rings = []
    profile = bevel_profile(width, options.bevel_segments, options.bevel_profile) if bevel else []
    if bevel:
        c, (t, n, b) = points[0], fr[0]
        for axial, dr, nrm in reversed(profile[1:]):
            rings.append((c - t * axial, radius + dr, t, n, b, (-nrm[0], nrm[1])))
    for c, (t, n, b) in zip(points, fr):
        rings.append((c, radius, t, n, b, (0.0, 1.0)))
    if bevel:
        c, (t, n, b) = points[-1], fr[-1]
        for axial, dr, nrm in profile[1:]:
            rings.append((c + t * axial, radius + dr, t, n, b, nrm))

    def radial(ring, j):
        _c, _r, _t, n, b, _nrm = ring
        ang = 2.0 * math.pi * j / sides
        return n * math.cos(ang) + b * math.sin(ang)

    verts = [[bm.verts.new(ring[0] + radial(ring, j) * ring[1]) for j in range(sides)] for ring in rings]

    # Length along the side island in circumference units: distance between
    # neighbouring ring rims, so the bevel continues the side island.
    lengths = [0.0]
    for (c0, r0, *_), (c1, r1, *_) in zip(rings, rings[1:]):
        lengths.append(lengths[-1] + math.hypot((c1 - c0).length, r1 - r0) / circumference)

    place, cap_centers, tube_offset = uv_layout(options, 2 if fill_caps else 0, lengths[-1])

    def surface_normal(ring, j):
        t, (axial, rad) = ring[2], ring[5]
        return (t * axial + radial(ring, j) * rad).normalized()

    loop_normals = []
    side_faces = []
    for i in range(len(rings) - 1):
        r0, r1 = verts[i], verts[i + 1]
        for j in range(sides):
            k = (j + 1) % sides
            face = bm.faces.new((r0[j], r0[k], r1[k], r1[j]))
            # Seam: the last column uses across = 1 instead of wrapping to 0.
            across = (j / sides, (j + 1) / sides, (j + 1) / sides, j / sides)
            along = (lengths[i], lengths[i], lengths[i + 1], lengths[i + 1])
            # Bevel rings are narrower than the side, so their rows taper
            # around the island's center line instead of stretching.
            taper = (rings[i][1], rings[i][1], rings[i + 1][1], rings[i + 1][1])
            for loop, a, l, r in zip(face.loops, across, along, taper):
                loop[uv_layer].uv = place(0.5 + (a - 0.5) * r / radius, tube_offset + l)
            for ri, rj in ((i, j), (i, k), (i + 1, k), (i + 1, j)):
                loop_normals.append(surface_normal(rings[ri], rj))
            side_faces.append(face)

    cap_faces = []
    if fill_caps:
        for index, center, reverse in ((0, cap_centers[0], True), (len(rings) - 1, cap_centers[1], False)):
            ring = verts[index]
            # Smaller rim after a bevel, so a smaller island at the same density.
            cap_r = CAP_UV_RADIUS * rings[index][1] / radius
            face = bm.faces.new(list(reversed(ring)) if reverse else list(ring))
            axis = rings[index][2] * (-1.0 if reverse else 1.0)
            for loop in face.loops:
                j = ring.index(loop.vert)
                ang = 2.0 * math.pi * j / sides
                # Mirror the start cap: it faces the other way along the curve.
                x = math.cos(ang) * (-1.0 if reverse else 1.0)
                y = math.sin(ang)
                loop[uv_layer].uv = place(center[0] + x * cap_r, center[1] + y * cap_r)
                loop_normals.append(axis.copy())
            cap_faces.append(face)

    for face in side_faces:
        face.smooth = options.shade_smooth
    for face in cap_faces:
        face.smooth = False

    bm.normal_update()
    exact = bevel and options.shade_smooth and options.harden_normals
    return bm, (loop_normals if exact else None)


def mesh_from_wire(obj, options, name=None):
    """New mesh datablock for a curve curve object."""
    curve = obj.data
    points = centerline(curve)
    radius = curve.bevel_depth
    sides = ring_sides(curve.bevel_resolution)
    world_rot = obj.matrix_world.to_3x3().normalized()

    bm, loop_normals = build_bmesh(points, radius, sides, curve.use_fill_caps, options, world_rot)
    hard_caps = curve.use_fill_caps and options.shade_smooth and options.cap_edges != "BEVEL"
    cap_keys = set()
    if hard_caps:
        bm.verts.index_update()
        cap_keys = {
            tuple(sorted((e.verts[0].index, e.verts[1].index)))
            for f in bm.faces if len(f.verts) > 4 for e in f.edges
        }

    mesh = bpy.data.meshes.new(name or obj.name)
    bm.to_mesh(mesh)
    bm.free()

    if cap_keys:
        attr = mesh.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
        attr.data.foreach_set("value", [tuple(sorted(e.vertices)) in cap_keys for e in mesh.edges])
    if loop_normals is not None:
        mesh.normals_split_custom_set([tuple(n) for n in loop_normals])

    for mat in curve.materials:
        mesh.materials.append(mat)
    return mesh
