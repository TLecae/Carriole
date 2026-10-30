"""Découverte des modificateurs : intégrés, dossier de plugins, entry points `etl.modifiers`."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import os
import pkgutil
import sys
from importlib.metadata import entry_points
from pathlib import Path

from etl_engine import i18n
from etl_engine.sdk import Modifier

log = logging.getLogger("etl.registry")


class Registry:
    def __init__(self) -> None:
        self._by_name: dict[str, type[Modifier]] = {}
        self.errors: dict[str, str] = {}  # source -> message : un plugin cassé n'empêche pas les autres

    def register(self, cls: type[Modifier], source: str = "") -> None:
        if not cls.name:
            return
        known = self._by_name.get(cls.name)
        if known is not None and known is not cls:
            self.errors[source or cls.__module__] = f"nom de modificateur en double : {cls.name}"
            return
        self._by_name[cls.name] = cls

    def _register_module(self, module: object, source: str) -> None:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Modifier) and obj is not Modifier and obj.__module__ == getattr(module, "__name__", ""):
                self.register(obj, source)

    def load_builtin(self) -> None:
        package = importlib.import_module("etl_engine.modifiers")
        for info in pkgutil.iter_modules(package.__path__):
            self._register_module(importlib.import_module(f"etl_engine.modifiers.{info.name}"), info.name)

    def load_entry_points(self) -> None:
        for ep in [*entry_points(group="etl.modifiers"), *entry_points(group="etl.transformers")]:  # + ancien nom
            try:
                loaded = ep.load()
            except Exception as exc:  # noqa: BLE001 - un plugin tiers peut échouer de mille façons
                self.errors[ep.name] = f"{type(exc).__name__}: {exc}"
                continue
            if inspect.isclass(loaded) and issubclass(loaded, Modifier):
                self.register(loaded, ep.name)
            else:
                self._register_module(loaded, ep.name)

    def load_directory(self, directory: str | Path) -> None:
        """Importe chaque `*.py` du dossier (un modificateur par fichier, sans installation). Sans dossier, rien : un
        chemin vide désignerait le dossier courant, dont chaque script serait exécuté."""
        if not str(directory).strip():
            return
        directory = Path(directory)
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"etl_plugin_{path.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            except Exception as exc:  # noqa: BLE001
                self.errors[path.name] = f"{type(exc).__name__}: {exc}"
                log.warning("plugin %s ignoré : %s", path.name, exc)
                continue
            self._register_module(module, path.name)
            locales = path.with_suffix(".locales.json")
            if locales.is_file():
                i18n.add_plugin_catalog(locales)

    @classmethod
    def discover(cls, plugins_dir: str | Path | None = None) -> Registry:
        reg = cls()
        reg.load_builtin()
        reg.load_entry_points()
        reg.load_directory(plugins_dir or os.environ.get("ETL_PLUGINS_DIR", ""))
        return reg

    # --- accès -------------------------------------------------------------------------
    def get(self, name: str) -> type[Modifier]:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"modifier inconnu : {name}") from None

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def all(self) -> list[type[Modifier]]:
        """Par catégorie, puis par libellé sans sa ponctuation (« Lecture (Geo)Parquet » reste parmi les « Geo… »)."""

        def label(c: type[Modifier]) -> str:
            return "".join(ch for ch in c.label or c.name if ch.isalnum() or ch.isspace())

        return sorted(self._by_name.values(), key=lambda c: (c.category, label(c)))
