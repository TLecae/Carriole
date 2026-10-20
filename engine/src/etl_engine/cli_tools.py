"""Commandes `etl` d'outillage : inspect, config, connections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from etl_engine import atomic, backend
from etl_engine import settings as user_settings
from etl_engine.cli_backend import EXIT_ERROR, require_server
from etl_engine.cli_io import say

config_app = typer.Typer(help="Réglages de l'utilisateur (settings.json).", no_args_is_help=True)
connections_app = typer.Typer(help="Connexions aux bases de données (backend démarré).", no_args_is_help=True)
i18n_app = typer.Typer(help="Catalogues de langue (textes traduits).", no_args_is_help=True)


def _parse_value(text: str) -> Any:
    """`set langue en` écrit du texte ; `set workers 4` un nombre ; `set a '{\"x\": 1}'` du JSON."""
    try:
        return json.loads(text)
    except ValueError:
        return text


# ---------------------------------------------------------------------------------------------- inspect


def describe_file(path: str, count: bool, layer: str | None = None) -> dict[str, Any]:
    """Format, couches ou feuilles, schéma, système de coordonnées (et nombre d'entités si demandé)."""
    import pyarrow.parquet as pq

    from etl_engine import formats
    from etl_engine.data import GEOM, get_crs
    from etl_engine.formats import ogr, xlsx

    fmt = formats.detect(path)
    out: dict[str, Any] = {"path": path, "format": fmt}
    opts: dict[str, Any] = {}
    if fmt in ogr.DRIVERS:
        out["layers"] = ogr.layers(path)
        if layer is None and out["layers"]:
            layer = out["layers"][0]
        opts["layer"] = layer
        out["layer"] = layer
    elif fmt == "xlsx":
        out["sheets"] = xlsx.sheets(path)
        opts["sheet"] = layer or (out["sheets"][0] if out["sheets"] else None)
        out["sheet"] = opts["sheet"]
    schema = formats.read_schema(fmt, path, **opts)
    out["crs"] = get_crs(schema)
    out["geometry"] = GEOM in schema.names
    out["fields"] = [{"name": f.name, "type": str(f.type)} for f in schema if f.name != GEOM]
    if fmt == "parquet" and "*" not in path:
        out["count"] = pq.ParquetFile(path).metadata.num_rows
    elif count:
        out["count"] = sum(t.num_rows for t in formats.read_batches(fmt, path, 100_000, **opts))
    return out


def register(app: typer.Typer) -> None:
    app.add_typer(config_app, name="config")
    app.add_typer(connections_app, name="connections")
    app.add_typer(i18n_app, name="i18n")

    @app.command()
    def inspect(
        file: Annotated[str, typer.Argument(help="Fichier de données (vecteur, CSV, Excel, Parquet).")],
        layer: Annotated[str | None, typer.Option("--layer", help="Couche (GeoPackage…) ou feuille (Excel).")] = None,
        count: Annotated[bool, typer.Option("--count", help="Compte les entités (lit tout le fichier).")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Décrit un fichier de données : format, couches, champs, système de coordonnées."""
        from etl_engine.sdk import ConfigError

        try:
            info = describe_file(file, count, layer)
        except (ConfigError, OSError, ValueError) as exc:
            say(f"Illisible : {exc}", err=True)
            raise typer.Exit(EXIT_ERROR) from None
        if json_output:
            say(json.dumps(info, ensure_ascii=False, indent=2))
            return
        say(f"{info['path']}  [{info['format']}]")
        for key, label in (("layers", "couches"), ("sheets", "feuilles")):
            if info.get(key):
                say(f"  {label} : {', '.join(info[key])}")
        say(f"  géométrie : {'oui' if info['geometry'] else 'non'}  SCR : {info['crs'] or '-'}")
        if "count" in info:
            say(f"  entités : {info['count']}")
        for f in info["fields"]:
            say(f"  {f['name']:30} {f['type']}")

    # ------------------------------------------------------------------ i18n
    @i18n_app.command("check")
    def i18n_check(lang: Annotated[str, typer.Option("--lang", help="Langue à contrôler.")] = "en") -> None:
        """Liste les textes sans traduction (et les traductions devenues inutiles). Code de sortie 1 s'il en manque."""
        from etl_engine import i18n_tools

        report = i18n_tools.check(lang)
        for key, label in (
            ("missing_modifiers", "sans traduction (modifiers)"),
            ("missing_messages", "sans traduction (messages)"),
            ("orphan_modifiers", "inutile (modifiers)"),
            ("orphan_messages", "inutile (messages)"),
        ):
            for item in report[key]:
                say(f"{label} : {item}")
        missing = len(report["missing_modifiers"]) + len(report["missing_messages"])
        say(f"{lang} : {missing} texte(s) sans traduction")
        raise typer.Exit(EXIT_ERROR if missing else 0)

    @i18n_app.command("extract")
    def i18n_extract(
        lang: Annotated[str, typer.Argument(help="Code de la langue (ex. de, es).")],
        out: Annotated[Path | None, typer.Option(help="Fichier à écrire (défaut : catalogue du moteur).")] = None,
    ) -> None:
        """Crée ou complète le catalogue d'une langue : les textes à traduire y reprennent le français."""
        from etl_engine import i18n_tools

        data = i18n_tools.extract(lang)
        if out is None:
            say(f"écrit : {i18n_tools.write_catalog(lang, data)}")
        else:
            atomic.write_text(out, json.dumps(data, ensure_ascii=False, indent=1) + chr(10))
            say(f"écrit : {out}")

    # ------------------------------------------------------------------ config
    @config_app.command("path")
    def config_path() -> None:
        """Emplacement du fichier de réglages et des données de l'application."""
        dirs = user_settings.app_dirs()
        say(f"réglages : {dirs.settings_file}\ndonnées  : {dirs.data}")

    @config_app.command("get")
    def config_get(
        key: Annotated[str | None, typer.Argument(help="Clé (a.b pour un sous-objet) ; vide : tout.")] = None,
    ):
        """Affiche un réglage, ou tous."""
        value = user_settings.load() if key is None else user_settings.get(key)
        if value is None:
            raise typer.Exit(EXIT_ERROR)
        say(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))

    @config_app.command("set")
    def config_set(key: Annotated[str, typer.Argument()], value: Annotated[str, typer.Argument()]) -> None:
        """Écrit un réglage (le texte est lu comme du JSON s'il en est : 4, true, [...])."""
        user_settings.set_value(key, _parse_value(value))

    @config_app.command("unset")
    def config_unset(key: Annotated[str, typer.Argument()]) -> None:
        """Supprime un réglage."""
        if not user_settings.unset(key):
            raise typer.Exit(EXIT_ERROR)

    # ------------------------------------------------------------------ connections
    @connections_app.command("list")
    def connections_list(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
        """Liste les connexions (sans mot de passe)."""
        rows = backend.call(require_server(), "GET", "/api/connections")
        if json_output:
            say(json.dumps(rows, ensure_ascii=False, indent=2))
            return
        for r in rows:
            say(f"{r['name']:24} {r.get('user', '')}@{r.get('host', '')}:{r.get('port', '')}/{r.get('dbname', '')}")

    @connections_app.command("add")
    def connections_add(
        name: Annotated[str, typer.Argument()],
        host: Annotated[str, typer.Option(prompt=True)],
        dbname: Annotated[str, typer.Option(prompt=True)],
        user: Annotated[str, typer.Option(prompt=True)],
        kind: Annotated[str, typer.Option(help="postgres ou oracle (--dbname : le service).")] = "postgres",
        port: Annotated[int | None, typer.Option(help="Vide : celui du type de base.")] = None,
        sslmode: Annotated[str, typer.Option()] = "prefer",
        password: Annotated[str, typer.Option(prompt=True, hide_input=True)] = "",
    ) -> None:
        """Crée ou met à jour une connexion (le mot de passe est saisi sans écho)."""
        body = {
            "kind": kind,
            "host": host,
            "port": port,
            "dbname": dbname,
            "user": user,
            "sslmode": sslmode,
            "password": password,
        }
        try:
            backend.call(require_server(), "PUT", f"/api/connections/{name}", body)
        except backend.BackendError as exc:
            say(f"Erreur : {exc}", err=True)
            raise typer.Exit(EXIT_ERROR) from None

    @connections_app.command("test")
    def connections_test(name: Annotated[str, typer.Argument()]) -> None:
        """Essaie la connexion."""
        try:
            result = backend.call(require_server(), "POST", f"/api/connections/{name}/test", timeout=60)
        except backend.BackendError as exc:
            say(f"Échec : {exc}", err=True)
            raise typer.Exit(EXIT_ERROR) from None
        say(json.dumps(result, ensure_ascii=False))
