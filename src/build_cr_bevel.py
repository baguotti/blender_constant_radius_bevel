"""Build the "CR Bevel" Geometry Nodes group (constant-radius bevel).

Usage inside Blender (headless or UI):
    import build_cr_bevel; ng = build_cr_bevel.build()

How it works (math in docs/PLAN.md):
1. Every selected edge gets the offset d = r*tan(phi/2), so the native Mesh
   Bevel node places the bevel boundaries exactly on the tangent lines.
2. A "skeleton" of cylinder axes is built, one segment per beveled edge.
   Each original vertex is classified, and axes are cut at the exact point
   where they meet (ball centre or chain crossing).
3. After the bevel, strip vertices are pushed onto their cylinder, corner
   patch vertices onto their sphere, and bevel ends that stop at an
   unbeveled face are kept in that face.

Vertex modes (attribute _cr_mode, per original vertex):
    0 FALLBACK  no exact answer: the native geometry is kept
    1 TERMINAL  one beveled edge ending at a 3-face vertex
    2 PIPE      two collinear beveled edges on the same cylinder
    3 JOINT     two beveled edges whose axes cross, 3 faces (chain joint)
    4 CORNER    three beveled edges whose axes meet in one point, 3 faces (ball)

Internal attributes use the prefix "_cr_" and are removed before output.
"""
import math

import bpy

GROUP_NAME = "CR Bevel"
MAX_EDGE_ANGLE = math.radians(179.0)  # tan(phi/2) explodes beyond this
FALLBACK, TERMINAL, PIPE, JOINT, CORNER = range(5)
GAP_TOL = 1e-3          # axes "meet" if closer than GAP_TOL * r
PARALLEL_TOL = 1e-8     # 1 - (u0.u1)^2 below this means parallel axes
EXTEND = 10.0           # uncut axis ends are extended by EXTEND * (r + d)
CLAMP_SHARE = 0.95      # stay this far below the exact overlap limit (avoids zero-length edges)
NO_LIMIT = 1e30
PER_EDGE_PASSES = 32    # Per Edge: passes of the shrink-until-valid loop
INLINE_SIN = math.sin(math.radians(2.0))   # BMesh bevel's in-line threshold (BEVEL_EPSILON_ANG)
CLAMP_MODES = ("Uniform", "Per Edge", "None")


def _offset_corner(B, corner, offset):
    n = B.node("GeometryNodeOffsetCornerInFace")
    B._feed(n.inputs["Corner Index"], corner)
    n.inputs["Offset"].default_value = offset
    return n.outputs["Corner Index"]


def _vertex_of(B, corner):
    n = B.node("GeometryNodeVertexOfCorner")
    B._feed(n.inputs["Corner Index"], corner)
    return n.outputs["Vertex Index"]


def _face_of(B, corner):
    n = B.node("GeometryNodeFaceOfCorner")
    B._feed(n.inputs["Corner Index"], corner)
    return n.outputs["Face Index"]


def _menu(B, gi, data_type, values):
    """Pick one of `values` (in CLAMP_MODES order) from the Clamp input.

    A menu input may feed only one Menu Switch, so a single switch (created on
    first use) outputs the mode number and the values are chosen from it.
    """
    if not hasattr(B, "clamp_mode"):
        menu = B.node("GeometryNodeMenuSwitch", data_type="INT")
        menu.enum_items.clear()
        for i, item in enumerate(CLAMP_MODES):
            menu.enum_items.new(item)
            menu.inputs[item].default_value = i
        B.link(gi.outputs["Clamp"], menu.inputs["Menu"])
        B.clamp_mode = menu.outputs["Output"]
    out = values[0]
    for i in range(1, len(values)):
        out = B.switch(data_type, B.compare("EQUAL", B.clamp_mode, i, data_type="INT"), out, values[i])
    return out


class Builder:
    """Small helper that keeps node creation and linking readable."""

    COL_W, ROW_H, PER_COL, STAGE_GAP = 200, 170, 5, 120

    def __init__(self, ng):
        self.ng = ng
        self.frame = None
        self.x0 = 0      # left edge of the current stage
        self.i = 0       # nodes placed in the current stage

    def stage(self, label):
        """Start a new labelled frame; following nodes are laid out inside it."""
        if self.frame is not None:
            self.x0 += (self.i // self.PER_COL + 1) * self.COL_W + self.STAGE_GAP
        self.frame = self.ng.nodes.new("NodeFrame")
        self.frame.label = label
        self.frame.label_size = 20
        self.i = 0

    def node(self, idname, **props):
        n = self.ng.nodes.new(idname)
        for k, v in props.items():
            setattr(n, k, v)
        if self.frame is not None:
            n.parent = self.frame
        col, row = divmod(self.i, self.PER_COL)
        n.location = (self.x0 + col * self.COL_W, -row * self.ROW_H)
        self.i += 1
        return n

    def link(self, out_socket, in_socket):
        self.ng.links.new(out_socket, in_socket)

    # ---- typed shortcuts -------------------------------------------------
    def math(self, op, a, b=None):
        n = self.node("ShaderNodeMath", operation=op)
        self._feed(n.inputs[0], a)
        if b is not None:
            self._feed(n.inputs[1], b)
        return n.outputs[0]

    def vmath(self, op, a, b=None, scale=None):
        n = self.node("ShaderNodeVectorMath", operation=op)
        self._feed(n.inputs[0], a)
        if b is not None:
            self._feed(n.inputs[1], b)
        if scale is not None:
            self._feed(n.inputs["Scale"], scale)
        out = n.outputs["Value"] if op in ("LENGTH", "DOT_PRODUCT", "DISTANCE") else n.outputs["Vector"]
        return out

    def bool_and(self, *items):
        acc = items[0]
        for it in items[1:]:
            n = self.node("FunctionNodeBooleanMath", operation="AND")
            self._feed(n.inputs[0], acc)
            self._feed(n.inputs[1], it)
            acc = n.outputs[0]
        return acc

    def bool_or(self, a, b):
        n = self.node("FunctionNodeBooleanMath", operation="OR")
        self._feed(n.inputs[0], a)
        self._feed(n.inputs[1], b)
        return n.outputs[0]

    def compare(self, op, a, b, data_type="FLOAT"):
        n = self.node("FunctionNodeCompare", data_type=data_type, operation=op)
        self._feed(n.inputs["A"], a)
        self._feed(n.inputs["B"], b)
        return n.outputs["Result"]

    def named(self, name, data_type):
        n = self.node("GeometryNodeInputNamedAttribute", data_type=data_type)
        n.inputs["Name"].default_value = name
        return n.outputs["Attribute"]

    def store(self, geo, name, value, data_type, domain, selection=None):
        n = self.node("GeometryNodeStoreNamedAttribute", data_type=data_type, domain=domain)
        n.inputs["Name"].default_value = name
        self.link(geo, n.inputs["Geometry"])
        self._feed(n.inputs["Value"], value)
        if selection is not None:
            self._feed(n.inputs["Selection"], selection)
        return n.outputs["Geometry"]

    # ---- extra shortcuts used by the per-vertex classification -----------
    def dot(self, a, b):
        return self.vmath("DOT_PRODUCT", a, b)

    def add(self, a, b):
        return self.vmath("ADD", a, b)

    def sub(self, a, b):
        return self.vmath("SUBTRACT", a, b)

    def scale(self, v, k):
        return self.vmath("SCALE", v, scale=k)

    def bool_not(self, a):
        n = self.node("FunctionNodeBooleanMath", operation="NOT")
        self._feed(n.inputs[0], a)
        return n.outputs[0]

    def switch(self, input_type, cond, if_false, if_true):
        n = self.node("GeometryNodeSwitch", input_type=input_type)
        self._feed(n.inputs["Switch"], cond)
        self._feed(n.inputs["False"], if_false)
        self._feed(n.inputs["True"], if_true)
        return n.outputs["Output"]

    def at_index(self, domain, data_type, value, index):
        n = self.node("GeometryNodeFieldAtIndex", domain=domain, data_type=data_type)
        self._feed(n.inputs["Value"], value)
        self._feed(n.inputs["Index"], index)
        return n.outputs["Value"]

    def dist_to_line(self, p, a, u):
        """Distance from point p to the infinite line a + t*u (u unit)."""
        w = self.sub(p, a)
        return self.vmath("LENGTH", self.sub(w, self.scale(u, self.dot(w, u))))

    def _feed(self, socket, value):
        if isinstance(value, bpy.types.NodeSocket):
            self.link(value, socket)
        else:
            socket.default_value = value


def _interface(ng):
    it = ng.interface
    it.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    s = it.new_socket("Selection", in_out="INPUT", socket_type="NodeSocketBool")
    s.default_value = True
    s.hide_value = False
    s.description = "Edges to bevel (combined with Angle Limit)"
    s = it.new_socket("Radius", in_out="INPUT", socket_type="NodeSocketFloat")
    s.default_value, s.min_value, s.subtype = 0.05, 0.0, "DISTANCE"
    s.force_non_field = True
    s.description = "Fillet radius, the same on every edge"
    s = it.new_socket("Segments", in_out="INPUT", socket_type="NodeSocketInt")
    s.default_value, s.min_value, s.max_value = 4, 1, 256
    s.force_non_field = True
    s = it.new_socket("Angle Limit", in_out="INPUT", socket_type="NodeSocketFloat")
    s.default_value, s.min_value, s.max_value, s.subtype = math.radians(30.0), 0.0, math.pi, "ANGLE"
    s.force_non_field = True
    s.description = "Only bevel edges whose face normals differ by more than this"
    s = it.new_socket("Exact Profile", in_out="INPUT", socket_type="NodeSocketBool")
    s.default_value = True
    s.force_non_field = True
    s.description = "Snap the bevel onto true circular arcs (off = native profile)"
    s = it.new_socket("Clamp", in_out="INPUT", socket_type="NodeSocketMenu")
    s.force_non_field = True
    s.description = ("Avoid overlapping bevels. Uniform: lower the radius everywhere so it stays "
                     "constant. Per Edge: lower it only on edges that need it. None: no limit")
    s = it.new_socket("Analytic Normals", in_out="INPUT", socket_type="NodeSocketBool")
    s.default_value = True
    s.force_non_field = True
    s.description = "Exact shading normals on the bevel; original faces keep their smooth/flat shading"
    s = it.new_socket("Set Bevel Material", in_out="INPUT", socket_type="NodeSocketBool")
    s.default_value = False
    s.force_non_field = True
    s = it.new_socket("Bevel Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    s.description = "Material for the new bevel faces (used when Set Bevel Material is on)"
    it.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")


def build(name=GROUP_NAME, debug_output=None):
    """Create (or replace) the node group and return it.

    debug_output="skeleton" outputs the helper skeleton instead of the mesh
    (used by tests only).
    """
    old = bpy.data.node_groups.get(name)
    if old:
        bpy.data.node_groups.remove(old)
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.is_modifier = True
    ng.description = "Constant-radius (rolling-ball) bevel built on the Mesh Bevel node"
    _interface(ng)
    B = Builder(ng)
    gi = B.node("NodeGroupInput")
    gi.location = (-400, 0)

    prepared, warning = _edge_data(B, gi)
    prepared = _vertex_modes(B, prepared)
    skeleton = _skeleton(B, gi, prepared)
    beveled = _bevel(B, gi, prepared)
    snapped = _snap(B, gi, beveled, skeleton)
    finished = _finish(B, gi, snapped, warning)

    B.stage("11. Remove internal attributes")
    rm = B.node("GeometryNodeRemoveAttribute")
    B.link(finished, rm.inputs["Geometry"])
    rm.inputs["Pattern Mode"].default_value = "Wildcard"
    rm.inputs["Name"].default_value = "_cr_*"

    go = B.node("NodeGroupOutput")
    go.parent = None
    go.location = (B.x0 + 2 * B.COL_W + B.STAGE_GAP, 0)
    B.link(skeleton if debug_output == "skeleton" else rm.outputs["Geometry"], go.inputs["Geometry"])
    for item in ng.interface.items_tree:
        if getattr(item, "name", "") == "Clamp" and item.in_out == "INPUT":
            item.default_value = CLAMP_MODES[0]
    return ng


def _edge_data(B, gi):
    """Stages 1-3: clamp, per-edge offset, axis offset, direction and convexity.

    Returns the prepared geometry and (show, message) for the clamp warning.
    """
    B.stage("1. Clamp: largest radius each edge can take without overlapping")
    radius_in = gi.outputs["Radius"]
    ang = B.node("GeometryNodeInputMeshEdgeAngle")
    # Measured in 5.2.4: convex > 0, concave < 0 (the manual states the opposite).
    sigma = ang.outputs["Signed Angle"]
    phi = ang.outputs["Unsigned Angle"]
    half = B.math("MULTIPLY", phi, 0.5)
    tan_half = B.math("TANGENT", half)
    valid = B.bool_and(
        gi.outputs["Selection"],
        B.compare("GREATER_THAN", phi, gi.outputs["Angle Limit"]),
        B.compare("GREATER_THAN", phi, 1e-4),
        B.compare("LESS_THAN", phi, MAX_EDGE_ANGLE),
        B.compare("GREATER_THAN", radius_in, 0.0),
    )
    ev = B.node("GeometryNodeInputMeshEdgeVertices")
    length = B.vmath("DISTANCE", ev.outputs["Position 1"], ev.outputs["Position 2"])
    # Offset per unit radius: D = tan(phi/2) on beveled edges, 0 elsewhere.
    unit_d = B.switch("FLOAT", valid, 0.0, tan_half)

    # Exact overlap limit, per face corner c whose outgoing edge g runs to corner c+1.
    # The native bevel puts its boundary points where offset lines cross; along g
    # they move in by t0 = (D_prev + D_g cos a)/sin a at c and by
    # t1 = (D_next + D_g cos b)/sin b at c+1 (a, b = interior angles).
    # Overlap starts when r * (t0 + t1) > |g|, so r_max(c) = |g| / (t0 + t1).
    def corner_geometry(corner):
        """Interior angle (cos, sin) of the face at `corner`; sin < 0 for reflex corners."""
        here = B.at_index("POINT", "FLOAT_VECTOR", B.node("GeometryNodeInputPosition").outputs["Position"],
                          _vertex_of(B, corner))
        nxt = B.at_index("POINT", "FLOAT_VECTOR", B.node("GeometryNodeInputPosition").outputs["Position"],
                         _vertex_of(B, _offset_corner(B, corner, 1)))
        prv = B.at_index("POINT", "FLOAT_VECTOR", B.node("GeometryNodeInputPosition").outputs["Position"],
                         _vertex_of(B, _offset_corner(B, corner, -1)))
        a = B.vmath("NORMALIZE", B.sub(prv, here))
        b = B.vmath("NORMALIZE", B.sub(nxt, here))
        face_normal = B.at_index("FACE", "FLOAT_VECTOR", B.node("GeometryNodeInputNormal").outputs["Normal"],
                                 _face_of(B, corner))
        return B.dot(a, b), B.dot(B.vmath("CROSS_PRODUCT", b, a), face_normal)

    index = B.node("GeometryNodeInputIndex").outputs["Index"]
    c1 = _offset_corner(B, index, 1)
    eoc0 = B.node("GeometryNodeEdgesOfCorner")
    B.link(index, eoc0.inputs["Corner Index"])
    eoc1 = B.node("GeometryNodeEdgesOfCorner")
    B.link(c1, eoc1.inputs["Corner Index"])
    g = eoc0.outputs["Next Edge Index"]
    g_len = B.at_index("EDGE", "FLOAT", length, g)
    cos_a, sin_a = corner_geometry(index)
    cos_b, sin_b = corner_geometry(c1)

    def shift(num, sin):
        """num / sin. Near-straight corners are placed specially by the native bevel
        (its in-line case, within 2 degrees) and don't constrain the radius."""
        tiny = B.compare("LESS_THAN", B.math("ABSOLUTE", sin), INLINE_SIN)
        return B.switch("FLOAT", tiny, B.math("DIVIDE", num, sin), 0.0)

    def shrink_of(d_field):
        """t0 + t1 along edge g for the given per-edge offsets (signed, reflex included)."""
        d_g = B.at_index("EDGE", "FLOAT", d_field, g)
        d_prev = B.at_index("EDGE", "FLOAT", d_field, eoc0.outputs["Previous Edge Index"])
        d_next = B.at_index("EDGE", "FLOAT", d_field, eoc1.outputs["Next Edge Index"])
        t0 = shift(B.math("ADD", d_prev, B.math("MULTIPLY", d_g, cos_a)), sin_a)
        t1 = shift(B.math("ADD", d_next, B.math("MULTIPLY", d_g, cos_b)), sin_b)
        return B.math("ADD", t0, t1)

    def per_edge_min(corner_value):
        """For each edge: the smallest value over the corners whose constraint involves it
        (the corners where it is g, and the corners just before and after them)."""
        near = B.math("MINIMUM", corner_value, B.math("MINIMUM",
                      B.at_index("CORNER", "FLOAT", corner_value, _offset_corner(B, index, -1)),
                      B.at_index("CORNER", "FLOAT", corner_value, c1)))
        coe = B.node("GeometryNodeCornersOfEdge")
        B.link(near, coe.inputs["Weights"])
        return B.switch("FLOAT", B.compare("GREATER_THAN", coe.outputs["Total"], 0, data_type="INT"), NO_LIMIT,
                        B.at_index("CORNER", "FLOAT", near, coe.outputs["Corner Index"]))

    # Uniform: every offset is r * D, so each corner gives r <= |g| / shrink(D).
    k_unit = shrink_of(unit_d)
    r_corner = B.switch("FLOAT", B.compare("GREATER_THAN", k_unit, 1e-9), NO_LIMIT, B.math("DIVIDE", g_len, k_unit))
    stat = B.node("GeometryNodeAttributeStatistic", domain="CORNER", data_type="FLOAT")
    B.link(gi.outputs["Geometry"], stat.inputs["Geometry"])
    B.link(r_corner, stat.inputs["Attribute"])
    r_uniform = B.math("MINIMUM", radius_in, B.math("MULTIPLY", stat.outputs["Min"], CLAMP_SHARE))

    # Per Edge: start from each edge's own limit. Radii then differ, so check
    # every corner with the actual offsets; where one is violated, give its three
    # edges their smallest radius, capped by the corner's uniform limit (equal
    # radii within that limit always satisfy it). Values never drop below the
    # Uniform result, which is the worst case. Zero passes unless Per Edge.
    # (Straight chains longer than PER_EDGE_PASSES pieces may not fully equalize.)
    steps = _menu(B, gi, "INT", (0, PER_EDGE_PASSES, 0))
    r_start = B.math("MINIMUM", radius_in, B.math("MULTIPLY", per_edge_min(r_corner), CLAMP_SHARE))
    geo = B.store(gi.outputs["Geometry"], "_cr_rpe", r_start, "FLOAT", "EDGE")
    rin, rout = B.node("GeometryNodeRepeatInput"), B.node("GeometryNodeRepeatOutput")
    rin.pair_with_output(rout)
    B.link(steps, rin.inputs["Iterations"])
    B.link(geo, rin.inputs["Geometry"])
    r_cur = B.named("_cr_rpe", "FLOAT")
    k_now = shrink_of(B.math("MULTIPLY", r_cur, unit_d))
    violated = B.compare("GREATER_THAN", k_now, B.math("MULTIPLY", g_len, CLAMP_SHARE))
    r_three = B.math("MINIMUM", B.at_index("EDGE", "FLOAT", r_cur, g), B.math("MINIMUM",
                     B.at_index("EDGE", "FLOAT", r_cur, eoc0.outputs["Previous Edge Index"]),
                     B.at_index("EDGE", "FLOAT", r_cur, eoc1.outputs["Next Edge Index"])))
    target = B.switch("FLOAT", violated, NO_LIMIT,
                      B.math("MINIMUM", r_three, B.math("MULTIPLY", r_corner, CLAMP_SHARE)))
    # Pieces of one straight edge (in-line corners) share the smaller radius,
    # otherwise the bevel's width steps along the edge.
    inline = B.bool_and(B.compare("LESS_THAN", B.math("ABSOLUTE", sin_a), INLINE_SIN),
                        B.compare("LESS_THAN", cos_a, 0.0))
    pair_min = B.math("MINIMUM", B.at_index("EDGE", "FLOAT", r_cur, g),
                      B.at_index("EDGE", "FLOAT", r_cur, eoc0.outputs["Previous Edge Index"]))
    target = B.math("MINIMUM", target, B.switch("FLOAT", inline, NO_LIMIT, pair_min))
    looped = B.store(rin.outputs["Geometry"], "_cr_rpe", B.math("MINIMUM", r_cur, per_edge_min(target)),
                     "FLOAT", "EDGE")
    B.link(looped, rout.inputs["Geometry"])
    geo = rout.outputs["Geometry"]

    radius = _menu(B, gi, "FLOAT", (r_uniform, B.named("_cr_rpe", "FLOAT"), radius_in))

    # Warning text: how much the radius was lowered.
    clamped = B.compare("LESS_THAN", radius, B.math("MULTIPLY", radius_in, 0.9999))
    n_clamped = B.node("GeometryNodeAttributeStatistic", domain="EDGE", data_type="FLOAT")
    B.link(geo, n_clamped.inputs["Geometry"])
    B.link(valid, n_clamped.inputs["Selection"])
    B.link(clamped, n_clamped.inputs["Attribute"])
    min_r = B.node("GeometryNodeAttributeStatistic", domain="EDGE", data_type="FLOAT")
    B.link(geo, min_r.inputs["Geometry"])
    B.link(valid, min_r.inputs["Selection"])
    B.link(radius, min_r.inputs["Attribute"])
    n_txt = B.node("FunctionNodeValueToString", data_type="INT")
    B.link(n_clamped.outputs["Sum"], n_txt.inputs["Value"])
    r_txt = B.node("FunctionNodeValueToString", data_type="FLOAT")
    B.link(min_r.outputs["Min"], r_txt.inputs["Value"])
    r_txt.inputs["Decimals"].default_value = 4
    join = B.node("GeometryNodeStringJoin")
    join.inputs["Delimiter"].default_value = ""
    parts = (n_txt.outputs["String"], " edge(s) clamped to avoid overlaps; smallest radius used: ",
             r_txt.outputs["String"])
    for part in reversed(parts):   # a multi-input socket stacks new links on top
        if isinstance(part, str):
            sn = B.node("FunctionNodeInputString")
            sn.string = part
            part = sn.outputs["String"]
        B.link(part, join.inputs["Strings"])
    warning = (B.compare("GREATER_THAN", n_clamped.outputs["Sum"], 0.5), join.outputs["String"])

    B.stage("2. Per-edge offset d = r tan(phi/2) and cylinder axis")
    d = B.math("MULTIPLY", radius, tan_half)
    convex_sign = B.math("SIGN", sigma)                       # s = +1 convex, -1 concave
    # Axis = edge - s * r / cos(phi/2) * bisector of the outward normals.
    k = B.math("MULTIPLY", B.math("MULTIPLY", convex_sign, -1.0),
               B.math("DIVIDE", radius, B.math("COSINE", half)))
    # Bisector of the two face normals. Normal read directly on an edge averages
    # the vertex normals, which is wrong here, so evaluate it on faces first.
    face_n = B.node("GeometryNodeFieldOnDomain", domain="FACE", data_type="FLOAT_VECTOR")
    B.link(B.node("GeometryNodeInputNormal").outputs["Normal"], face_n.inputs["Value"])
    bis = B.vmath("NORMALIZE", face_n.outputs["Value"])
    axis_off = B.scale(bis, k)
    direction = B.vmath("NORMALIZE", B.sub(ev.outputs["Position 2"], ev.outputs["Position 1"]))

    B.stage("3. Store per-edge data (_cr_* attributes)")
    g = geo
    g = B.store(g, "_cr_vid", B.node("GeometryNodeInputIndex").outputs["Index"], "INT", "POINT")
    g = B.store(g, "_cr_vpos", B.node("GeometryNodeInputPosition").outputs["Position"], "FLOAT_VECTOR", "POINT")
    g = B.store(g, "_cr_sel", valid, "BOOLEAN", "EDGE")
    g = B.store(g, "_cr_self", valid, "FLOAT", "EDGE")
    g = B.store(g, "_cr_d", d, "FLOAT", "EDGE")
    g = B.store(g, "_cr_r", radius, "FLOAT", "EDGE")
    g = B.store(g, "_cr_s", convex_sign, "FLOAT", "EDGE")
    g = B.store(g, "_cr_off", axis_off, "FLOAT_VECTOR", "EDGE")
    g = B.store(g, "_cr_u", direction, "FLOAT_VECTOR", "EDGE")
    g = B.store(g, "_cr_va", ev.outputs["Vertex Index 1"], "INT", "EDGE")
    g = B.store(g, "_cr_vb", ev.outputs["Vertex Index 2"], "INT", "EDGE")
    return g, warning


def _vertex_modes(B, geo):
    """Stages 4-5: classify each original vertex and compute where its axes meet."""
    B.stage("4. Classify vertices: terminal / pipe / joint / corner")
    pos = B.node("GeometryNodeInputPosition").outputs["Position"]
    nb = B.node("GeometryNodeInputMeshVertexNeighbors")
    # Number of beveled edges at the vertex: mean of the 0/1 edge flag times edge count.
    count = B.math("ROUND", B.math("MULTIPLY", B.named("_cr_self", "FLOAT"), nb.outputs["Vertex Count"]))
    weights = B.math("SUBTRACT", 1.0, B.named("_cr_self", "FLOAT"))   # beveled edges sort first

    lines = []
    for k in range(3):
        eov = B.node("GeometryNodeEdgesOfVertex")
        B.link(weights, eov.inputs["Weights"])
        eov.inputs["Sort Index"].default_value = k
        e = eov.outputs["Edge Index"]
        off = B.at_index("EDGE", "FLOAT_VECTOR", B.named("_cr_off", "FLOAT_VECTOR"), e)
        u = B.at_index("EDGE", "FLOAT_VECTOR", B.named("_cr_u", "FLOAT_VECTOR"), e)
        s = B.at_index("EDGE", "FLOAT", B.named("_cr_s", "FLOAT"), e)
        rk = B.at_index("EDGE", "FLOAT", B.named("_cr_r", "FLOAT"), e)
        # Edge direction pointing away from this vertex: other end = p1 + p2 - here.
        ends = B.node("GeometryNodeInputMeshEdgeVertices")
        p12 = B.add(B.at_index("EDGE", "FLOAT_VECTOR", ends.outputs["Position 1"], e),
                    B.at_index("EDGE", "FLOAT_VECTOR", ends.outputs["Position 2"], e))
        away = B.vmath("NORMALIZE", B.sub(p12, B.scale(pos, 2.0)))
        lines.append((B.add(pos, off), u, s, away, rk))
    (a0, u0, s0, away0, r0), (a1, u1, s1, away1, r1), (a2, u2, s2, _, r2) = lines
    tol = B.math("MULTIPLY", r0, GAP_TOL)   # relative to the radius actually used here

    # Closest points between axis lines 0 and 1.
    w = B.sub(a0, a1)
    b = B.dot(u0, u1)
    dd = B.dot(u0, w)
    ee = B.dot(u1, w)
    den = B.math("SUBTRACT", 1.0, B.math("MULTIPLY", b, b))
    t0 = B.math("DIVIDE", B.math("SUBTRACT", B.math("MULTIPLY", b, ee), dd), den)
    t1 = B.math("DIVIDE", B.math("SUBTRACT", ee, B.math("MULTIPLY", b, dd)), den)
    p0 = B.add(a0, B.scale(u0, t0))
    p1 = B.add(a1, B.scale(u1, t1))
    meet = B.scale(B.add(p0, p1), 0.5)
    gap_ok = B.compare("LESS_THAN", B.vmath("DISTANCE", p0, p1), tol)
    crossing = B.compare("GREATER_EQUAL", den, PARALLEL_TOL)
    # Same convexity AND same radius (Per Edge clamp can give edges different radii).
    same01 = B.bool_and(B.compare("EQUAL", s0, s1), B.compare("LESS_THAN", B.math("ABSOLUTE", B.math("SUBTRACT", r0, r1)), tol))
    same12 = B.bool_and(B.compare("EQUAL", s1, s2), B.compare("LESS_THAN", B.math("ABSOLUTE", B.math("SUBTRACT", r1, r2)), tol))

    is1 = B.compare("EQUAL", count, 1.0)
    is2 = B.compare("EQUAL", count, 2.0)
    is3 = B.compare("EQUAL", count, 3.0)
    three_faces = B.compare("EQUAL", nb.outputs["Face Count"], 3, data_type="INT")
    terminal = B.bool_and(is1, three_faces)
    pipe = B.bool_and(is2, B.bool_not(crossing), same01, B.compare("LESS_THAN", B.dist_to_line(a1, a0, u0), tol))
    # JOINT and CORNER need the standard layout: exactly 3 faces at the vertex
    # (otherwise e.g. folded faces or extra unbeveled edges break the formulas).
    joint = B.bool_and(is2, three_faces, crossing, gap_ok, same01)
    corner = B.bool_and(is3, three_faces, crossing, gap_ok, same01, same12,
                        B.compare("LESS_THAN", B.dist_to_line(meet, a2, u2), tol))
    mode = B.math("ADD",
                  B.math("ADD", B.math("MULTIPLY", terminal, TERMINAL), B.math("MULTIPLY", pipe, PIPE)),
                  B.math("ADD", B.math("MULTIPLY", joint, JOINT), B.math("MULTIPLY", corner, CORNER)))

    # End face of a terminal vertex: of its 3 faces, the one most aligned with the edge.
    best_n, best_a = None, None
    for k in range(3):
        cov = B.node("GeometryNodeCornersOfVertex")
        cov.inputs["Sort Index"].default_value = k
        foc = B.node("GeometryNodeFaceOfCorner")
        B.link(cov.outputs["Corner Index"], foc.inputs["Corner Index"])
        n = B.at_index("FACE", "FLOAT_VECTOR", B.node("GeometryNodeInputNormal").outputs["Normal"],
                       foc.outputs["Face Index"])
        a = B.math("ABSOLUTE", B.dot(n, u0))
        if best_n is None:
            best_n, best_a = n, a
        else:
            better = B.compare("GREATER_THAN", a, best_a)
            best_n = B.switch("VECTOR", better, best_n, n)
            best_a = B.math("MAXIMUM", a, best_a)

    B.stage("5. Store per-vertex data")
    geo = B.store(geo, "_cr_mode", mode, "INT", "POINT")
    geo = B.store(geo, "_cr_X", meet, "FLOAT_VECTOR", "POINT")
    geo = B.store(geo, "_cr_endn", best_n, "FLOAT_VECTOR", "POINT")
    geo = B.store(geo, "_cr_u0", u0, "FLOAT_VECTOR", "POINT")
    geo = B.store(geo, "_cr_rv", r0, "FLOAT", "POINT")      # radius at this vertex
    geo = B.store(geo, "_cr_sv", s0, "FLOAT", "POINT")      # convexity at this vertex
    geo = B.store(geo, "_cr_u1", u1, "FLOAT_VECTOR", "POINT")
    # JOINT seam plane: through the meeting point, normal = difference of the
    # two away-from-vertex edge directions (it bisects the two edges).
    geo = B.store(geo, "_cr_seam_n", B.sub(away0, away1), "FLOAT_VECTOR", "POINT")
    return geo


def _skeleton(B, gi, prepared):
    """Stage 6: axis segments, two copies per edge, grouped by end vertex.

    A bevel vertex keeps its original vertex id, so it only searches the axes
    of edges at its own original vertex. Axis ends at JOINT/CORNER vertices
    are cut at the meeting point; other ends are extended.
    """
    B.stage("6. Skeleton: cylinder axes, cut where they meet")
    dup = B.node("GeometryNodeDuplicateElements", domain="EDGE")
    B.link(prepared, dup.inputs["Geometry"])
    B.link(B.named("_cr_sel", "BOOLEAN"), dup.inputs["Selection"])
    dup.inputs["Amount"].default_value = 2

    pos = B.node("GeometryNodeInputPosition").outputs["Position"]
    ev = B.node("GeometryNodeInputMeshEdgeVertices")
    mid = B.scale(B.add(ev.outputs["Position 1"], ev.outputs["Position 2"]), 0.5)
    away = B.vmath("NORMALIZE", B.sub(pos, mid))
    reach = B.math("MULTIPLY", B.math("ADD", B.named("_cr_r", "FLOAT"), B.named("_cr_d", "FLOAT")), EXTEND)
    extended = B.add(B.add(pos, B.named("_cr_off", "FLOAT_VECTOR")), B.scale(away, reach))
    mode = B.named("_cr_mode", "INT")
    cut = B.bool_or(B.compare("EQUAL", mode, JOINT, data_type="INT"),
                    B.compare("EQUAL", mode, CORNER, data_type="INT"))
    sp = B.node("GeometryNodeSetPosition")
    B.link(dup.outputs["Geometry"], sp.inputs["Geometry"])
    B.link(B.switch("VECTOR", cut, extended, B.named("_cr_X", "FLOAT_VECTOR")), sp.inputs["Position"])

    first = B.compare("EQUAL", dup.outputs["Duplicate Index"], 0, data_type="INT")
    grp = B.switch("INT", first, B.named("_cr_vb", "INT"), B.named("_cr_va", "INT"))
    return B.store(sp.outputs["Geometry"], "_cr_grp", grp, "INT", "EDGE")


def _bevel(B, gi, prepared):
    """Stage 7: the native bevel with constant-radius offsets."""
    B.stage("7. Native Mesh Bevel with constant-radius offsets")
    bev = B.node("GeometryNodeMeshBevel")
    B.link(prepared, bev.inputs["Mesh"])
    B.link(B.named("_cr_sel", "BOOLEAN"), bev.inputs["Selection"])
    d_attr = B.named("_cr_d", "FLOAT")
    for s in ("Start Left Offset", "Start Right Offset", "End Left Offset", "End Right Offset"):
        B.link(d_attr, bev.inputs[s])
    B.link(gi.outputs["Segments"], bev.inputs["Segments"])
    bev.inputs["Shape"].default_value = 0.5
    # Stored as 0/1 floats so that reading them per vertex means "touches any such face".
    g = B.store(bev.outputs["Mesh"], "_cr_ef", bev.outputs["Edge Face"], "FLOAT", "FACE")
    g = B.store(g, "_cr_vf", bev.outputs["Vertex Face"], "FLOAT", "FACE")
    g = B.store(g, "_cr_of", B.bool_not(B.bool_or(bev.outputs["Edge Face"], bev.outputs["Vertex Face"])),
                "FLOAT", "FACE")
    return B.store(g, "cr_bevel_face", B.bool_or(bev.outputs["Edge Face"], bev.outputs["Vertex Face"]),
                   "BOOLEAN", "FACE")


def _snap(B, gi, geo, skeleton):
    """Stage 8: move vertices onto the exact cylinders and spheres."""
    B.stage("8. Snap: strips to cylinders, corners to spheres, ends into their face")
    radius = B.named("_cr_rv", "FLOAT")        # (possibly clamped) radius at this vertex
    pos = B.node("GeometryNodeInputPosition").outputs["Position"]
    mode = B.named("_cr_mode", "INT")

    prox = B.node("GeometryNodeProximity", target_element="EDGES")
    B.link(skeleton, prox.inputs["Geometry"])
    B.link(B.named("_cr_grp", "INT"), prox.inputs["Group ID"])
    B.link(B.named("_cr_vid", "INT"), prox.inputs["Sample Group ID"])
    B.link(pos, prox.inputs["Sample Position"])
    q = prox.outputs["Position"]
    on_cyl = B.add(q, B.scale(B.vmath("NORMALIZE", B.sub(pos, q)), radius))

    centre = B.named("_cr_X", "FLOAT_VECTOR")   # ball centre / axis crossing

    # TERMINAL: slide along the axis back into the end face's plane.
    n_end, u = B.named("_cr_endn", "FLOAT_VECTOR"), B.named("_cr_u0", "FLOAT_VECTOR")
    t = B.math("DIVIDE", B.dot(B.sub(B.named("_cr_vpos", "FLOAT_VECTOR"), on_cyl), n_end), B.dot(u, n_end))
    in_end_face = B.add(on_cyl, B.scale(u, t))
    is_terminal = B.compare("EQUAL", mode, TERMINAL, data_type="INT")
    strip_target = B.switch("VECTOR", is_terminal, on_cyl, in_end_face)

    # CORNER: patch vertices onto the ball.
    on_ball = B.add(centre, B.scale(B.vmath("NORMALIZE", B.sub(pos, centre)), radius))

    # JOINT: seam vertices go onto the intersection of the two cylinders. It lies
    # in the seam plane, where it is the set |w|^2 - (w.u0)^2 = r^2 (w = p - X), so
    # project into the plane and scale from X.
    nb = B.named("_cr_seam_n", "FLOAT_VECTOR")
    w = B.sub(pos, centre)
    w = B.sub(w, B.scale(nb, B.math("DIVIDE", B.dot(w, nb), B.dot(nb, nb))))
    wu = B.dot(w, u)
    lam = B.math("DIVIDE", radius,
                 B.math("SQRT", B.math("SUBTRACT", B.dot(w, w), B.math("MULTIPLY", wu, wu))))
    on_seam = B.add(centre, B.scale(w, lam))
    is_joint = B.compare("EQUAL", mode, JOINT, data_type="INT")
    strip_target = B.switch("VECTOR", is_joint, strip_target, on_seam)

    on_strip = B.compare("GREATER_THAN", B.named("_cr_ef", "FLOAT"), 0.0)
    # Joint vertices on an original face (the seam ends) keep their native place:
    # the top one is already exact, the one on the unbeveled edge cannot be exact
    # on this topology and must stay in both side faces.
    joint_end = B.bool_and(is_joint, B.compare("GREATER_THAN", B.named("_cr_of", "FLOAT"), 0.0))
    on_patch = B.compare("GREATER_THAN", B.named("_cr_vf", "FLOAT"), 0.0)
    exact_mode = B.compare("GREATER_THAN", mode, FALLBACK, data_type="INT")
    strip_sel = B.bool_and(on_strip, exact_mode, B.bool_not(joint_end), prox.outputs["Is Valid"],
                           B.compare("GREATER_THAN", prox.outputs["Distance"], 1e-9))
    patch_sel = B.bool_and(on_patch, B.bool_not(on_strip), B.compare("EQUAL", mode, CORNER, data_type="INT"))

    move = B.bool_and(gi.outputs["Exact Profile"], B.bool_or(strip_sel, patch_sel))
    target = B.switch("VECTOR", patch_sel, strip_target, on_ball)

    # Exact surface normal at the final position (outward: flipped for concave).
    sign = B.named("_cr_sv", "FLOAT")
    n_strip = B.vmath("NORMALIZE", B.sub(pos, q))           # radial direction is kept by the snap
    n_ball = B.vmath("NORMALIZE", B.sub(pos, centre))
    final = B.switch("VECTOR", move, pos, target)
    u1 = B.named("_cr_u1", "FLOAT_VECTOR")
    rad0 = B.sub(B.sub(final, centre), B.scale(u, B.dot(B.sub(final, centre), u)))
    rad1 = B.sub(B.sub(final, centre), B.scale(u1, B.dot(B.sub(final, centre), u1)))
    n_seam = B.vmath("NORMALIZE", B.add(B.vmath("NORMALIZE", rad0), B.vmath("NORMALIZE", rad1)))
    n = B.switch("VECTOR", patch_sel, n_strip, n_ball)
    n = B.switch("VECTOR", is_joint, n, n_seam)
    has_n = B.bool_and(gi.outputs["Exact Profile"], B.bool_or(move, joint_end))
    geo = B.store(geo, "_cr_n", B.scale(n, sign), "FLOAT_VECTOR", "POINT")
    geo = B.store(geo, "_cr_hasn", has_n, "BOOLEAN", "POINT")

    sp = B.node("GeometryNodeSetPosition")
    B.link(geo, sp.inputs["Geometry"])
    B.link(move, sp.inputs["Selection"])
    B.link(target, sp.inputs["Position"])
    return sp.outputs["Geometry"]


def _finish(B, gi, geo, warning):
    """Stages 9-10: exact custom normals, bevel material, clamp warning."""
    B.stage("9. Custom normals: exact on the bevel, input shading on original faces")
    face_n = B.node("GeometryNodeFieldOnDomain", domain="FACE", data_type="FLOAT_VECTOR")
    B.link(B.node("GeometryNodeInputNormal").outputs["Normal"], face_n.inputs["Value"])
    is_bevel = B.named("cr_bevel_face", "BOOLEAN")
    # Fallback: average of the adjacent bevel faces' normals only.
    masked = B.node("GeometryNodeFieldOnDomain", domain="FACE", data_type="FLOAT_VECTOR")
    B.link(B.switch("VECTOR", is_bevel, (0.0, 0.0, 0.0), face_n.outputs["Value"]), masked.inputs["Value"])
    per_point = B.node("GeometryNodeFieldOnDomain", domain="POINT", data_type="FLOAT_VECTOR")
    B.link(masked.outputs["Value"], per_point.inputs["Value"])
    smooth_bevel = B.vmath("NORMALIZE", per_point.outputs["Value"])
    # Strip vertices that are not moved (fallback corners, Exact Profile off)
    # average only the strip faces around them: the corner patch would tilt the
    # normal along the edge and make long strip quads shade unevenly.
    masked_e = B.node("GeometryNodeFieldOnDomain", domain="FACE", data_type="FLOAT_VECTOR")
    B.link(B.switch("VECTOR", B.compare("GREATER_THAN", B.named("_cr_ef", "FLOAT"), 0.5), (0.0, 0.0, 0.0),
                    face_n.outputs["Value"]), masked_e.inputs["Value"])
    per_point_e = B.node("GeometryNodeFieldOnDomain", domain="POINT", data_type="FLOAT_VECTOR")
    B.link(masked_e.outputs["Value"], per_point_e.inputs["Value"])
    on_strip = B.compare("GREATER_THAN", B.named("_cr_ef", "FLOAT"), 0.0)
    approx = B.switch("VECTOR", on_strip, smooth_bevel, B.vmath("NORMALIZE", per_point_e.outputs["Value"]))
    on_bevel = B.switch("VECTOR", B.named("_cr_hasn", "BOOLEAN"), approx, B.named("_cr_n", "FLOAT_VECTOR"))
    # Original faces keep the input's shading: flat stays flat; smooth faces use the
    # average over the adjacent smooth original faces (the bevel doesn't bleed in).
    smooth = B.node("GeometryNodeInputShadeSmooth").outputs["Smooth"]
    smooth_orig = B.bool_and(smooth, B.bool_not(is_bevel))
    masked_o = B.node("GeometryNodeFieldOnDomain", domain="FACE", data_type="FLOAT_VECTOR")
    B.link(B.switch("VECTOR", smooth_orig, (0.0, 0.0, 0.0), face_n.outputs["Value"]), masked_o.inputs["Value"])
    per_point_o = B.node("GeometryNodeFieldOnDomain", domain="POINT", data_type="FLOAT_VECTOR")
    B.link(masked_o.outputs["Value"], per_point_o.inputs["Value"])
    on_orig = B.switch("VECTOR", smooth, face_n.outputs["Value"], B.vmath("NORMALIZE", per_point_o.outputs["Value"]))
    corner_n = B.switch("VECTOR", is_bevel, on_orig, on_bevel)
    # Tangent space (not Free) so the normals survive deforming modifiers placed after this one.
    smn = B.node("GeometryNodeSetMeshNormal", mode="TANGENT_SPACE", domain="CORNER")
    B.link(geo, smn.inputs["Mesh"])
    B.link(corner_n, smn.inputs["Custom Normal"])
    geo = B.switch("GEOMETRY", gi.outputs["Analytic Normals"], geo, smn.outputs["Mesh"])

    B.stage("10. Bevel material and clamp warning")
    mat = B.node("GeometryNodeSetMaterial")
    B.link(geo, mat.inputs["Geometry"])
    B.link(B.bool_and(gi.outputs["Set Bevel Material"], B.named("cr_bevel_face", "BOOLEAN")), mat.inputs["Selection"])
    B.link(gi.outputs["Bevel Material"], mat.inputs["Material"])
    geo = mat.outputs["Geometry"]
    warn = B.node("GeometryNodeWarning", warning_type="WARNING")
    B.link(warning[0], warn.inputs["Show"])
    B.link(warning[1], warn.inputs["Message"])
    # The warning only shows if its output is used: route the geometry through it.
    return B.switch("GEOMETRY", warn.outputs["Show"], geo, geo)


if __name__ == "__main__":
    build()
