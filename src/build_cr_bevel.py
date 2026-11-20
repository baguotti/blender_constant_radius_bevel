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

    prepared = _edge_data(B, gi)
    prepared = _vertex_modes(B, gi, prepared, debug=debug_output is not None)
    skeleton = _skeleton(B, gi, prepared)
    beveled = _bevel(B, gi, prepared)
    snapped = _snap(B, gi, beveled, skeleton)

    B.stage("8. Remove internal attributes")
    rm = B.node("GeometryNodeRemoveAttribute")
    B.link(snapped, rm.inputs["Geometry"])
    rm.inputs["Pattern Mode"].default_value = "Wildcard"
    rm.inputs["Name"].default_value = "_cr_*"

    go = B.node("NodeGroupOutput")
    go.parent = None
    go.location = (B.x0 + 2 * B.COL_W + B.STAGE_GAP, 0)
    B.link(skeleton if debug_output == "skeleton" else rm.outputs["Geometry"], go.inputs["Geometry"])
    return ng


def _edge_data(B, gi):
    """Stage 1-2: per-edge offset, axis offset, direction and convexity."""
    B.stage("1. Per-edge offset d = r tan(phi/2) and cylinder axis")
    radius = gi.outputs["Radius"]
    ang = B.node("GeometryNodeInputMeshEdgeAngle")
    # Measured in 5.2.4: convex > 0, concave < 0 (the manual states the opposite).
    sigma = ang.outputs["Signed Angle"]
    phi = ang.outputs["Unsigned Angle"]
    half = B.math("MULTIPLY", phi, 0.5)
    d = B.math("MULTIPLY", radius, B.math("TANGENT", half))
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
    valid = B.bool_and(
        gi.outputs["Selection"],
        B.compare("GREATER_THAN", phi, gi.outputs["Angle Limit"]),
        B.compare("GREATER_THAN", phi, 1e-4),
        B.compare("LESS_THAN", phi, MAX_EDGE_ANGLE),
        B.compare("GREATER_THAN", radius, 0.0),
    )
    ev = B.node("GeometryNodeInputMeshEdgeVertices")
    direction = B.vmath("NORMALIZE", B.sub(ev.outputs["Position 2"], ev.outputs["Position 1"]))

    B.stage("2. Store per-edge data (_cr_* attributes)")
    g = gi.outputs["Geometry"]
    g = B.store(g, "_cr_vid", B.node("GeometryNodeInputIndex").outputs["Index"], "INT", "POINT")
    g = B.store(g, "_cr_vpos", B.node("GeometryNodeInputPosition").outputs["Position"], "FLOAT_VECTOR", "POINT")
    g = B.store(g, "_cr_sel", valid, "BOOLEAN", "EDGE")
    g = B.store(g, "_cr_self", valid, "FLOAT", "EDGE")
    g = B.store(g, "_cr_d", d, "FLOAT", "EDGE")
    g = B.store(g, "_cr_s", convex_sign, "FLOAT", "EDGE")
    g = B.store(g, "_cr_off", axis_off, "FLOAT_VECTOR", "EDGE")
    g = B.store(g, "_cr_u", direction, "FLOAT_VECTOR", "EDGE")
    g = B.store(g, "_cr_va", ev.outputs["Vertex Index 1"], "INT", "EDGE")
    g = B.store(g, "_cr_vb", ev.outputs["Vertex Index 2"], "INT", "EDGE")
    return g


def _vertex_modes(B, gi, geo, debug=False):
    """Stage 3: classify each original vertex and compute where its axes meet."""
    B.stage("3. Classify vertices: terminal / pipe / joint / corner")
    radius = gi.outputs["Radius"]
    tol = B.math("MULTIPLY", radius, GAP_TOL)
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
        # Edge direction pointing away from this vertex: other end = p1 + p2 - here.
        ends = B.node("GeometryNodeInputMeshEdgeVertices")
        p12 = B.add(B.at_index("EDGE", "FLOAT_VECTOR", ends.outputs["Position 1"], e),
                    B.at_index("EDGE", "FLOAT_VECTOR", ends.outputs["Position 2"], e))
        away = B.vmath("NORMALIZE", B.sub(p12, B.scale(pos, 2.0)))
        lines.append((B.add(pos, off), u, s, away))
    (a0, u0, s0, away0), (a1, u1, s1, away1), (a2, u2, s2, _) = lines

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
    same01 = B.compare("EQUAL", s0, s1)
    same12 = B.compare("EQUAL", s1, s2)

    is1 = B.compare("EQUAL", count, 1.0)
    is2 = B.compare("EQUAL", count, 2.0)
    is3 = B.compare("EQUAL", count, 3.0)
    three_faces = B.compare("EQUAL", nb.outputs["Face Count"], 3, data_type="INT")
    terminal = B.bool_and(is1, three_faces)
    pipe = B.bool_and(is2, B.bool_not(crossing), B.compare("LESS_THAN", B.dist_to_line(a1, a0, u0), tol))
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

    B.stage("4. Store per-vertex data")
    geo = B.store(geo, "_cr_mode", mode, "INT", "POINT")
    geo = B.store(geo, "_cr_X", meet, "FLOAT_VECTOR", "POINT")
    geo = B.store(geo, "_cr_endn", best_n, "FLOAT_VECTOR", "POINT")
    geo = B.store(geo, "_cr_u0", u0, "FLOAT_VECTOR", "POINT")
    # JOINT seam plane: through the meeting point, normal = difference of the
    # two away-from-vertex edge directions (it bisects the two edges).
    geo = B.store(geo, "_cr_seam_n", B.sub(away0, away1), "FLOAT_VECTOR", "POINT")
    if debug:
        geo = B.store(geo, "_cr_dbg_count", count, "FLOAT", "POINT")
        geo = B.store(geo, "_cr_dbg_gap", B.vmath("DISTANCE", p0, p1), "FLOAT", "POINT")
        geo = B.store(geo, "_cr_dbg_den", den, "FLOAT", "POINT")
        geo = B.store(geo, "_cr_dbg_s", B.add(B.add(B.scale((1, 0, 0), s0), B.scale((0, 1, 0), s1)),
                                               B.scale((0, 0, 1), s2)), "FLOAT_VECTOR", "POINT")
    return geo


def _skeleton(B, gi, prepared):
    """Stage 5: axis segments, two copies per edge, grouped by end vertex.

    A bevel vertex keeps its original vertex id, so it only searches the axes
    of edges at its own original vertex. Axis ends at JOINT/CORNER vertices
    are cut at the meeting point; other ends are extended.
    """
    B.stage("5. Skeleton: cylinder axes, cut where they meet")
    dup = B.node("GeometryNodeDuplicateElements", domain="EDGE")
    B.link(prepared, dup.inputs["Geometry"])
    B.link(B.named("_cr_sel", "BOOLEAN"), dup.inputs["Selection"])
    dup.inputs["Amount"].default_value = 2

    pos = B.node("GeometryNodeInputPosition").outputs["Position"]
    ev = B.node("GeometryNodeInputMeshEdgeVertices")
    mid = B.scale(B.add(ev.outputs["Position 1"], ev.outputs["Position 2"]), 0.5)
    away = B.vmath("NORMALIZE", B.sub(pos, mid))
    reach = B.math("MULTIPLY", B.math("ADD", gi.outputs["Radius"], B.named("_cr_d", "FLOAT")), EXTEND)
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
    """Stage 6: the native bevel with constant-radius offsets."""
    B.stage("6. Native Mesh Bevel with constant-radius offsets")
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
    """Stage 7: move vertices onto the exact cylinders and spheres."""
    B.stage("7. Snap: strips to cylinders, corners to spheres, ends into their face")
    radius = gi.outputs["Radius"]
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

    sp = B.node("GeometryNodeSetPosition")
    B.link(geo, sp.inputs["Geometry"])
    B.link(B.bool_and(gi.outputs["Exact Profile"], B.bool_or(strip_sel, patch_sel)), sp.inputs["Selection"])
    B.link(B.switch("VECTOR", patch_sel, strip_target, on_ball), sp.inputs["Position"])
    return sp.outputs["Geometry"]


if __name__ == "__main__":
    build()
