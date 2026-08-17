"""
Row-order integrity (correctness gate for radiology delivery). The index of an item MUST be
its position in the client's manifest, so a verdict can never land against the wrong X-ray.
A missing image must leave a visible gap, never shift everything after it. Every delivery
path (portal_results, admin_export) orders by this index, so protecting it at ingest
protects the whole chain. Run: PYTHONPATH=. python3 tests/test_ingest_order.py

Note: case-id passthrough (#4) is the durable fix and is MUST-FIX before real delivery; until
then this index IS the mapping, so its integrity is non-negotiable.
"""
from app.services import ingest


def _manifest(names):
    return [{"image": n, "prediction": f"pred_{n}", "extra": {}} for n in names]


def test_all_present_keeps_manifest_order():
    m = _manifest(["a.png", "b.png", "c.png", "d.png"])
    present, gaps = ingest.plan_items(m, lambda name: True)
    assert gaps == []
    assert [r["idx"] for r in present] == [0, 1, 2, 3]
    # idx N maps to manifest row N — the mapping the client relies on
    assert [r["image"] for r in present] == ["a.png", "b.png", "c.png", "d.png"]


def test_missing_middle_leaves_gap_not_shift():
    m = _manifest(["a.png", "b.png", "c.png", "d.png"])
    # c.png is unreadable/missing
    present, gaps = ingest.plan_items(m, lambda name: name != "c.png")

    # c's slot is a visible gap...
    assert [g["idx"] for g in gaps] == [2]
    assert gaps[0]["image"] == "c.png"

    # ...and crucially d.png STAYS at index 3, not shifted up to 2.
    by_idx = {r["idx"]: r["image"] for r in present}
    assert by_idx == {0: "a.png", 1: "b.png", 3: "d.png"}
    assert 2 not in by_idx, "gap must remain empty; d must not slide into c's slot"

    # union of present + gap indices covers every manifest row exactly once
    assert sorted([r["idx"] for r in present] + [g["idx"] for g in gaps]) == [0, 1, 2, 3]


def test_first_and_last_missing():
    m = _manifest(["a.png", "b.png", "c.png"])
    present, gaps = ingest.plan_items(m, lambda name: name == "b.png")
    assert [r["idx"] for r in present] == [1]
    assert present[0]["image"] == "b.png"  # b stays at index 1, not pulled to 0
    assert sorted(g["idx"] for g in gaps) == [0, 2]


def test_present_items_never_reordered():
    # Even if exists() is asked in a weird order, output indices are ascending manifest positions.
    m = _manifest([f"img{i}.png" for i in range(10)])
    present, gaps = ingest.plan_items(m, lambda name: int(name[3:-4]) % 3 != 0)  # drop 0,3,6,9
    idxs = [r["idx"] for r in present]
    assert idxs == sorted(idxs), "present items must stay in manifest order"
    assert idxs == [1, 2, 4, 5, 7, 8]
    assert sorted(g["idx"] for g in gaps) == [0, 3, 6, 9]


if __name__ == "__main__":
    test_all_present_keeps_manifest_order()
    test_missing_middle_leaves_gap_not_shift()
    test_first_and_last_missing()
    test_present_items_never_reordered()
    print("PASS: tests/test_ingest_order.py (idx == manifest row; missing image = visible gap, no shift)")
