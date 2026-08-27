import json
from functools import lru_cache
from pathlib import Path

from .schemas import BENCHMARK_DATASET_VERSION, BenchmarkCase, BenchmarkCategory

_DATASET_PATH = Path(__file__).with_name("professional_benchmarks.v1.json")


class BenchmarkCatalog:
    """Immutable, source-controlled benchmark definitions."""

    def __init__(self, cases: tuple[BenchmarkCase, ...] | None = None) -> None:
        self._cases = cases or _load_cases()
        self._by_slug = {case.slug: case for case in self._cases}
        if len(self._by_slug) != len(self._cases):
            raise ValueError("benchmark slugs must be unique")
        categories = {case.category for case in self._cases}
        if categories != set(BenchmarkCategory):
            missing = set(BenchmarkCategory) - categories
            raise ValueError(f"benchmark dataset is missing categories: {sorted(missing)}")

    def list(self, *, category: BenchmarkCategory | None = None) -> tuple[BenchmarkCase, ...]:
        return tuple(case for case in self._cases if category is None or case.category is category)

    def get(self, slug: str) -> BenchmarkCase | None:
        return self._by_slug.get(slug)


@lru_cache(maxsize=1)
def _load_cases() -> tuple[BenchmarkCase, ...]:
    document = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    if document.get("dataset_version") != BENCHMARK_DATASET_VERSION:
        raise ValueError("benchmark dataset version does not match its schema")
    cases = tuple(BenchmarkCase.model_validate(item) for item in document.get("cases", []))
    if not cases:
        raise ValueError("benchmark dataset cannot be empty")
    return cases


__all__ = ["BenchmarkCatalog"]
