"""
purecoder/langstore.py

Where a bootstrapped language lives between runs.

A hand-written entry is code. A drafted one is data, so it is stored as data:
one JSON file per language under a user data dir, loaded into the registry at
import. That keeps "adding a language is data, not code" literally true, and
makes a bad entry removable with `rm`.
"""

import dataclasses
import json
import os
from pathlib import Path

from .languages import RESERVED_NAMES, LanguageSpec, ProjectSpec, register

# JSON has no tuple, and a LanguageSpec is frozen and compared by value -- a
# list where a tuple belongs makes every equality check quietly false.
_TUPLE_FIELDS = ("probe", "build", "run", "aliases")
_FIELDS = tuple(f.name for f in dataclasses.fields(LanguageSpec))


def store_dir() -> Path:
    """Where saved languages live. PURECODER_HOME wins, then XDG."""
    root = os.environ.get("PURECODER_HOME")
    if root:
        return Path(root) / "languages"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "purecoder" / "languages"


def to_json(spec: LanguageSpec, **provenance) -> dict:
    """A spec plus where it came from.

    Provenance is not part of the spec, so the CLI can say "drafted from
    ./zig-docs on 2026-08-03" rather than presenting a candidate as though a
    human had written it.
    """
    data = {f: getattr(spec, f) for f in _FIELDS}
    for f in _TUPLE_FIELDS:
        data[f] = list(data[f])
    data["project"] = dataclasses.asdict(spec.project) if spec.project else None
    data["bootstrapped"] = True
    data.update(provenance)
    return data


def from_json(data: dict) -> LanguageSpec:
    """Rebuild a spec, ignoring provenance keys it does not declare."""
    fields = {f: data[f] for f in _FIELDS if f in data}
    for f in _TUPLE_FIELDS:
        if f in fields:
            fields[f] = tuple(fields[f])
    if fields.get("project"):
        fields["project"] = ProjectSpec(**fields["project"])
    return LanguageSpec(**fields)


def save(spec: LanguageSpec, **provenance) -> Path:
    """Write one language to the store. -> the path written."""
    if spec.name in RESERVED_NAMES:
        raise ValueError(f"{spec.name!r} is a reserved language -- a drafted "
                         f"spec may not replace a wired entry or a standing "
                         f"refusal")
    directory = store_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{spec.name}.json"
    path.write_text(json.dumps(to_json(spec, **provenance), indent=2) + "\n")
    return path


def load_all() -> list:
    """Register every saved language. -> those loaded.

    A bad file must never stop the CLI from starting: this runs at import, so a
    truncated write or a hand-edit that lost a brace would otherwise make
    `purecoder status` unusable rather than merely losing one language.
    """
    directory = store_dir()
    if not directory.is_dir():
        return []

    loaded = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        # The shadow guard holds here as well as in save(): the file is
        # editable by hand, so checking only on the way in checks the wrong end.
        if not isinstance(data, dict) or data.get("name") in RESERVED_NAMES:
            continue
        try:
            spec = from_json(data)
        except (TypeError, KeyError, ValueError):
            continue
        register(spec)
        loaded.append(spec)
    return loaded
