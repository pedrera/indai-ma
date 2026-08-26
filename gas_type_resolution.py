import re
from dataclasses import asdict, dataclass
from typing import Iterable


SUPPORTED_GAS_TYPES = (
    "natural_gas",
    "biomethane",
    "lng",
    "hydrogen_blend",
)
GAS_TYPE_LABELS = {
    "natural_gas": "Gas natural",
    "biomethane": "Biometano",
    "lng": "GNL",
    "hydrogen_blend": "Mezcla de hidrógeno",
}
GAS_ALIASES = {
    "gas natural": "natural_gas",
    "biometano": "biomethane",
    "biomethane": "biomethane",
    "gnl": "lng",
    "lng": "lng",
    "mezcla de hidrógeno": "hydrogen_blend",
    "mezcla de hidrogeno": "hydrogen_blend",
    "hydrogen blend": "hydrogen_blend",
}
GAS_PATTERN = re.compile(
    r"\b(?:gas\s+natural|biometano|biomethane|gnl|lng|"
    r"mezcla\s+de\s+hidr[oó]geno|hydrogen\s+blend)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GasTypeResolution:
    gas_type: str | None
    source: str | None
    status: str
    user_candidates: list[str]
    rag_candidates: list[str]
    conflicts: list[str]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def detect_gas_types(text: str) -> list[str]:
    return sorted(
        {
            GAS_ALIASES[" ".join(match.group(0).lower().split())]
            for match in GAS_PATTERN.finditer(text)
        }
    )


def resolve_gas_type(
    user_text: str,
    retrieved_context: Iterable[str] = (),
) -> GasTypeResolution:
    user_candidates = detect_gas_types(user_text)
    rag_candidates = sorted(
        {
            gas_type
            for text in retrieved_context
            for gas_type in detect_gas_types(text)
        }
    )
    if len(user_candidates) > 1 or len(rag_candidates) > 1:
        return GasTypeResolution(
            gas_type=None,
            source=None,
            status="ambiguous",
            user_candidates=user_candidates,
            rag_candidates=rag_candidates,
            conflicts=sorted(set(user_candidates + rag_candidates)),
        )
    if user_candidates and rag_candidates and user_candidates != rag_candidates:
        return GasTypeResolution(
            gas_type=None,
            source=None,
            status="conflict",
            user_candidates=user_candidates,
            rag_candidates=rag_candidates,
            conflicts=sorted(set(user_candidates + rag_candidates)),
        )
    if user_candidates:
        return GasTypeResolution(
            gas_type=user_candidates[0],
            source="user",
            status="resolved",
            user_candidates=user_candidates,
            rag_candidates=rag_candidates,
            conflicts=[],
        )
    if rag_candidates:
        return GasTypeResolution(
            gas_type=rag_candidates[0],
            source="rag",
            status="resolved",
            user_candidates=[],
            rag_candidates=rag_candidates,
            conflicts=[],
        )
    return GasTypeResolution(
        gas_type=None,
        source=None,
        status="unresolved",
        user_candidates=[],
        rag_candidates=[],
        conflicts=[],
    )


def gas_type_resolution_error(resolution: GasTypeResolution) -> str | None:
    if resolution.status == "unresolved":
        return "No se ha identificado el tipo de gas."
    if resolution.status == "conflict":
        user = GAS_TYPE_LABELS[resolution.user_candidates[0]]
        rag = GAS_TYPE_LABELS[resolution.rag_candidates[0]]
        return (
            "Se han detectado tipos de gas distintos en la consulta y en la "
            f"documentación: {user} y {rag}."
        )
    if resolution.status == "ambiguous":
        if len(resolution.user_candidates) > 1:
            return "Se han detectado varios tipos de gas en la consulta."
        return (
            "Se han detectado varios tipos de gas en la documentación "
            "recuperada."
        )
    return None
