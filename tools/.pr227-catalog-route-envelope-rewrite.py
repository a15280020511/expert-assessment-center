from __future__ import annotations

from pathlib import Path

CATALOG = Path("open-model-market/v5_catalog_view.py")
TESTS = Path("tests/test_v5_catalog_endpoint_eligibility.py")

NEW_HELPERS = '''_ROUTE_IDENTITY_FIELDS = (
    "model",
    "company",
    "official_intelligence_rank",
    "provider",
    "provider_endpoint",
    "input_modalities",
    "output_modalities",
)
_ROUTE_VARIABLE_FIELDS = (
    "context_length",
    "max_completion_tokens",
    "prompt_price_per_million",
    "completion_price_per_million",
    "supported_parameters",
    "synthetic_fixture_only",
)


def _canonical_endpoint_row(row: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _merge_provider_route_rows(
    existing: Mapping[str, Any],
    candidate: Mapping[str, Any],
    key: tuple[str, str],
) -> Mapping[str, Any]:
    expected_fields = set(_ROUTE_IDENTITY_FIELDS) | set(_ROUTE_VARIABLE_FIELDS)
    actual_fields = set(existing) | set(candidate)
    if actual_fields != expected_fields:
        raise CatalogViewError(
            f"unhandled duplicate provider route fields: {key}"
        )
    for field in _ROUTE_IDENTITY_FIELDS:
        if existing.get(field) != candidate.get(field):
            raise CatalogViewError(
                f"conflicting duplicate provider route identity: {key}/{field}"
            )
    supported = sorted(
        {
            str(value)
            for value in existing.get("supported_parameters", [])
        }
        & {
            str(value)
            for value in candidate.get("supported_parameters", [])
        }
    )
    if not OUTPUT_LIMIT_PARAMETERS.intersection(
        {value.casefold() for value in supported}
    ):
        raise CatalogViewError(
            f"conflicting duplicate provider route capabilities: {key}"
        )
    merged = dict(existing)
    merged.update(
        {
            "context_length": min(
                int(existing["context_length"]),
                int(candidate["context_length"]),
            ),
            "max_completion_tokens": min(
                int(existing["max_completion_tokens"]),
                int(candidate["max_completion_tokens"]),
            ),
            "prompt_price_per_million": max(
                float(existing["prompt_price_per_million"]),
                float(candidate["prompt_price_per_million"]),
            ),
            "completion_price_per_million": max(
                float(existing["completion_price_per_million"]),
                float(candidate["completion_price_per_million"]),
            ),
            "supported_parameters": supported,
            "synthetic_fixture_only": bool(
                existing.get("synthetic_fixture_only")
                or candidate.get("synthetic_fixture_only")
            ),
        }
    )
    return merged


def _coalesce_provider_route_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    order: list[tuple[str, str]] = []
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
            order.append(key)
            continue
        if _canonical_endpoint_row(existing) == _canonical_endpoint_row(row):
            continue
        observed[key] = _merge_provider_route_rows(existing, row, key)
    return [observed[key] for key in order]


'''

NEW_TESTS = '''    def test_conflicting_route_variants_use_conservative_envelope(self) -> None:
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
            "context_length": 131_072,
            "max_completion_tokens": 8_192,
            "supported_parameters": ["max_tokens"],
            "pricing": {
                "prompt": "0.000002",
                "completion": "0.000004",
            },
        }
        catalog = compact_endpoint_catalog(
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
        self.assertEqual(1, len(catalog["endpoints"]))
        row = catalog["endpoints"][0]
        self.assertEqual(131_072, row["context_length"])
        self.assertEqual(8_192, row["max_completion_tokens"])
        self.assertEqual(["max_tokens"], row["supported_parameters"])
        self.assertEqual(2.0, row["prompt_price_per_million"])
        self.assertEqual(4.0, row["completion_price_per_million"])

    def test_duplicate_route_identity_conflict_fails_closed(self) -> None:
        model = self.model(model_maximum=262_144)
        catalog = compact_endpoint_catalog(
            [model],
            {
                model.id: {
                    "data": {
                        "endpoints": [
                            {
                                "tag": "together",
                                "context_length": 1_048_576,
                                "max_completion_tokens": 262_144,
                                "supported_parameters": ["max_tokens"],
                                "pricing": {
                                    "prompt": "0.000001",
                                    "completion": "0.000003",
                                },
                            }
                        ]
                    }
                }
            },
            required_context_tokens=16_384,
        )
        row = catalog["endpoints"][0]
        conflicting = {**row, "company": "different-company"}
        with self.assertRaisesRegex(
            CatalogViewError,
            "conflicting duplicate provider route identity",
        ):
            catalog_index({"endpoints": [row, conflicting]})

'''

catalog = CATALOG.read_text(encoding="utf-8")
start = catalog.find("def _canonical_endpoint_row(")
end = catalog.find("def compact_endpoint_catalog(", start)
if start < 0 or end < 0:
    raise SystemExit("catalog duplicate helper region is missing")
region = catalog[start:end]
if "def _deduplicate_exact_endpoint_rows(" not in region:
    raise SystemExit("expected exact-dedup implementation is missing")
catalog = catalog[:start] + NEW_HELPERS + catalog[end:]
catalog = catalog.replace(
    "_deduplicate_exact_endpoint_rows(rows)",
    "_coalesce_provider_route_rows(rows)",
)
if "_deduplicate_exact_endpoint_rows" in catalog:
    raise SystemExit("old exact-dedup symbol remains")
CATALOG.write_text(catalog, encoding="utf-8")

tests = TESTS.read_text(encoding="utf-8")
test_start = tests.find(
    "    def test_conflicting_exact_endpoint_rows_fail_closed(self) -> None:\n"
)
test_end = tests.find(
    "    def test_zero_endpoint_completion_is_rejected(self) -> None:\n",
    test_start,
)
if test_start < 0 or test_end < 0:
    raise SystemExit("catalog conflict test region is missing")
tests = tests[:test_start] + NEW_TESTS + tests[test_end:]
TESTS.write_text(tests, encoding="utf-8")
