"""Build the "CR Bevel" Geometry Nodes group (constant-radius bevel).

Usage inside Blender (headless or UI):
    import build_cr_bevel; ng = build_cr_bevel.build()

Phase 1 scope: exact cylindrical edge strips (convex and concave). Corners
(vertex patches) are left as the native Mesh Bevel node makes them; Phase 2
adds them. The math is documented in docs/PLAN.md.

Internal attributes use the prefix "_cr_" and are removed before output.
"""
import math

import bpy

GROUP_NAME = "CR Bevel"
MAX_EDGE_ANGLE = math.radians(179.0)  # tan(phi/2) explodes beyond this


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
    """debug_output="skeleton" returns the helper skeleton instead of the mesh (tests only)."""
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
    radius = gi.outputs["Radius"]

    # ---- per-edge quantities on the original mesh ------------------------
    B.stage("1. Per-edge offset d = r tan(phi/2) and axis offset")
    ang = B.node("GeometryNodeInputMeshEdgeAngle")
    # Measured in 5.2.4: convex > 0, concave < 0 (the manual states the opposite).
    sigma = ang.outputs["Signed Angle"]
    phi = ang.outputs["Unsigned Angle"]
    half = B.math("MULTIPLY", phi, 0.5)
    d = B.math("MULTIPLY", radius, B.math("TANGENT", half))
    # Axis offset from the edge: -sign(sigma) * r / cos(phi/2) * bisector of the
    # outward normals, i.e. into the material for convex edges, out of it for concave.
    k = B.math("MULTIPLY", B.math("MULTIPLY", B.math("SIGN", sigma), -1.0),
               B.math("DIVIDE", radius, B.math("COSINE", half)))
    bis = B.vmath("NORMALIZE", B.node("GeometryNodeInputNormal").outputs["Normal"])
    axis_off = B.vmath("SCALE", bis, scale=k)

    valid = B.bool_and(
        gi.outputs["Selection"],
        B.compare("GREATER_THAN", phi, gi.outputs["Angle Limit"]),
        B.compare("GREATER_THAN", phi, 1e-4),
        B.compare("LESS_THAN", phi, MAX_EDGE_ANGLE),
        B.compare("GREATER_THAN", radius, 0.0),
    )
    ev = B.node("GeometryNodeInputMeshEdgeVertices")

    B.stage("2. Store per-element data (_cr_* attributes)")
    g = gi.outputs["Geometry"]
    g = B.store(g, "_cr_vid", B.node("GeometryNodeInputIndex").outputs["Index"], "INT", "POINT")
    g = B.store(g, "_cr_sel", valid, "BOOLEAN", "EDGE")
    g = B.store(g, "_cr_d", d, "FLOAT", "EDGE")
    g = B.store(g, "_cr_off", axis_off, "FLOAT_VECTOR", "EDGE")
    g = B.store(g, "_cr_va", ev.outputs["Vertex Index 1"], "INT", "EDGE")
    g = B.store(g, "_cr_vb", ev.outputs["Vertex Index 2"], "INT", "EDGE")
    prepared = g

    # ---- skeleton: one axis segment per selected edge, two copies --------
    # Copy 0 is grouped under the edge's first vertex, copy 1 under the second,
    # so a bevel vertex (which keeps its original vertex id) only searches the
    # axes of edges at its own original vertex.
    B.stage("3. Skeleton: cylinder axes, grouped by end vertex")
    dup = B.node("GeometryNodeDuplicateElements", domain="EDGE")
    B.link(prepared, dup.inputs["Geometry"])
    B.link(B.named("_cr_sel", "BOOLEAN"), dup.inputs["Selection"])
    dup.inputs["Amount"].default_value = 2
    sp = B.node("GeometryNodeSetPosition")
    B.link(dup.outputs["Geometry"], sp.inputs["Geometry"])
    B.link(B.named("_cr_off", "FLOAT_VECTOR"), sp.inputs["Offset"])
    sw = B.node("GeometryNodeSwitch", input_type="INT")
    B.link(B.compare("EQUAL", dup.outputs["Duplicate Index"], 0, data_type="INT"), sw.inputs["Switch"])
    B.link(B.named("_cr_vb", "INT"), sw.inputs["False"])
    B.link(B.named("_cr_va", "INT"), sw.inputs["True"])
    skeleton = B.store(sp.outputs["Geometry"], "_cr_grp", sw.outputs["Output"], "INT", "EDGE")

    # ---- native bevel with constant-radius offsets -----------------------
    B.stage("4. Native Mesh Bevel with constant-radius offsets")
    bev = B.node("GeometryNodeMeshBevel")
    B.link(prepared, bev.inputs["Mesh"])
    B.link(B.named("_cr_sel", "BOOLEAN"), bev.inputs["Selection"])
    d_attr = B.named("_cr_d", "FLOAT")
    for s in ("Start Left Offset", "Start Right Offset", "End Left Offset", "End Right Offset"):
        B.link(d_attr, bev.inputs[s])
    B.link(gi.outputs["Segments"], bev.inputs["Segments"])
    bev.inputs["Shape"].default_value = 0.5
    g = B.store(bev.outputs["Mesh"], "_cr_ef", bev.outputs["Edge Face"], "BOOLEAN", "FACE")
    g = B.store(g, "cr_bevel_face", B.bool_or(bev.outputs["Edge Face"], bev.outputs["Vertex Face"]), "BOOLEAN", "FACE")

    # ---- snap strip vertices onto their cylinder -------------------------
    B.stage("5. Snap strip vertices onto their cylinder")
    prox = B.node("GeometryNodeProximity", target_element="EDGES")
    B.link(skeleton, prox.inputs["Geometry"])
    B.link(B.named("_cr_grp", "INT"), prox.inputs["Group ID"])
    B.link(B.named("_cr_vid", "INT"), prox.inputs["Sample Group ID"])
    pos = B.node("GeometryNodeInputPosition").outputs["Position"]
    B.link(pos, prox.inputs["Sample Position"])
    q = prox.outputs["Position"]
    rel = B.vmath("SUBTRACT", pos, q)
    target = B.vmath("ADD", q, B.vmath("SCALE", B.vmath("NORMALIZE", rel), scale=radius))
    snap_sel = B.bool_and(
        gi.outputs["Exact Profile"],
        B.named("_cr_ef", "BOOLEAN"),
        prox.outputs["Is Valid"],
        B.compare("GREATER_THAN", prox.outputs["Distance"], 1e-9),
    )
    sp2 = B.node("GeometryNodeSetPosition")
    B.link(g, sp2.inputs["Geometry"])
    B.link(snap_sel, sp2.inputs["Selection"])
    B.link(target, sp2.inputs["Position"])

    # ---- tidy up -----------------------------------------------------------
    B.stage("6. Remove internal attributes")
    rm = B.node("GeometryNodeRemoveAttribute")
    B.link(sp2.outputs["Geometry"], rm.inputs["Geometry"])
    rm.inputs["Pattern Mode"].default_value = "Wildcard"
    rm.inputs["Name"].default_value = "_cr_*"
    go = B.node("NodeGroupOutput")
    go.parent = None
    go.location = (B.x0 + 2 * B.COL_W + B.STAGE_GAP, 0)
    final = skeleton if debug_output == "skeleton" else rm.outputs["Geometry"]
    B.link(final, go.inputs["Geometry"])
    return ng


if __name__ == "__main__":
    build()
