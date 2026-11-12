"""讀取語意層定義（catalog.yaml）。"""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Fact:
    key: str
    from_: str
    valid: str
    dims: dict[str, str]
    scope: dict[str, str]
    reason: str


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    fact: str
    date: str
    kind: str
    unit: str
    decimals: int
    basis: str
    expr: str | None = None
    num: str | None = None
    den: str | None = None
    scale: float = 1
    join: str = ""
    confidential: bool = False
    target: dict | None = None
    missing: dict | None = None


@dataclass(frozen=True)
class Catalog:
    facts: dict[str, Fact]
    dims: dict[str, dict]
    metrics: dict[str, Metric] = field(default_factory=dict)


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    raw = yaml.safe_load((Path(__file__).with_name("catalog.yaml")).read_text(encoding="utf-8"))
    facts = {k: Fact(k, v["from"], v["valid"], v["dims"], v["scope"], v["reason"]) for k, v in raw["facts"].items()}
    metrics = {k: Metric(key=k, **v) for k, v in raw["metrics"].items()}
    return Catalog(facts, raw["dimensions"], metrics)
