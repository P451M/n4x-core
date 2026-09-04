from __future__ import annotations

from n4x.system.inspect_bounds import INSPECT_HARD_CAP, page_inspect


def test_page_inspect_summarizes_and_truncates() -> None:
    items = [type("Row", (), {"id": str(index), "values": {"n": index}})() for index in range(3)]

    page = page_inspect(
        items,
        offset=0,
        limit=2,
        include_values=False,
        dump=lambda item: {"id": item.id, "values": item.values},
        summary=lambda item: {"id": item.id},
    )
    assert page["status"] == "truncated"
    assert page["count"] == 3
    assert page["items"] == [{"id": "0"}, {"id": "1"}]

    one = page_inspect(
        items,
        include_values=True,
        item_id="2",
        id_of=lambda item: item.id,
        dump=lambda item: {"id": item.id, "values": item.values},
        summary=lambda item: {"id": item.id},
    )
    assert one["status"] == "ok"
    assert one["items"] == [{"id": "2", "values": {"n": 2}}]


def test_page_inspect_rejects_over_cap() -> None:
    page = page_inspect(
        [],
        limit=INSPECT_HARD_CAP + 1,
        dump=lambda item: {},
        summary=lambda item: {},
    )
    assert page["status"] == "too_large"
    assert page["items"] == []
