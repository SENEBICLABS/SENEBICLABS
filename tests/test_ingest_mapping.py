"""
Slice #4 (client-independent half): the declared-mapping ingest scaffolding turns a client's
manifest into normalized items via a column mapping, and fails LOUDLY on bad input before
anything uploads. Run: PYTHONPATH=. python3 tests/test_ingest_mapping.py
(pytest is not installed in this env, so this is self-running.)
"""
import os
import tempfile

from app.services import ingest


def _csv(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_default_mapping():
    path = _csv("filename,prediction\nimg001.png,Pneumonia\nimg002.png,Normal\n")
    try:
        rows = ingest.load_manifest(path, ingest.DEFAULT_MAPPING)
        assert [r["image"] for r in rows] == ["img001.png", "img002.png"]
        assert rows[0]["prediction"] == "Pneumonia"
        assert rows[0]["extra"] == {}
    finally:
        os.remove(path)


def test_custom_mapping_with_extra_columns():
    path = _csv("image,model_label,study_id\nx.png,TB,S-1\n")
    mapping = {"format": "csv", "image_column": "image",
               "prediction_column": "model_label", "extra_columns": ["study_id"]}
    try:
        rows = ingest.load_manifest(path, mapping)
        assert rows[0]["image"] == "x.png"
        assert rows[0]["prediction"] == "TB"
        assert rows[0]["extra"] == {"study_id": "S-1"}
    finally:
        os.remove(path)


def test_prediction_column_optional():
    path = _csv("filename\na.png\nb.png\n")
    mapping = {"format": "csv", "image_column": "filename"}
    try:
        rows = ingest.load_manifest(path, mapping)
        assert rows[1]["prediction"] is None
    finally:
        os.remove(path)


def test_loud_failures():
    good = "filename,prediction\na.png,Normal\n"

    # bad mappings / formats
    for mapping, why in [
        ({"format": "parquet", "image_column": "filename"}, "unsupported format"),
        ({"format": "csv"}, "missing image_column"),
        ({"format": "csv", "image_column": "filename", "extra_columns": "study_id"}, "extra_columns not a list"),
    ]:
        p = _csv(good)
        try:
            ingest.load_manifest(p, mapping)
            raise AssertionError(f"expected ValueError for: {why}")
        except ValueError:
            pass
        finally:
            os.remove(p)

    # declared column absent from the manifest
    p = _csv("wrongcol,prediction\na.png,Normal\n")
    try:
        ingest.load_manifest(p, ingest.DEFAULT_MAPPING)
        raise AssertionError("expected ValueError for missing declared column")
    except ValueError as e:
        assert "missing declared column" in str(e)
    finally:
        os.remove(p)

    # empty manifest (header only) and a blank image cell
    p = _csv("filename,prediction\n")
    try:
        ingest.load_manifest(p, ingest.DEFAULT_MAPPING)
        raise AssertionError("expected ValueError for empty manifest")
    except ValueError:
        pass
    finally:
        os.remove(p)

    p = _csv("filename,prediction\n ,Normal\n")
    try:
        ingest.load_manifest(p, ingest.DEFAULT_MAPPING)
        raise AssertionError("expected ValueError for blank image cell")
    except ValueError as e:
        assert "row 1" in str(e)
    finally:
        os.remove(p)


if __name__ == "__main__":
    test_default_mapping()
    test_custom_mapping_with_extra_columns()
    test_prediction_column_optional()
    test_loud_failures()
    print("PASS: tests/test_ingest_mapping.py (declared mapping maps rows; bad input fails loudly)")
