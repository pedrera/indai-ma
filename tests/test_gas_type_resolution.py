import unittest

from diagnostics import PerformanceRecorder
from gas_analysis import GasAnalysisError, prepare_gas_analysis
from gas_type_resolution import (
    gas_type_resolution_error,
    resolve_gas_type,
)


class GasTypeResolutionTests(unittest.TestCase):
    def test_user_and_rag_agree_on_natural_gas(self) -> None:
        result = resolve_gas_type(
            "Analiza esta cartera de gas natural.",
            ["CONTRATO MARCO DE SUMINISTRO DE GAS NATURAL"],
        )
        self.assertEqual(result.gas_type, "natural_gas")
        self.assertEqual(result.source, "user")
        self.assertEqual(result.status, "resolved")

    def test_rag_resolves_natural_gas_when_user_omits_it(self) -> None:
        result = resolve_gas_type(
            "Consulta el contrato del Hospital Costa Sur.",
            [
                "CONTRATO MARCO DE SUMINISTRO DE GAS NATURAL. El volumen "
                "anual contratado de gas natural es de 48 GWh."
            ],
        )
        self.assertEqual(result.gas_type, "natural_gas")
        self.assertEqual(result.source, "rag")
        self.assertEqual(result.status, "resolved")

    def test_user_biomethane_conflicts_with_rag_natural_gas(self) -> None:
        result = resolve_gas_type(
            "Analiza el contrato de biometano.",
            ["El contrato establece suministro de gas natural."],
        )
        self.assertEqual(result.status, "conflict")
        self.assertIsNone(result.gas_type)
        self.assertIn("Biometano", gas_type_resolution_error(result))
        self.assertIn("Gas natural", gas_type_resolution_error(result))

    def test_unresolved_gas_has_controlled_error(self) -> None:
        result = resolve_gas_type(
            "Consulta el contrato del hospital.",
            ["El contrato establece una flexibilidad del 15 %."],
        )
        self.assertEqual(result.status, "unresolved")
        self.assertEqual(
            gas_type_resolution_error(result),
            "No se ha identificado el tipo de gas.",
        )

    def test_rag_resolves_lng(self) -> None:
        result = resolve_gas_type(
            "Consulta las condiciones contratadas.",
            ["Contrato marco para el suministro de GNL."],
        )
        self.assertEqual(result.gas_type, "lng")
        self.assertEqual(result.source, "rag")

    def test_scenario_parser_accepts_rag_resolved_gas(self) -> None:
        user_text = (
            "Hospital Costa Sur con demanda prevista de 120 GWh y 95 GWh "
            "de suministro contratado. Coste medio de suministro 32 EUR/MWh, "
            "precio medio de venta 45 EUR/MWh y precio spot 42 EUR/MWh."
        )
        resolution = resolve_gas_type(
            user_text,
            ["CONTRATO MARCO DE SUMINISTRO DE GAS NATURAL"],
        )
        recorder = PerformanceRecorder(
            "rag-gas", "lmstudio", "test", "gas_analysis"
        )
        prepared = prepare_gas_analysis(
            user_text, recorder, gas_resolution=resolution
        )
        self.assertEqual(len(prepared.scenarios), 3)
        parsing_event = next(
            event
            for event in recorder.snapshot().events
            if event.stage == "input_parsing"
        )
        diagnostic = parsing_event.metadata["parsing_result"]
        self.assertEqual(diagnostic["detected_gas_types"], ["natural_gas"])
        self.assertEqual(diagnostic["gas_type_source"], "rag")

    def test_scenario_parser_rejects_user_rag_conflict(self) -> None:
        text = (
            "Biometano con demanda prevista de 120 GWh y suministro "
            "contratado de 95 GWh. Precio de venta 45 EUR/MWh, coste de "
            "suministro 32 EUR/MWh y spot 42 EUR/MWh."
        )
        resolution = resolve_gas_type(
            text, ["Contrato de suministro de gas natural."]
        )
        recorder = PerformanceRecorder(
            "rag-conflict", "lmstudio", "test", "gas_analysis"
        )
        with self.assertRaisesRegex(
            GasAnalysisError, "tipos de gas distintos"
        ):
            prepare_gas_analysis(text, recorder, gas_resolution=resolution)


if __name__ == "__main__":
    unittest.main()
