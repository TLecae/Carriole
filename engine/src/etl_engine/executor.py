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
from pydantic import ValidationError

from etl_engine import duck, i18n
from etl_engine.analysis import compute_schemas
from etl_engine.cache import (
    PortSink,
    PortSource,
    PortStream,
    Tap,
    Throttle,
    port_path,
    read_meta,
    reuse_files,
    write_meta,
)
from etl_engine.components import expand_components
from etl_engine.data import DEFAULT_BATCH_ROWS
from etl_engine.flow import Flow, expand_late, resolve_params, system_values
from etl_engine.plan import Plan, build_plan
from etl_engine.registry import Registry
from etl_engine.sdk import Cancelled, ConfigError, Context
from etl_engine.spatial import is_out_of_memory
from etl_engine.sqlroute import has_sql_route, run_sql_route

EventSink = Callable[[dict[str, Any]], None]


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


def fingerprint(node_type: str, inst: Any, upstream: list[tuple[str, str, str]]) -> str:
    payload = {
        "type": node_type,
        "version": inst.version,
        "params": inst.params.model_dump(mode="json"),
        "extra": inst.fingerprint_extra(),
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


def _default_batch_workers(threads: int) -> int:
    """Lots traités en parallèle par nœud : `ETL_BATCH_WORKERS`, sinon la moitié des cœurs du run (2 à 8)."""
    env = os.environ.get("ETL_BATCH_WORKERS")
    if env:
        return max(1, int(env))
    return max(2, min(8, threads // 2))


def _publish_aliases(
    run_dir: Path, aliases: dict[tuple[str, str], tuple[str, str]], event: Callable[..., None]
) -> None:
    """Rend les ports d'un nœud composant consultables : lien vers le fichier du port interne qui les alimente."""
    published: dict[str, dict[str, int]] = defaultdict(dict)
    for (outer, port), (inner, inner_port) in aliases.items():
        source = port_path(run_dir, inner, inner_port)
        if not source.exists():
            continue
        dst = port_path(run_dir, outer, port)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.unlink(missing_ok=True)
        try:
            os.link(source, dst)
        except OSError:
            shutil.copy2(source, dst)
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
            workers=batch_workers if batch_workers is not None else _default_batch_workers(run_threads),
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
    fresh = uuid.uuid4().hex  # empreinte propre à ce run : un nœud toujours relancé invalide aussi son aval
    for nid in plan.order:
        ups = [(link.to[1], prints[link.from_[0]], link.from_[1]) for link in plan.inbound.get(nid, [])]
        prints[nid] = fingerprint(flow.node(nid).type, plan.nodes[nid], ups)
        inst = plan.nodes[nid]
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

    def reusable(nid: str) -> dict | None:
        previous = read_meta(Path(reuse_from) / nid) if (reuse_from and effective_cache[nid]) else None
        if not (previous and previous.get("status") == "done" and previous.get("fingerprint") == prints[nid]):
            return None
        # ses données doivent être encore là (run sans cache, cache purgé : le nœud est recalculé)
        folder = Path(reuse_from)  # type: ignore[arg-type]
        kept = all((folder / nid / f"{port}.parquet").is_file() for port, n in previous.get("rows", {}).items() if n)
        return previous if kept else None

    previous_of = {nid: reusable(nid) for nid in selected}
    # run « jusqu'au nœud » : les nœuds hors de sa branche encore valables passent dans ce run (fichiers liés, pas
    # recopiés) ; le run suivant, qu'il soit complet ou vise une autre branche, les reprend à son tour
    if partial:
        for nid in plan.order:
            kept = None if nid in previous_of else reusable(nid)
            if kept is None:
                continue
            reuse_files(Path(reuse_from) / nid, run_dir / nid)  # type: ignore[arg-type]
            write_meta(run_dir / nid, kept)
            res = results[nid]
            res.rows, res.rows_in, res.status = kept["rows"], kept.get("rows_in", {}), "cached"
            event(type="node", node=nid, status="cached", seconds=0, rows=res.rows, rows_in=res.rows_in)

    # --- branchement en direct : un nœud à une seule liaison entrante démarre avec son amont
    use_pipeline = pipeline if pipeline is not None else os.environ.get("ETL_PIPELINE", "1") not in ("0", "false")
    sql_capable = {nid: use_sql and has_sql_route(plan.nodes[nid]) for nid in selected}

    def pipelinable(nid: str) -> bool:
        links = plan.inbound.get(nid, [])
        if not use_pipeline or len(links) != 1 or nid in plan.late:
            return False
        up = links[0].from_[0]
        return (
            up in deps[nid]
            and not previous_of[nid]
            and not previous_of[up]
            and not sql_capable[nid]
            and not sql_capable[up]
        )

    live = {nid for nid in selected if pipelinable(nid)}
    live_children: dict[str, list[str]] = defaultdict(list)
    for nid in selected:
        if nid in live:
            live_children[plan.inbound[nid][0].from_[0]].append(nid)
    taps: dict[tuple[str, str], list[Tap]] = defaultdict(list)
    streams: dict[str, tuple[str, Tap]] = {}  # nœud en direct -> (port d'entrée, file)

    # un port sans fichier : run sans cache, tous ses consommateurs en direct, pas la sortie d'un composant
    alias_sources = set(aliases.values())
    port_links: dict[tuple[str, str], list[str]] = defaultdict(list)
    for nid in selected:
        for link in plan.inbound.get(nid, []):
            port_links[(link.from_[0], link.from_[1])].append(nid)

    def store(nid: str, port: str) -> bool:
        readers = port_links.get((nid, port), [])
        return (
            effective_cache[nid] or not readers or (nid, port) in alias_sources or any(r not in live for r in readers)
        )

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
            previous = previous_of[nid]
            if previous:
                reuse_files(Path(reuse_from) / nid, ndir)  # type: ignore[arg-type]
                res.rows, res.rows_in, res.status = previous["rows"], previous.get("rows_in", {}), "cached"
                res.seconds = round(time.perf_counter() - t_wall, 3)
                write_meta(ndir, previous)
                for port, count in res.rows.items():
                    event(type="counts", node=nid, port=port, count=count)
                return

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
            if nid in streams:
                port, tap = streams[nid]
                sources[port] = PortStream(tap)
            else:
                port_files: dict[str, list[Path]] = {
                    port: [
                        p
                        for link in plan.inbound.get(nid, [])
                        if link.to[1] == port and (p := port_path(run_dir, *link.from_)).exists()
                    ]
                    for port in ins
                }
                if sql_capable[nid]:
                    routed = run_sql_route(
                        inst,
                        port_files,
                        {port: port_path(run_dir, nid, port) for port in outs},
                        {port: schemas.get((nid, port)) for port in outs},
                        {link.to[1]: schemas.get((link.from_[0], link.from_[1])) for link in plan.inbound.get(nid, [])},
                        ndir / ".spill",
                    )
                    if routed is not None:
                        res.rows, res.rows_in = routed.rows, routed.rows_in
                        res.status = "done"
                        res.seconds = round(time.perf_counter() - t_wall, 3)
                        res.rss_mb = sampler.peak(t_mono, time.monotonic())
                        write_meta(
                            ndir,
                            {
                                "fingerprint": prints[nid],
                                "status": "done",
                                "rows": res.rows,
                                "rows_in": res.rows_in,
                                "seconds": res.seconds,
                                "route": "sql",
                            },
                        )
                        for port, count in res.rows.items():
                            event(type="counts", node=nid, port=port, count=count)
                        event(type="log", node=nid, level="INFO", message="exécuté par DuckDB (voie SQL)")
                        return
                for port, files in port_files.items():
                    sources[port] = PortSource(files, batch_rows, cancel)
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

            def emit(port: str, table: Any) -> None:
                sink = sinks.get(port)
                if sink is None:
                    raise ConfigError(f"{inst.name} : port de sortie inconnu « {port} »")
                sink.write(table)

            if inst.mode == "blocking" and nid not in live:
                # nœud qui charge tout : quelques-uns à la fois (un nœud en direct suit son amont, il n'attend pas)
                while not heavy.acquire(timeout=0.2):
                    if cancel.is_set():
                        raise Cancelled
                holding = True
            inst.run(sources, emit)
            ok = True
        except Cancelled:
            res.status = "cancelled"
        except ConfigError as exc:
            res.status, res.error = "error", f"configuration : {exc}"
        except MemoryError:
            res.status, res.error = "error", str(_out_of_memory())
            event(type="log", node=nid, level="ERROR", message=traceback.format_exc(limit=6))
        except Exception as exc:  # noqa: BLE001 - toute erreur de modificateur est rapportée, pas propagée
            # GEOS signale un manque de mémoire par une exception ordinaire (« bad allocation »)
            memory = is_out_of_memory(exc)
            res.status, res.error = "error", str(_out_of_memory()) if memory else f"{type(exc).__name__} : {exc}"
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

    def release(up: str) -> None:
        """Sans cache : les fichiers d'un nœud partent quand lui et tous ses consommateurs ont fini."""
        if not effective_cache[up] and up in has_consumers and remaining[up] == 0 and up in finished:
            shutil.rmtree(run_dir / up, ignore_errors=True)

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
