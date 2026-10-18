"""The hashed brand check still catches a planted name.

The planted name is a neutral fictional brand (or BRAND_CHECK_PLANTED_TERM from
the environment); its hashes are computed here, so no real name appears in tests.
"""
import importlib.util
import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_brands", ROOT / "scripts" / "check_brands.py")
cb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cb)

PLANTED = os.environ.get("BRAND_CHECK_PLANTED_TERM", "Zorblax Provisions")


def _hashes(*terms):
    return {"term_hashes": {cb.digest(t.lower()) for t in terms},
            "spelling_hashes": {cb.digest(t) for t in terms}}


def test_planted_term_is_caught_in_a_file(tmp_path):
    (tmp_path / "data.json").write_text(f'{{"merchant": "{PLANTED} online"}}\n')
    hits = cb.scan(tmp_path, ["data.json"], **_hashes(PLANTED))
    assert hits == [f"data.json:1: {PLANTED}"]


def test_word_boundaries_and_case_match_the_old_regex():
    h = _hashes("Quillon", "Brakk-Moss", "Fenwick and Dorr")
    assert cb.find_hits("paid Quillon's card; Quillon.co; Quillon123", **h) == ["Quillon"] * 3
    assert cb.find_hits("XQuillon, Quillonx, quillon, QUILLON", **h) == []
    assert cb.find_hits("Brakk-Moss and Fenwick and Dorr", **h) == ["Brakk-Moss", "Fenwick and Dorr"]
    assert cb.find_hits("Fenwick  and Dorr", **h) == []


def test_blocklist_is_stored_as_hashes_only():
    for s in (*cb.TERM_HASHES, *cb.SPELLING_HASHES):
        assert re.fullmatch(r"[0-9a-f]{64}", s)
    assert len(cb.SPELLING_HASHES) >= len(cb.TERM_HASHES) > 0
