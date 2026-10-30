"""Noms de types renommés le 2026-10-20 (ADR 0041), modificateurs fondus dans un autre le 2026-10-28 (ADR 0042) :
les flux enregistrés avant sont migrés à la lecture.

Types, ports et paramètres : un flux ancien s'ouvre et s'exécute sans retouche ; il est réécrit avec les nouveaux
noms au prochain enregistrement.
"""

from __future__ import annotations

from typing import Any

TYPES: dict[str, str] = {
    "Aggregator": "Aggregate",
    "AreaCalculator": "MeasureArea",
    "AreaOnAreaOverlayer": "OverlayPolygons",
    "AttributeCreator": "AddAttributes",
    "AttributeKeeper": "RetainAttributes",
    "AttributeManager": "RefactorAttributes",
    "AttributePivoter": "PivotWider",
    "AttributeRemover": "DropAttributes",
    "AttributeRenamer": "RenameAttributes",
    "AttributeSplitter": "SplitText",
    "AttributeTypeConverter": "CastAttributes",
    "AttributeUnpivoter": "PivotLonger",
    "AttributeValueMapper": "MapValues",
    "Bufferer": "Buffer",
    "BulkAttributeRenamer": "RenameAttributesByPattern",
    "CenterPointReplacer": "Centroids",
    "Clipper": "Clip",
    "CoordinateExtractor": "ExtractCoordinates",
    "Creator": "CreateFeatures",
    "DateTimeConverter": "ConvertDate",
    "Deaggregator": "MultipartToSingleparts",
    "Dissolver": "Dissolve",
    "DuplicateFilter": "FindDuplicates",
    "FeatureJoiner": "JoinByField",
    "FeatureMerger": "LookupJoin",
    "FeatureReader": "ReadListedFiles",
    "FeatureWriter": "SplitToFiles",
    "FileLister": "ListFiles",
    "FlowRunner": "RunFlow",
    "GeometryExtractor": "GeometryToAttribute",
    "GeometryFilter": "SplitByGeometryType",
    "GeometryRepairer": "FixGeometries",
    "GeometryReplacer": "GeometryFromAttribute",
    "GeometryValidator": "CheckValidity",
    "GridGenerator": "CreateGrid",
    "HTTPCaller": "HttpRequest",
    "HullReplacer": "Hull",
    "InlineQuerier": "ExecuteSql",
    "JSONExtractor": "ExtractJson",
    "LengthCalculator": "MeasureLength",
    "ListBuilder": "BuildList",
    "ListExploder": "ExplodeList",
    "NeighborFinder": "JoinByNearest",
    "NullAttributeMapper": "ReplaceNulls",
    "PointInAreaCounter": "CountPointsInPolygon",
    "PointOnAreaOverlayer": "PointsInPolygons",
    "PythonCaller": "PythonScript",
    "RasterBandStacker": "StackBands",
    "RasterClipper": "ClipRasterByMask",
    "RasterContourGenerator": "Contour",
    "RasterMosaicker": "MergeRasters",
    "RasterPointSampler": "SampleRasterValues",
    "RasterPolygonizer": "Polygonize",
    "RasterReclassifier": "ReclassifyRaster",
    "RasterReprojector": "WarpRaster",
    "RasterResampler": "ResampleRaster",
    "Rasterizer": "Rasterize",
    "Reprojector": "Reproject",
    "SQLCreator": "DatabaseQuery",
    "SQLExecutor": "DatabaseQueryPerFeature",
    "Sampler": "Sample",
    "Simplifier": "Simplify",
    "Sorter": "OrderBy",
    "SpatialFilter": "ExtractByLocation",
    "SpatialRelator": "JoinByLocation",
    "StatisticsCalculator": "StatisticsByGroup",
    "StringExtractor": "ExtractText",
    "StringReplacer": "ReplaceText",
    "TerrainAnalyzer": "TerrainAnalysis",
    "TestFilter": "RouteByExpression",
    "Tester": "ExtractByExpression",
    "VariableSetter": "SetVariables",
    "VertexCreator": "PointFromXY",
}

# ports renommés, par nouveau type : ancien -> nouveau (entrées et sorties)
PORTS: dict[str, dict[str, str]] = {
    "Clip": {"clippee": "input", "clipper": "overlay"},
    "ClipRasterByMask": {"clipper": "mask"},
    "LookupJoin": {
        "requestor": "input",
        "supplier": "lookup",
        "Merged": "Joined",
        "NotMerged": "Unjoined",
        "UnusedSupplier": "UnusedLookup",
    },
    "Aggregate": {"Aggregate": "output"},
    "Dissolve": {"Dissolved": "output"},
    "OverlayPolygons": {"Area": "output"},
    "HttpRequest": {"Failed": "Rejected"},  # 2026-10-29 : une entité en erreur part sur Rejected, comme ailleurs
}

# paramètres renommés d'un type d'aujourd'hui (2026-10-29, ADR 0045) : ancien -> nouveau
PARAMS: dict[str, dict[str, str]] = {
    "MapValues": {"source": "attribute"},
    "GeometryToAttribute": {"remove": "remove_geometry"},
}


def _renamed(kind: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Paramètres d'un nœud avec les noms d'aujourd'hui (`PARAMS`) ; None s'il n'y a rien à changer."""
    names = PARAMS.get(kind, {})
    if not any(old in params and new not in params for old, new in names.items()):
        return None
    return {names.get(k, k) if names.get(k, k) not in params else k: v for k, v in params.items()}


def _params(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Paramètres renommés d'un nœud du type `kind` (nouveau nom)."""
    out = dict(params)
    if kind == "Clip":
        if "clipper_attributes" in out:
            out["overlay_attributes"] = out.pop("clipper_attributes")
        if out.get("prefix") == "clipper_":
            out["prefix"] = "overlay_"
    elif kind == "ClipRasterByMask" and "clipper_attributes" in out:
        out["mask_attributes"] = out.pop("clipper_attributes")
    elif kind == "LookupJoin":
        for old, new in (("requestor_keys", "input_keys"), ("supplier_keys", "lookup_keys")):
            if old in out:
                out[new] = out.pop(old)
        sides = {"requestor": "input", "supplier": "lookup"}
        for key in ("geometry", "conflict"):
            if out.get(key) in sides:
                out[key] = sides[out[key]]
    return out


# modificateurs fondus dans un autre : ports de l'ancien -> ports du modificateur d'accueil
MERGED_PORTS: dict[str, dict[str, str]] = {
    "LookupJoin": {"input": "base", "lookup": "join", "Unjoined": "UnjoinedBase", "UnusedLookup": "UnjoinedJoin"},
    "CountPointsInPolygon": {"areas": "base", "points": "related"},
    "CheckValidity": {"Valid": "output", "Invalid": "Rejected"},  # avec « Réparer » seulement
}


def _merged(kind: str, params: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, str]] | None:
    """(type d'accueil, paramètres, ports renommés) d'un nœud d'un modificateur fondu dans un autre, réglé pour donner
    le même résultat ; None pour les autres nœuds."""
    if kind == "LookupJoin":  # -> jointure attributaire, avec le choix de la géométrie
        out: dict[str, Any] = {
            "base_keys": params.get("input_keys", []),
            "join_keys": params.get("lookup_keys", []),
            "geometry": {"lookup": "join", "none": "none"}.get(params.get("geometry", ""), "base"),
            "conflict": "base" if params.get("conflict") == "input" else "other",
            "on_multiple": params.get("on_multiple", "first"),  # défaut de LookupJoin (« all » pour JoinByField)
        }
        if "attributes" in params:
            out["attributes"] = params["attributes"]
        return "JoinByField", out, MERGED_PORTS[kind]
    if kind == "CountPointsInPolygon":  # -> relateur spatial : polygones en « base », points en « related »
        out = {"predicate": "intersects", "count_attribute": params.get("count_attribute", "_count")}
        out.update({k: params[k] for k in ("sum_attributes", "on_multiple") if k in params})
        return "JoinByLocation", out, MERGED_PORTS[kind]
    if kind == "CheckValidity" and "repair" in params:  # « Réparer » : c'est le rôle de FixGeometries
        if params["repair"]:
            fix = {"method": "linework", "keep_type": False, "report_attribute": "_repaired_reason"}
            return "FixGeometries", fix, MERGED_PORTS[kind]
        return kind, {k: v for k, v in params.items() if k != "repair"}, {}
    return None


def migrate(data: Any) -> Any:
    """Flux (dictionnaire JSON) aux noms d'aujourd'hui ; rendu tel quel s'il n'y a rien à changer."""
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return data
    port_maps: dict[str, list[dict[str, str]]] = {}  # par nœud : renommages de ports, appliqués dans l'ordre
    nodes, changed = [], False
    for node in data["nodes"]:
        if isinstance(node, dict):
            if node.get("type") in TYPES:
                kind = TYPES[node["type"]]
                node = {**node, "type": kind, "params": _params(kind, node.get("params") or {})}
                changed = True
            renamed = _renamed(str(node.get("type")), node.get("params") or {})
            if renamed is not None:
                node, changed = {**node, "params": renamed}, True
            maps = [PORTS.get(str(node.get("type")), {})]
            merged = _merged(str(node.get("type")), node.get("params") or {})
            if merged is not None:
                kind, params, ports = merged
                node = {**node, "type": kind, "params": params}
                maps.append(ports)
                changed = True
            port_maps[str(node.get("id"))] = maps
        nodes.append(node)
    links = []
    for link in data.get("links") or []:
        if isinstance(link, dict):
            for end in ("from", "to"):
                ref = link.get(end)
                if isinstance(ref, list | tuple) and len(ref) == 2:
                    port = ref[1]
                    for ports in port_maps.get(str(ref[0]), []):
                        port = ports.get(port, port)
                    if port != ref[1]:
                        link = {**link, end: [ref[0], port]}
                        changed = True
        links.append(link)
    subflows = data.get("subflows")
    if isinstance(subflows, dict) and subflows:
        migrated = {name: migrate(sub) for name, sub in subflows.items()}
        if any(migrated[n] is not subflows[n] for n in subflows):
            subflows, changed = migrated, True
    if not changed:
        return data
    return {**data, "nodes": nodes, "links": links, **({"subflows": subflows} if "subflows" in data else {})}
