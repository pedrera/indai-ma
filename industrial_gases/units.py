"""Controlled operational unit catalog for Industrial Gases."""
from dataclasses import dataclass
from enum import Enum


class UnitDimension(str, Enum):
    MASS = "MASS"
    LIQUID_VOLUME = "LIQUID_VOLUME"
    GAS_VOLUME = "GAS_VOLUME"
    NORMALIZED_GAS_VOLUME = "NORMALIZED_GAS_VOLUME"


@dataclass(frozen=True)
class UnitSpec:
    code: str
    dimension: UnitDimension


UNIT_CATALOG = {
    "kg": UnitSpec("kg", UnitDimension.MASS),
    "t": UnitSpec("t", UnitDimension.MASS),
    "L": UnitSpec("L", UnitDimension.LIQUID_VOLUME),
    "m3": UnitSpec("m3", UnitDimension.LIQUID_VOLUME),
    "Nm3": UnitSpec("Nm3", UnitDimension.NORMALIZED_GAS_VOLUME),
    "Sm3": UnitSpec("Sm3", UnitDimension.NORMALIZED_GAS_VOLUME),
}


def unit_spec(code: str) -> UnitSpec:
    try:
        return UNIT_CATALOG[code]
    except (KeyError, TypeError) as error:
        raise ValueError(f"unsupported operational unit: {code!r}") from error
