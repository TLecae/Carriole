"""Exécution d'un flux : ordre topologique, branches indépendantes en parallèle, nœuds en flux, cache par port.

Un nœud à une seule liaison entrante est « branché en direct » sur son amont (ADR 0013) : il démarre en même temps
que lui et reçoit ses lots au fil de l'eau (`cache.Tap`), au lieu d'attendre qu'il ait tout écrit puis de relire le
fichier. Les ports restent écrits (cache, inspecteur), sauf dans un run sans cache quand tous les consommateurs d'un
port sont branchés en direct.

Ressources : le run reçoit un budget (mémoire, cœurs : sa part de la machine quand plusieurs runs tournent) qui
dimensionne les threads et les connexions DuckDB ; les nœuds lourds (bloquants) ne s'exécutent que quelques-uns à
la fois ; un garde-fou arrête le run proprement, avec un message, quand la mémoire du poste est épuisée.

Événements envoyés à `on_event` (dictionnaires JSON, `ts` = horodatage) :

- `run_started`  : nodes (ordre d'exécution)
- `node`         : node, status (running | done | cached | error | cancelled), seconds, rows, rows_in, rss_mb, error
- `counts`       : node, port, count — nombre d'entités passées sur le port (mis à jour pendant le run)
- `log`          : node, level, message
- `run_finished` : status (done | error | cancelled), error, seconds
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import traceback
import uuid
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil
import pyarrow.parquet as pq
from pydantic import ValidationError

from etl_engine import duck, i18n
from etl_engine.analysis import compute_schemas
from etl_engine.cache import (
    PortSink,
    PortSource,
    PortStream,
    Tap,
    Throttle,
    define,
    link_port,
    port_data,
    port_path,
    port_source,
    read_meta,
    rebase_rasters,
    refer,
    reuse_files,
    write_meta,
)
from etl_engine.components import expand_components
from etl_engine.data import DEFAULT_BATCH_ROWS, GEOM, get_crs, with_crs
from etl_engine.expr import deterministic
from etl_engine.flow import Flow, expand_late, resolve_params, system_values
from etl_engine.plan import Plan, build_plan
from etl_engine.registry import Registry
from etl_engine.sdk import Cancelled, ConfigError, Context, Modifier, expressions
from etl_engine.spatial import is_out_of_memory
from etl_engine.sqlroute import Source, SqlRouteResult, has_sql_route, row_wise, run_sql_route

EventSink = Callable[[dict[str, Any]], None]


def _common_crs(sources: list[Source]) -> str | None:
    """Le SCR des ports d'entrée, s'ils n'en portent qu'un : celui d'une sortie dont la géométrie l'aurait perdu (un
    modificateur, un plugin qui reconstruit sa table sans les métadonnées)."""
    found = {s.crs if not isinstance(s, Path) else get_crs(pq.read_schema(s)) for s in sources}
    found.discard(None)
    return found.pop() if len(found) == 1 else None


def _out_of_memory(names: str | None = None, gb: float | None = None) -> MemoryError:
    """Message pour l'utilisateur quand la mémoire manque (nœud en erreur ou garde-fou du run)."""
    if names is None:
        return MemoryError(
            "mémoire insuffisante, réduire ou filtrer les données en amont, ou augmenter le budget mémoire dans "
            "Paramètres"
        )
    return MemoryError(
        f"mémoire insuffisante pendant « {names} » ({gb} Go utilisés) : réduire ou filtrer les données en amont, ou "
        "augmenter le budget mémoire dans Paramètres"
    )


@dataclass
class NodeResult:
    id: str
    status: str = "pending"  # pending | running | done | cached | error | cancelled | disabled | not_run
    seconds: float = 0.0
    rows: dict[str, int] = field(default_factory=dict)  # entités écrites par port de sortie
    rows_in: dict[str, int] = field(default_factory=dict)  # entités lues par port d'entrée
    rss_mb: float | None = None
    error: str | None = None


@dataclass
class RunResult:
    status: str  # done | error | cancelled
    nodes: dict[str, NodeResult]
    seconds: float = 0.0
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "seconds": round(self.seconds, 3),
            "error": self.error,
            "nodes": {k: vars(v) for k, v in self.nodes.items()},
        }


class _RssSampler:
    """Mémoire résidente du processus échantillonnée en continu : pic par nœud, et garde-fou (`watch`)."""

    def __init__(self, interval: float = 0.1, watch: Callable[[float], None] | None = None) -> None:
        self._proc = psutil.Process()
        self._samples: list[tuple[float, float]] = []
        self._stop = threading.Event()
        self._interval = interval
        self._watch = watch
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._sample()
        self._thread.start()

    def _sample(self) -> float:
        rss = self._proc.memory_info().rss / 2**20
        self._samples.append((time.monotonic(), rss))
        return rss

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            rss = self._sample()
            if self._watch is not None:
                self._watch(rss)

    def stop(self) -> None:
        self._stop.set()

    def peak(self, t0: float, t1: float) -> float:
        self._sample()
        window = [v for t, v in self._samples if t0 - self._interval <= t <= t1 + self._interval]
        return round(max(window or [0.0]), 1)


def fingerprint(node_type: str, inst: Any, upstream: list[tuple[str, str, str]], extra: str | None = None) -> str:
    payload = {
        "type": node_type,
        "version": inst.version,
        "params": inst.params.model_dump(mode="json"),
        "extra": inst.fingerprint_extra() if extra is None else extra,
        "upstream": sorted(upstream),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]


def reusable_kind(inst: Any, partial: bool = False) -> bool:
    """Un nœud peut-il être repris d'un run précédent ? Jamais une écriture (sans sortie). Une source externe
    (`reusable = "partial"` : base, API…) seulement dans un run « jusqu'au nœud » (`partial`)."""
    cls = type(inst)
    kind = getattr(cls, "reusable", True)
    return bool(cls.outputs) and (kind is True or (partial and kind == "partial"))


def _describe_params(exc: ValidationError) -> str:
    return " ; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'paramètres'} : {e['msg']}" for e in exc.errors()[:3])


def _publish_aliases(
    run_dir: Path, aliases: dict[tuple[str, str], tuple[str, str]], event: Callable[..., None]
) -> None:
    """Rend les ports d'un nœud composant consultables : lien vers le fichier (ou la référence) du port interne qui
    les alimente."""
    published: dict[str, dict[str, int]] = defaultdict(dict)
    for (outer, port), (inner, inner_port) in aliases.items():
        if not link_port(port_path(run_dir, inner, inner_port), port_path(run_dir, outer, port)):
            continue
        rows = (read_meta(run_dir / inner) or {}).get("rows", {}).get(inner_port, 0)
        published[outer][port] = rows
        event(type="counts", node=outer, port=port, count=rows)
    for outer, rows in published.items():
        write_meta(run_dir / outer, {"fingerprint": "composant", "status": "done", "rows": rows, "rows_in": {}})


def run_flow(
    flow: Flow,
    registry: Registry,
    run_dir: str | Path,
    *,
    params: dict[str, str] | None = None,
    cache: bool = True,
    reuse_from: str | Path | None = None,
    target: str | None = None,
    on_event: EventSink | None = None,
    cancel: threading.Event | None = None,
    max_workers: int | None = None,
    timeout: float | None = None,
    batch_rows: int = DEFAULT_BATCH_ROWS,
    batch_workers: int | None = None,
    sql_route: bool | None = None,
    lang: str | None = None,
    flow_path: str | Path | None = None,
    run_id: str | None = None,
    user: str | None = None,
    memory_mb: int | None = None,
    threads: int | None = None,
    pipeline: bool | None = None,
) -> RunResult:
    """Exécute `flow`. Les données de chaque port sont écrites sous `run_dir`.

    - `cache` : conserve les ports (réglable par nœud) ; sinon un port est supprimé dès que ses consommateurs ont fini.
    - `reuse_from` : run précédent ; un nœud dont l'empreinte (paramètres + amont) est inchangée n'est pas recalculé.
    - `target` : n'exécute que ce nœud et ses ancêtres (« lancer jusqu'à un nœud »).
    - `flow_path`, `run_id`, `user` : valeurs de `${sys:flow_dir}`, `${sys:run_id}`, `${sys:user}` (`run_id` : le nom du
      dossier du run par défaut).
    - `lang` : langue des messages (journal, erreurs) ; par défaut le réglage de l'utilisateur. Le code est en français.
    - `memory_mb`, `threads` : part de la machine accordée au run (défaut : `ETL_RUN_MEMORY_MB` / `ETL_RUN_THREADS`
      posés par le worker, sinon toute la machine) ; dimensionne les threads, DuckDB et le garde-fou mémoire.
    - `pipeline` : branchement en direct des nœuds à une entrée (défaut : `ETL_PIPELINE`, activé).
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    cancel = cancel or threading.Event()
    lock = threading.Lock()
    started = time.perf_counter()
    duck.configure_run(memory_mb, threads, run_dir / ".spill")
    run_threads = duck.run_threads()

    lang = i18n.normalize(lang)

    def loc(text: str | None) -> str | None:
        return i18n.tr(text, lang) if text else text

    def event(**kw: Any) -> None:
        if on_event:
            for key in ("message", "error"):
                if isinstance(kw.get(key), str):
                    kw[key] = loc(kw[key])
            with lock:
                on_event({"ts": time.time(), **kw})

    try:
        flow, aliases = expand_components(flow)  # les composants sont remplacés par leur contenu
    except ConfigError as exc:
        res = RunResult("error", {}, time.perf_counter() - started, loc(str(exc)))
        event(type="run_finished", status="error", error=res.error, seconds=0)
        return res
    results = {n.id: NodeResult(n.id, "disabled" if not n.enabled else "not_run") for n in flow.nodes}

    def finish(status: str, error: str | None) -> RunResult:
        error = loc(error)
        for node_result in results.values():
            node_result.error = loc(node_result.error)
        res = RunResult(status, results, time.perf_counter() - started, error)
        event(type="run_finished", status=status, error=error, seconds=round(res.seconds, 3))
        return res

    try:
        values = resolve_params(flow, params, system_values(flow_path, run_id or run_dir.name, user))
    except ValueError as exc:
        return finish("error", str(exc))

    run_vars: dict[str, str] = {}  # variables posées en cours de run (SetVariables), lues par ${var:NOM}

    def ctx_for(node_id: str) -> Context:
        return Context(
            node_id=node_id,
            variables=run_vars,
            work_dir=run_dir / node_id / "rasters",
            params=values,
            cancel=cancel,
            batch_rows=batch_rows,
            workers=batch_workers if batch_workers is not None else duck.batch_workers(run_threads),
            log_sink=lambda n, level, msg: event(type="log", node=n, level=level, message=msg),
        )

    plan: Plan = build_plan(flow, registry, values, ctx_for)
    if plan.errors:
        return finish("error", "; ".join(f"{i.node or '-'} : {i.message}" for i in plan.errors))
    schemas, issues = compute_schemas(plan)
    config = [i for i in issues if i.level == "error"]
    if config:
        for i in config:
            results[i.node].status, results[i.node].error = "error", i.message
        return finish("error", "; ".join(f"{i.node} : {i.message}" for i in config))

    if target is not None:  # « jusqu'au nœud » sur un composant : jusqu'au nœud interne qui alimente sa sortie
        target = next((inner for (outer, _), (inner, _) in aliases.items() if outer == target), target)
    selected = [n for n in plan.order if target is None or n == target or n in plan.upstream(target)]
    if target is not None and target not in plan.nodes:
        return finish("error", f"nœud cible introuvable ou désactivé : {target}")
    for nid in selected:
        results[nid].status = "pending"

    # empreintes de tous les nœuds, dans l'ordre topologique (celle d'un nœud dépend de celles de son amont) : un run
    # « jusqu'au nœud » reprend aussi, sans les exécuter, les nœuds hors de sa branche encore valables
    partial = target is not None
    effective_cache = {nid: cache for nid in plan.order}  # réglage global du run
    prints: dict[str, str] = {}
    # empreinte « statique » (réglages et amont, jamais salée) : un run « jusqu'au nœud » garde les derniers résultats
    # des nœuds hors de sa branche dont rien n'a changé, même ceux qu'un run complet relancerait
    statics: dict[str, str] = {}
    fresh = uuid.uuid4().hex  # empreinte propre à ce run : un nœud toujours relancé invalide aussi son aval
    for nid in plan.order:
        inst = plan.nodes[nid]
        extra = inst.fingerprint_extra()  # une fois : celle d'une adresse web interroge le serveur
        links = plan.inbound.get(nid, [])
        prints[nid] = fingerprint(
            flow.node(nid).type, inst, [(ln.to[1], prints[ln.from_[0]], ln.from_[1]) for ln in links], extra
        )
        statics[nid] = fingerprint(
            flow.node(nid).type, inst, [(ln.to[1], statics[ln.from_[0]], ln.from_[1]) for ln in links], extra
        )
        if reusable_kind(inst):
            continue
        before = read_meta(Path(reuse_from) / nid) if (reuse_from and cache) else None
        if reusable_kind(inst, partial) and before and str(before.get("fingerprint", ""))[:16] == prints[nid][:16]:
            prints[nid] = before["fingerprint"]  # source inchangée, reprise : son aval garde aussi ses empreintes
        else:
            prints[nid] = f"{prints[nid][:16]}{fresh[:16]}"

    deps = {nid: {link.from_[0] for link in plan.inbound.get(nid, [])} & set(selected) for nid in selected}
    consumers: dict[str, int] = defaultdict(int)
    for nid in selected:
        for up in deps[nid]:
            consumers[up] += 1
    has_consumers = {nid for nid, n in consumers.items() if n}

    use_sql = sql_route if sql_route is not None else os.environ.get("ETL_SQL_ROUTE", "1") not in ("0", "false")

    # un port sans fichier (run sans cache, pas la sortie d'un composant) : tous ses consommateurs en direct, ou aucun
    # sur un nœud dont d'autres ports sont lus (« Failed » d'un filtre non branché : compté, pas écrit). Un nœud sans
    # aucun consommateur écrit tous ses ports : ce sont les résultats du run, consultables.
    alias_sources = set(aliases.values())
    port_links: dict[tuple[str, str], list[str]] = defaultdict(list)
    for nid in selected:
        for link in plan.inbound.get(nid, []):
            port_links[(link.from_[0], link.from_[1])].append(nid)

    def is_read(nid: str, port: str) -> bool:
        return bool(port_links.get((nid, port))) or (nid, port) in alias_sources

    def previous_result(nid: str, outside: bool = False) -> dict | None:
        """Résultat de `nid` dans le run précédent, s'il peut passer dans ce run : même empreinte ; pour un nœud hors
        de la branche d'un run « jusqu'au nœud » (`outside`), même empreinte statique suffit (ses derniers résultats
        restent valables, même s'il est de ceux qu'un run complet relance)."""
        previous = read_meta(Path(reuse_from) / nid) if (reuse_from and effective_cache[nid]) else None
        if not (previous and previous.get("status") == "done"):
            return None
        same = previous.get("fingerprint") == prints[nid] or (outside and previous.get("static") == statics[nid])
        if not same:
            return None
        # ses données doivent être encore là (run sans cache, cache purgé : le nœud est recalculé)
        folder = Path(reuse_from)  # type: ignore[arg-type]
        try:
            ports = [port for port, n in previous.get("rows", {}).items() if n]
            kept = all(port_source(folder, nid, port) is not None for port in ports)
        except OSError:  # fichier désigné modifié, port amont d'une définition disparu : le nœud est recalculé
            kept = False
        if kept and not outside and not row_wise(plan.nodes[nid]):
            # un port d'un nœud qui doit tout charger (statistiques, tri, jointure), seulement défini parce que rien
            # ne le lisait, et lu dans ce run : il est recalculé pour être écrit une fois (`written`), au lieu que
            # chaque lecture refasse tout le calcul (ADR 0044)
            kept = not any(is_read(nid, port) and port_data(port_path(folder, nid, port)) is None for port in ports)
        return previous if kept else None

    previous_of = {nid: previous_result(nid) for nid in selected}

    # --- branchement en direct : un nœud à une seule liaison entrante démarre avec son amont
    use_pipeline = pipeline if pipeline is not None else os.environ.get("ETL_PIPELINE", "1") not in ("0", "false")

    # voies directes (SQL, port qui désigne un fichier) : le port est écrit d'un coup, ou pas du tout ; jamais en direct
    def has_direct_route(inst: Modifier) -> bool:
        return has_sql_route(inst) or type(inst).ready_ports is not Modifier.ready_ports

    direct = {nid: use_sql and has_direct_route(plan.nodes[nid]) for nid in selected}
    # la part de DuckDB n'est partagée qu'entre les nœuds lourds de ce run (un seul : toute la part)
    duck.configure_heavy_nodes(
        sum(1 for n in selected if not previous_of[n] and (direct[n] or plan.nodes[n].mode == "blocking"))
    )

    def pipelinable(nid: str) -> bool:
        links = plan.inbound.get(nid, [])
        if not use_pipeline or len(links) != 1 or nid in plan.late:
            return False
        up = links[0].from_[0]
        return up in deps[nid] and not previous_of[nid] and not previous_of[up] and not direct[nid] and not direct[up]

    live = {nid for nid in selected if pipelinable(nid)}
    live_children: dict[str, list[str]] = defaultdict(list)
    for nid in selected:
        if nid in live:
            live_children[plan.inbound[nid][0].from_[0]].append(nid)
    taps: dict[tuple[str, str], list[Tap]] = defaultdict(list)
    streams: dict[str, tuple[str, Tap]] = {}  # nœud en direct -> (port d'entrée, file)

    # nœud différé -> nœuds de l'amont que ses définitions désignent (gardés tant qu'elles le sont, run sans cache)
    defined_from: dict[str, set[str]] = {}
    refs_lock = threading.Lock()

    def store(nid: str, port: str) -> bool:
        readers = port_links.get((nid, port), [])
        if effective_cache[nid] or (nid, port) in alias_sources:
            return True
        return any(r not in live for r in readers) if readers else nid not in has_consumers

    def written(nid: str, port: str, definable: bool) -> bool:
        """Voie SQL : le port est écrit ; sinon défini par sa requête (`definable`, ADR 0044) ou seulement compté. Un
        port défini est recalculé par chaque nœud qui le lit : c'est le cas de tous ceux d'un nœud ligne à ligne, ce
        qui ne coûte presque rien. Celui d'un nœud qui doit tout charger (tri, statistiques, jointure) est écrit dès
        qu'un nœud le lit, défini sinon : il n'est calculé que si on l'ouvre dans l'inspecteur."""
        if not definable:
            return store(nid, port)
        return is_read(nid, port) and not row_wise(plan.nodes[nid])

    # --- ressources : nœuds lourds limités, garde-fou mémoire
    heavy = threading.BoundedSemaphore(duck.heavy_slots())
    share_mb = duck.run_memory_mb()
    state: dict[str, Any] = {"error": None, "timed_out": False, "oom": None}
    active: set[str] = set()  # nœuds en cours
    active_lock = threading.Lock()

    def guard(rss: float) -> None:
        if state["oom"] is not None or cancel.is_set():
            return
        vm = psutil.virtual_memory()
        if rss > share_mb and vm.available < max(vm.total * 0.05, 512 * 2**20):
            with active_lock:
                state["oom"] = (rss, sorted(active))
            cancel.set()

    sampler = _RssSampler(watch=guard)
    sampler.start()
    if timeout:
        timer = threading.Timer(timeout, lambda: (state.update(timed_out=True), cancel.set()))
        timer.daemon = True
        timer.start()
    event(type="run_started", nodes=selected)

    def adopt(nid: str, previous: dict) -> bool:
        """Le nœud passe dans ce run avec les fichiers du run précédent (liens, pas copies), état « cached ». Faux si
        ses fichiers n'ont pas pu être repris : il est alors recalculé (ou, hors de la branche, laissé de côté)."""
        old_dir = Path(reuse_from) / nid  # type: ignore[arg-type]
        meta = {**previous, "static": statics[nid]}
        try:
            reuse_files(old_dir, run_dir / nid)
            rebase_rasters(run_dir / nid, Path(reuse_from), run_dir)  # type: ignore[arg-type]
            write_meta(run_dir / nid, meta)
            for port, count in meta["rows"].items():  # une définition reprise désigne des ports de ce run
                if count and port_source(run_dir, nid, port) is None:
                    raise FileNotFoundError(f"données de {nid}/{port} introuvables")
        except OSError as exc:
            event(type="log", node=nid, level="WARNING", message=f"cache non repris, recalcul : {exc}")
            shutil.rmtree(run_dir / nid, ignore_errors=True)
            return False
        res = results[nid]
        res.rows, res.rows_in, res.status = meta["rows"], meta.get("rows_in", {}), "cached"
        for port, count in res.rows.items():
            event(type="counts", node=nid, port=port, count=count)
        event(type="node", node=nid, status="cached", seconds=0, rows=res.rows, rows_in=res.rows_in)
        return True

    def run_node(nid: str) -> None:
        inst = plan.nodes[nid]
        res = results[nid]
        res.status = "running"
        event(type="node", node=nid, status="running")
        t_wall, t_mono = time.perf_counter(), time.monotonic()
        ndir = run_dir / nid
        ins, outs = inst.ports(inst.params)
        sources: dict[str, PortSource | PortStream] = {}
        sinks: dict[str, PortSink] = {}
        ok = False
        holding = False
        try:
            ndir.mkdir(parents=True, exist_ok=True)
            if (
                nid in plan.late
            ):  # les variables posées en amont ont leur valeur : les paramètres sont validés maintenant
                try:
                    inst.params = type(inst).Params.model_validate(expand_late(plan.late[nid], run_vars))
                except ValidationError as exc:
                    raise ConfigError(_describe_params(exc)) from exc
                except ValueError as exc:
                    raise ConfigError(str(exc)) from exc
            inherited: str | None = None  # SCR des ports d'entrée, pour une sortie qui l'aurait perdu
            if nid in streams:
                port, tap = streams[nid]
                sources[port] = PortStream(tap)
            else:
                # les ports de l'amont : leur fichier, ou la requête qui les lit (port défini à la demande)
                inputs: dict[str, list[Source]] = {port: [] for port in ins}
                upstream: dict[str, list[tuple[str, str]]] = {port: [] for port in ins}
                for link in plan.inbound.get(nid, []):
                    found = port_source(run_dir, *link.from_)
                    if found is not None:
                        inputs[link.to[1]].append(found)
                        upstream[link.to[1]].append((link.from_[0], link.from_[1]))
                rows_in = {port: sum(results[u].rows.get(p, 0) for u, p in ups) for port, ups in upstream.items()}
                inherited = _common_crs([s for parts in inputs.values() for s in parts])
                out_schemas = {port: schemas.get((nid, port)) for port in outs}
                in_schemas = {
                    link.to[1]: schemas.get((link.from_[0], link.from_[1])) for link in plan.inbound.get(nid, [])
                }
                spill = ndir / ".spill"
                route, routed = "", None
                ready = inst.ready_ports() if direct[nid] else None
                if ready is not None:  # le port désigne un fichier déjà au format des ports : rien à recopier
                    route = "reference"
                    routed = SqlRouteResult(
                        {port: refer(port_path(run_dir, nid, port), path) for port, path in ready.items()}
                    )
                elif direct[nid]:
                    # un port défini désigne des ports de l'amont (pas un lecteur) et doit donner les mêmes lignes à
                    # chaque lecture : une expression non déterministe (`random()`…) l'empêche ; un nœud ligne à ligne
                    # passe alors par la voie par lots, qui fait foi pour lui
                    definable = bool(ins) and all(deterministic(e) for e in expressions(inst.params))
                    if definable or not row_wise(inst):
                        paths = {port: port_path(run_dir, nid, port) if written(nid, port, definable) else None
                                 for port in outs}  # fmt: skip
                        routed = run_sql_route(
                            inst, inputs, paths, out_schemas, in_schemas, spill, cancel, define=definable
                        )
                        everything = routed is not None and routed.queries and not any(paths.values())
                        route = "deferred" if everything else "sql"
                    if routed is not None and routed.queries:  # les ports non écrits gardent leur requête
                        named = {routed.relations[port]: ups for port, ups in upstream.items()}
                        for port, query in routed.queries.items():
                            schema = out_schemas.get(port)
                            crs = get_crs(schema) if schema else None
                            if not crs and schema is not None and GEOM in schema.names:
                                crs = inherited
                            define(port_path(run_dir, nid, port), query, named, crs)
                        with refs_lock:
                            defined_from[nid] = {u for ups in upstream.values() for u, _ in ups}
                if routed is not None:
                    res.rows, res.rows_in = routed.rows, rows_in
                    res.status = "done"
                    res.seconds = round(time.perf_counter() - t_wall, 3)
                    res.rss_mb = sampler.peak(t_mono, time.monotonic())
                    write_meta(
                        ndir,
                        {
                            "fingerprint": prints[nid],
                            "static": statics[nid],
                            "status": "done",
                            "rows": res.rows,
                            "rows_in": res.rows_in,
                            "seconds": res.seconds,
                            "route": route,
                        },
                    )
                    for port, count in res.rows.items():
                        event(type="counts", node=nid, port=port, count=count)
                    if route == "sql":
                        event(type="log", node=nid, level="INFO", message="exécuté par DuckDB (voie SQL)")
                        if routed.queries:
                            ports = ", ".join(routed.queries)
                            event(type="log", node=nid, level="INFO", message=f"calculés à la demande : {ports}")
                    elif route == "deferred":
                        event(
                            type="log", node=nid, level="INFO", message="défini par une requête, calculé à la demande"
                        )
                    else:
                        event(type="log", node=nid, level="INFO", message="fichier lu sur place, sans copie")
                    if not ins:  # lecture : le nombre d'entités lues, comme par la voie Python
                        total = sum(res.rows.values())
                        event(type="log", node=nid, level="INFO", message=f"{total} entités lues")
                    return
                for port, parts in inputs.items():
                    sources[port] = PortSource(parts, batch_rows, cancel, total=rows_in[port])
            throttles = {port: Throttle() for port in outs}
            for port in outs:

                def on_rows(count: int, port: str = port) -> None:
                    if throttles[port].ready():
                        event(type="counts", node=nid, port=port, count=count)

                sinks[port] = PortSink(
                    port_path(run_dir, nid, port),
                    schemas.get((nid, port)),
                    on_rows,
                    taps=taps.get((nid, port)),
                    store=store(nid, port),
                )

            # géométrie sans SCR (table reconstruite sans ses métadonnées) : celui déclaré, sinon celui des entrées
            fallback = {
                port: (get_crs(declared) if (declared := schemas.get((nid, port))) else None) or inherited
                for port in outs
            }

            def emit(port: str, table: Any) -> None:
                sink = sinks.get(port)
                if sink is None:
                    raise ConfigError(f"{inst.name} : port de sortie inconnu « {port} »")
                if fallback.get(port) and GEOM in table.column_names and not get_crs(table):
                    table = with_crs(table, fallback[port])
                sink.write(table)

            if inst.mode == "blocking" and nid not in live:
                # nœud qui charge tout : quelques-uns à la fois (un nœud en direct suit son amont, il n'attend pas)
                while not heavy.acquire(timeout=0.2):
                    if cancel.is_set():
                        raise Cancelled
                holding = True
            inst.run(sources, emit)
            for sink in sinks.values():
                sink.finish()  # fin de l'écriture des fichiers : une erreur (disque plein…) est celle du nœud
            ok = True
        except Cancelled:
            res.status = "cancelled"
        except ConfigError as exc:
            res.status, res.error = "error", f"configuration : {exc}"
        except MemoryError:
            res.status, res.error = "error", str(_out_of_memory())
            event(type="log", node=nid, level="ERROR", message=traceback.format_exc(limit=6))
        except Exception as exc:  # noqa: BLE001 - toute erreur de modificateur est rapportée, pas propagée
            # GEOS signale un manque de mémoire par une exception ordinaire (« bad allocation ») ; DuckDB, Arrow ou
            # Python, un disque plein par une erreur d'écriture (débordement, fichier d'un port)
            found = duck.resource_error(exc)
            if isinstance(found, MemoryError) or is_out_of_memory(exc):
                res.status, res.error = "error", str(_out_of_memory())
            else:
                res.status, res.error = "error", str(found) if found else f"{type(exc).__name__} : {exc}"
            event(type="log", node=nid, level="ERROR", message=traceback.format_exc(limit=6))
        finally:
            if holding:
                heavy.release()
            for sink in sinks.values():
                sink.close(ok=ok)
        res.seconds = round(time.perf_counter() - t_wall, 3)
        res.rss_mb = sampler.peak(t_mono, time.monotonic())
        if sinks or sources:
            res.rows = {p: s.rows for p, s in sinks.items()}
            res.rows_in = {p: s.rows for p, s in sources.items()}
        if ok:
            res.status = "done"
            write_meta(
                ndir,
                {
                    "fingerprint": prints[nid],
                    "static": statics[nid],
                    "status": "done",
                    "rows": res.rows,
                    "rows_in": res.rows_in,
                    "seconds": res.seconds,
                },
            )
        for port, count in res.rows.items():
            event(type="counts", node=nid, port=port, count=count)

    def job(nid: str) -> str:
        with active_lock:
            active.add(nid)
        try:
            run_node(nid)
        finally:
            with active_lock:
                active.discard(nid)
            if nid in streams:
                streams[nid][1].close()  # l'amont n'attend plus ce nœud, même s'il n'a pas tout lu
        r = results[nid]
        event(
            type="node",
            node=nid,
            status=r.status,
            seconds=r.seconds,
            rows=r.rows,
            rows_in=r.rows_in,
            rss_mb=r.rss_mb,
            error=r.error,
        )
        return nid

    def group(root: str) -> list[str]:
        """Le nœud et tous ceux qui sont branchés en direct sur lui (de proche en proche) : ils démarrent ensemble."""
        members, todo = [root], [root]
        while todo:
            for child in live_children.get(todo.pop(), []):
                link = plan.inbound[child][0]
                tap = Tap(cancel)
                taps[(link.from_[0], link.from_[1])].append(tap)
                streams[child] = (link.to[1], tap)
                members.append(child)
                todo.append(child)
        return members

    node_workers = max_workers or min(8, run_threads)
    waiting = {nid: set(d) for nid, d in deps.items() if nid not in live}
    remaining = dict(consumers)
    running: dict[Any, str] = {}
    finished: set[str] = set()

    released: set[str] = set()

    def release(up: str) -> None:
        """Sans cache : les fichiers d'un nœud partent quand lui et tous ses consommateurs ont fini, et qu'aucune
        définition encore gardée ne le désigne (un nœud final différé garde sa chaîne amont, ADR 0044)."""
        if up in released or effective_cache[up] or up not in has_consumers or remaining[up] or up not in finished:
            return
        with refs_lock:
            if any(up in refs for n, refs in defined_from.items() if n not in released):
                return
            sources_of = defined_from.get(up, set())
        shutil.rmtree(run_dir / up, ignore_errors=True)
        released.add(up)
        for u in sources_of:
            release(u)

    # nœuds repris du run précédent : passés tout de suite, sans attendre une place pour s'exécuter (consultables dès le
    # début du run, même pendant un nœud long)
    for nid in [n for n in plan.order if previous_of.get(n)]:
        if not adopt(nid, previous_of[nid]):  # type: ignore[arg-type]
            continue
        finished.add(nid)
        del waiting[nid]
        for d in waiting.values():
            d.discard(nid)
        for up in deps[nid]:
            remaining[up] -= 1
    # run « jusqu'au nœud » : les nœuds hors de sa branche dont rien n'a changé gardent leurs derniers résultats
    # (fichiers liés) ; le run suivant, complet ou visant une autre branche, les retrouve à son tour
    if partial:
        for nid in plan.order:
            if nid not in previous_of and (kept := previous_result(nid, outside=True)):
                adopt(nid, kept)

    try:
        with (
            ThreadPoolExecutor(max_workers=node_workers, thread_name_prefix="etl-node") as pool,
            ThreadPoolExecutor(max_workers=max(1, len(live)), thread_name_prefix="etl-live") as live_pool,
        ):
            while waiting or running:
                if not cancel.is_set() and state["error"] is None:
                    for nid in [n for n, d in waiting.items() if not d]:
                        del waiting[nid]
                        members = group(nid)
                        for member in members[1:]:  # les consommateurs d'abord : ils attendent les lots
                            running[live_pool.submit(job, member)] = member
                        running[pool.submit(job, nid)] = nid
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in done:
                    nid = running.pop(fut)
                    fut.result()
                    r = results[nid]
                    if r.status in ("done", "cached"):
                        finished.add(nid)
                        for d in waiting.values():
                            d.discard(nid)
                        for up in deps[nid]:
                            remaining[up] -= 1
                            release(up)
                        release(nid)
                    elif r.status == "error" and state["error"] is None:
                        state["error"] = f"{nid} : {r.error}"
                        cancel.set()
                if cancel.is_set() or state["error"]:
                    waiting.clear()
    finally:
        sampler.stop()
        if timeout:
            timer.cancel()
        _publish_aliases(run_dir, aliases, event)
        shutil.rmtree(run_dir / ".spill", ignore_errors=True)

    if state["oom"] is not None:
        rss, nodes = state["oom"]
        message = str(_out_of_memory(", ".join(nodes) or "-", round(rss / 1024, 1)))
        for nid in nodes:
            results[nid].status, results[nid].error = "error", message
        return finish("error", message)
    if state["error"]:
        return finish("error", state["error"])
    if state["timed_out"]:
        return finish("error", f"délai maximal dépassé ({timeout} s)")
    if cancel.is_set():
        return finish("cancelled", None)
    return finish("done", None)
