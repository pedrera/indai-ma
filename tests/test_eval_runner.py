from types import SimpleNamespace
from unittest.mock import patch

from evals.models import EvaluationCase
from evals.runner import EvaluationRunner
from alternative_evaluation import AlternativeEvaluationInputs


def _case(**kwargs):
    values = {'case_id': 'fixture_case', 'name': 'fixture', 'category': 'supervisor', 'query': 'q'}
    values.update(kwargs)
    return EvaluationCase(**values)


def test_evaluation_case_supports_optional_alternative_inputs_and_zero_values():
    assert _case().alternative_evaluation is None
    scenario = _case(alternative_evaluation={'coverage_volume_gwh': 0, 'coverage_price_eur_mwh': 0})
    assert scenario.alternative_evaluation.coverage_volume_gwh == 0
    assert scenario.alternative_evaluation.coverage_price_eur_mwh == 0


def test_evaluation_case_rejects_extra_fields():
    import pytest
    with pytest.raises(ValueError):
        _case(alternative_evaluation={'unexpected': 1})
    with pytest.raises(ValueError):
        EvaluationCase.model_validate({**_case().model_dump(), 'unexpected': 1})


def test_runner_maps_scenario_inputs_and_preserves_legacy_none():
    calls = []

    class FakeSupervisor:
        def run(self, query, timeout, *, evaluation_inputs=None):
            calls.append(evaluation_inputs)
            return SimpleNamespace(status=SimpleNamespace(value='completed'))

    runner = EvaluationRunner(lambda case, recorder: FakeSupervisor())
    with patch('evals.runner.evaluate', return_value=SimpleNamespace()):
        runner.run([_case(), _case(case_id='scenario_case', alternative_evaluation={
            'coverage_volume_gwh': 0.3, 'coverage_price_eur_mwh': 40
        })])

    assert calls[0] is None
    assert calls[1] == AlternativeEvaluationInputs(0.3, 40)


def test_runner_maps_volume_only_and_price_only_without_query_parsing():
    calls = []

    class FakeSupervisor:
        def run(self, query, timeout, *, evaluation_inputs=None):
            calls.append(evaluation_inputs)
            return SimpleNamespace(status=SimpleNamespace(value='completed'))

    runner = EvaluationRunner(lambda case, recorder: FakeSupervisor())
    cases = [
        _case(case_id='volume_only', query='spot +20%', alternative_evaluation={'coverage_volume_gwh': 0.3}),
        _case(case_id='price_only', query='sin datos', alternative_evaluation={'coverage_price_eur_mwh': 40}),
    ]
    with patch('evals.runner.evaluate', return_value=SimpleNamespace()):
        runner.run(cases)

    assert calls == [AlternativeEvaluationInputs(0.3, None), AlternativeEvaluationInputs(None, 40)]
