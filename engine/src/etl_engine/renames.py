"""Noms de types renommés le 2026-10-20 (ADR 0041) : les flux enregistrés avant sont migrés à la lecture.

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
}


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


def migrate(data: Any) -> Any:
    """Flux (dictionnaire JSON) aux noms d'aujourd'hui ; rendu tel quel s'il n'y a rien à changer."""
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return data
    kinds: dict[str, str] = {}
    nodes, changed = [], False
    for node in data["nodes"]:
        if isinstance(node, dict) and node.get("type") in TYPES:
            kind = TYPES[node["type"]]
            node = {**node, "type": kind, "params": _params(kind, node.get("params") or {})}
            changed = True
        if isinstance(node, dict):
            kinds[str(node.get("id"))] = str(node.get("type"))
        nodes.append(node)
    links = []
    for link in data.get("links") or []:
        if isinstance(link, dict):
            for end in ("from", "to"):
                ref = link.get(end)
                if isinstance(ref, list | tuple) and len(ref) == 2:
                    port = PORTS.get(kinds.get(str(ref[0]), ""), {}).get(ref[1])
                    if port:
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
