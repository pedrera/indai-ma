import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from streamlit.testing.v1 import AppTest

from comparison_store import ComparisonStore
from diagnostics import PerformanceRecorder
from execution_metrics import build_execution_comparison_record


class BenchmarkHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.sqlite3"

    def app(self):
        return AppTest.from_string(
            "from pathlib import Path\n"
            "from benchmark_ui import render_benchmark_history\n"
            f"render_benchmark_history(Path({str(self.path)!r}))\n",
            default_timeout=15,
        ).run()

    def save(self, batch_id, mode, case):
        store = ComparisonStore(self.path)
        recorder = PerformanceRecorder(batch_id + mode + case, "fake", "model", mode)
        recorder.finish("completed")
        store.append(build_execution_comparison_record(recorder.snapshot(), "SHORT"),
                     batch_id=batch_id, case_id=case, repetition=1,
                     provider="fake", model="model", config={})

    def test_empty_history_does_not_create_database(self):
        app = self.app()
        self.assertFalse(app.exception)
        self.assertIn("Todavía no hay", app.info[0].value)
        self.assertFalse(self.path.exists())

    def test_batches_filters_and_metrics_render(self):
        self.save("first", "deterministic", "short")
        self.save("second", "deterministic", "short")
        self.save("second", "planner_agent", "long")
        batches = ComparisonStore(self.path).list_batches()
        self.assertTrue(all(batch["first_saved_at"] for batch in batches))
        self.assertEqual([{k: b[k] for k in ("batch_id", "run_count")} for b in batches], [
            {"batch_id": "second", "run_count": 2},
            {"batch_id": "first", "run_count": 1},
        ])
        app = self.app()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox[0].value, "second")
        self.assertEqual(len(app.dataframe[0].value), 2)
        self.assertEqual(app.dataframe[1].value.iloc[0]["Motivos de validación"], "No registrados")
        app.multiselect[0].set_value(["short"]).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe[0].value), 1)
        self.assertEqual(app.dataframe[0].value.iloc[0]["Completadas"], 1)
        app.multiselect[1].set_value(["planner_agent"]).run()
        self.assertIn("No hay ejecuciones", app.info[0].value)

    def test_latest_button_switches_from_previous_batch(self):
        self.save("first", "deterministic", "short")
        app = self.app()
        self.save("second", "deterministic", "short")
        app.button(key="benchmark_refresh").click().run()
        self.assertEqual(app.selectbox[0].value, "first")
        app.button(key="benchmark_latest").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox[0].value, "second")
        self.assertIn("Más reciente", app.selectbox[0].options[0])

    def test_corrupt_database_is_controlled(self):
        self.path.write_text("not a sqlite database", encoding="utf-8")
        app = self.app()
        self.assertFalse(app.exception)
        self.assertIn("No se pudo leer", app.error[0].value)


if __name__ == "__main__":
    unittest.main()
