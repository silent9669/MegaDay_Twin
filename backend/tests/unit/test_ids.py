from __future__ import annotations

from app.core.ids import is_ulid, new_id


def test_new_id_is_ulid_and_sortable() -> None:
    a, b = new_id(), new_id()
    assert len(a) == 26 and is_ulid(a)
    assert a != b


def test_new_id_prefix() -> None:
    i = new_id("dec")
    assert i.startswith("dec-")
    assert is_ulid(i)
    assert not is_ulid("dec-77")
