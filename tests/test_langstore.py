"""Persisting a bootstrapped language: it must survive a round trip exactly,
and it must never be able to impersonate a built-in entry."""

import dataclasses

import pytest

from purecoder import langstore
from purecoder.languages import BUILTIN_NAMES, PYTHON, LanguageSpec, ProjectSpec


def test_a_spec_survives_a_round_trip_unchanged():
    assert langstore.from_json(langstore.to_json(PYTHON)) == PYTHON


def test_tuple_fields_come_back_as_tuples_not_lists():
    """JSON has no tuple. A LanguageSpec is frozen and compared by value, so a
    list where a tuple belongs makes every equality check quietly false."""
    spec = langstore.from_json(langstore.to_json(PYTHON))
    assert isinstance(spec.run, tuple)
    assert isinstance(spec.aliases, tuple)


def test_a_spec_with_no_project_round_trips():
    bare = LanguageSpec(name="bare", extension=".b")
    assert langstore.from_json(langstore.to_json(bare)) == bare


def test_provenance_is_recorded_and_does_not_break_the_round_trip():
    data = langstore.to_json(PYTHON, docs_dir="/docs", learned="2026-08-03")
    assert data["bootstrapped"] is True
    assert data["docs_dir"] == "/docs"
    assert langstore.from_json(data) == PYTHON


def test_a_docs_store_survives_the_round_trip():
    spec = LanguageSpec(name="zig", extension=".zig", docs_store="zig")
    assert langstore.from_json(langstore.to_json(spec)).docs_store == "zig"


def test_an_entry_written_before_the_field_existed_still_loads():
    """Real files predate it. A missing key is an old entry, not a broken one."""
    data = langstore.to_json(LanguageSpec(name="zig", extension=".zig"))
    del data["docs_store"]
    assert langstore.from_json(data).docs_store == ""


def test_the_docs_index_follows_the_store_root(monkeypatch, tmp_path):
    """The spec holds a stem, never a path: an absolute path baked into a saved
    language breaks the moment PURECODER_HOME moves."""
    monkeypatch.setenv("PURECODER_HOME", str(tmp_path))
    assert langstore.docs_index_path("zig") == tmp_path / "docs" / "zig"


def test_the_docs_index_is_not_inside_the_languages_directory(monkeypatch,
                                                              tmp_path):
    """`load_all` globs *.json there. An index's metadata file living alongside
    the languages would be read as one."""
    monkeypatch.setenv("PURECODER_HOME", str(tmp_path))
    assert langstore.store_dir() not in langstore.docs_index_path("zig").parents


def test_the_built_in_names_are_reserved():
    assert {"python", "c++", "rust", "powerquery", "go", "ocaml"} <= BUILTIN_NAMES


def test_a_project_spec_round_trips_field_for_field():
    spec = LanguageSpec(name="x", extension=".x",
                        project=ProjectSpec(entry="m.x", install="i", run="r",
                                            test="t", entry_stub="STUB"))
    assert langstore.from_json(langstore.to_json(spec)).project.entry_stub == "STUB"


# ---- saving and loading --------------------------------------------------

CANDIDATE = LanguageSpec(
    name="zig", extension=".zig", probe=("zig", "version"),
    build=("zig", "build-exe", "{src}"), run=("{bin}",),
    preamble="PRE", epilogue="POST", test_system="assert with PC_CHECK",
    check_call="PC_CHECK",
)


def test_saving_then_loading_restores_the_spec(store):
    from purecoder.languages import REGISTRY

    langstore.save(CANDIDATE, docs_dir="/docs")
    assert (store / "zig.json").is_file()

    REGISTRY.pop("zig", None)
    loaded = langstore.load_all()
    assert [s.name for s in loaded] == ["zig"]
    assert REGISTRY["zig"] == CANDIDATE


def test_saving_a_reserved_name_is_refused(store):
    for name in ("python", "powerquery"):
        with pytest.raises(ValueError, match="reserved"):
            langstore.save(dataclasses.replace(CANDIDATE, name=name))


def test_a_placeholder_name_can_be_learned(store):
    """`go`, `java`, `swift` and `ocaml` are declared so a refusal can name
    them, and wired to nothing. A learned entry is exactly what they are
    waiting for."""
    from purecoder.languages import REGISTRY

    langstore.save(dataclasses.replace(CANDIDATE, name="ocaml"))
    REGISTRY.pop("ocaml", None)
    assert [s.name for s in langstore.load_all()] == ["ocaml"]
    assert REGISTRY["ocaml"].run == CANDIDATE.run


def test_a_file_claiming_a_built_in_name_is_ignored(store):
    """The guard has to hold at load time too -- the file is editable by hand
    and by anything else on the machine."""
    import json

    from purecoder.languages import REGISTRY

    store.mkdir(parents=True, exist_ok=True)
    (store / "python.json").write_text(json.dumps(
        {"name": "python", "extension": ".fake", "run": ["echo"],
         "test_system": "x", "probe": [], "build": [], "aliases": []}))
    langstore.load_all()
    assert REGISTRY["python"].extension == ".py", "a built-in was overwritten"


def test_a_corrupt_file_is_skipped_not_fatal(store):
    import json

    store.mkdir(parents=True, exist_ok=True)
    (store / "broken.json").write_text("{not json")
    (store / "zig.json").write_text(json.dumps(langstore.to_json(CANDIDATE)))
    assert [s.name for s in langstore.load_all()] == ["zig"]


def test_a_file_missing_a_required_field_is_skipped(store):
    import json

    store.mkdir(parents=True, exist_ok=True)
    (store / "half.json").write_text(json.dumps({"extension": ".h"}))
    assert langstore.load_all() == []


def test_loading_an_empty_store_is_not_an_error(store):
    assert langstore.load_all() == []
