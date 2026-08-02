from __future__ import annotations

from pathlib import Path

CATALOG = Path("open-model-market/v5_catalog_view.py")
TESTS = Path("tests/test_v5_catalog_endpoint_eligibility.py")

HELPERS = '''def _canonical_endpoint_row(row: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _deduplicate_exact_endpoint_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    unique: list[Mapping[str, Any]] = []
    observed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("model") or ""),
            str(row.get("provider") or ""),
        )
        if not all(key):
            continue
        existing = observed.get(key)
        if existing is None:
            observed[key] = row
            unique.append(row)
            continue
        if _canonical_endpoint_row(existing) == _canonical_endpoint_row(row):
            continue
        raise CatalogViewError(
            f"conflicting duplicate exact catalog endpoint: {key}"
        )
    return unique


'''

OLD_INDEX = '''def catalog_index(
    catalog: Mapping[str, Any],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in catalog.get("endpoints", []):
        if not isinstance(row, Mapping):
            continue
        key = (
            str(row.get("model") or ""),
            str(row.get("provider") or ""),
        )
        if not all(key):
            continue
        if key in result:
            raise CatalogViewError(f"duplicate exact catalog endpoint: {key}")
        result[key] = row
    return result
'''

NEW_INDEX = '''def catalog_index(
    catalog: Mapping[str, Any],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    rows = [
        row
        for row in catalog.get("endpoints", [])
        if isinstance(row, Mapping)
    ]
    for row in _deduplicate_exact_endpoint_rows(rows):
        key = (
            str(row.get("model") or ""),
            str(row.get("provider") or ""),
        )
        result[key] = row
    return result
'''

TEST_METHODS = '''    def test_identical_exact_endpoint_rows_are_deduplicated(self) -> None:
        model = self.model(model_maximum=262_144)
        endpoint = {
            "tag": "together",
            "context_length": 1_048_576,
            "max_completion_tokens": 262_144,
            "supported_parameters": ["max_tokens", "temperature"],
            "pricing": {
                "prompt": "0.000001",
                "completion": "0.000003",
            },
        }
        catalog = compact_endpoint_catalog(
            [model],
            {
                model.id: {
                    "data": {
                        "endpoints": [endpoint, dict(endpoint)],
                    }
                }
            },
            required_context_tokens=16_384,
        )
        self.assertEqual(1, len(catalog["endpoints"]))
        row = catalog["endpoints"][0]
        index = catalog_index({"endpoints": [row, dict(row)]})
        self.assertEqual(1, len(index))
        self.assertIn((model.id, "together"), index)

    def test_conflicting_exact_endpoint_rows_fail_closed(self) -> None:
        model = self.model(model_maximum=262_144)
        first = {
            "tag": "together",
            "context_length": 1_048_576,
            "max_completion_tokens": 262_144,
            "supported_parameters": ["max_tokens", "temperature"],
            "pricing": {
                "prompt": "0.000001",
                "completion": "0.000003",
            },
        }
        second = {
            **first,
            "pricing": {
                "prompt": "0.000001",
                "completion": "0.000004",
            },
        }
        with self.assertRaisesRegex(
            CatalogViewError,
            "conflicting duplicate exact catalog endpoint",
        ):
            compact_endpoint_catalog(
                [model],
                {
                    model.id: {
                        "data": {
                            "endpoints": [first, second],
                        }
                    }
                },
                required_context_tokens=16_384,
            )

'''

catalog = CATALOG.read_text(encoding="utf-8")
if "def _deduplicate_exact_endpoint_rows(" in catalog:
    raise SystemExit("catalog deduplication is already present")
helper_anchor = "\n\ndef compact_endpoint_catalog("
if catalog.count(helper_anchor) != 1:
    raise SystemExit("catalog helper anchor mismatch")
catalog = catalog.replace(helper_anchor, "\n\n" + HELPERS + "def compact_endpoint_catalog(", 1)
rows_anchor = "            elif row is not None:\n                rows.append(row)\n    rows.sort("
if catalog.count(rows_anchor) != 1:
    raise SystemExit("catalog row-sort anchor mismatch")
catalog = catalog.replace(
    rows_anchor,
    "            elif row is not None:\n"
    "                rows.append(row)\n"
    "    rows = list(_deduplicate_exact_endpoint_rows(rows))\n"
    "    rows.sort(",
    1,
)
if catalog.count(OLD_INDEX) != 1:
    raise SystemExit("catalog index implementation mismatch")
catalog = catalog.replace(OLD_INDEX, NEW_INDEX, 1)
CATALOG.write_text(catalog, encoding="utf-8")

tests = TESTS.read_text(encoding="utf-8")
old_import = '''from v5_catalog_view import (  # noqa: E402
    CatalogViewError,
    compact_endpoint_catalog,
    eligible_models,
)
'''
new_import = '''from v5_catalog_view import (  # noqa: E402
    CatalogViewError,
    catalog_index,
    compact_endpoint_catalog,
    eligible_models,
)
'''
if tests.count(old_import) != 1:
    raise SystemExit("catalog test import block mismatch")
tests = tests.replace(old_import, new_import, 1)
test_anchor = "    def test_zero_endpoint_completion_is_rejected(self) -> None:\n"
if tests.count(test_anchor) != 1:
    raise SystemExit("catalog test insertion anchor mismatch")
tests = tests.replace(test_anchor, TEST_METHODS + test_anchor, 1)
TESTS.write_text(tests, encoding="utf-8")
