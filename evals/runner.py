import json
import math
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from diagnostics import PerformanceRecorder
from supervisor import Supervisor, event_counts
from evals.models import EvaluationCase, EvaluationResult, ValueCheck, EvaluationSummary
from alternative_evaluation import AlternativeEvaluationInputs

CASE_DIR = Path(__file__).parent / 'cases'


def load_cases(directory=CASE_DIR, category=None, mode='fast'):
    cases, ids = [], set()
    for path in sorted(Path(directory).glob('*.jsonl')):
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            try:
                case = EvaluationCase.model_validate_json(line)
                if case.case_id in ids:
                    raise ValueError('Duplicate case_id')
            except ValueError as error:
                raise ValueError(f'{path.name}:{number}: invalid evaluation case: {error}') from error
            ids.add(case.case_id)
            if (category is None or case.category == category) and (mode != 'fast' or 'model_dependent' not in case.tags):
                cases.append(case)
    return cases


def result_view(output):
    """Index existing values by name; no derived business values."""
    view = {'status': output.status.value, 'content': output.content}
    raw_output = output.model_dump(mode='json')
    if raw_output.get('recommendation') is not None:
        view['recommendation'] = raw_output['recommendation']
    for item in output.specialist_results:
        if item.result is None:
            continue
        raw = raw_output['specialist_results'][output.specialist_results.index(item)]['result']
        raw['tools'] = {t['name']: t for t in raw.get('tool_executions', [])}
        raw['facts'] = {f['name']: f['value'] for f in raw.get('contract_facts', [])}
        comparison = raw.get('comparison')
        if comparison:
            raw['comparison_by_customer'] = {
                c.get('customer') or c.get('document_name'): {f['name']: f['value'] for f in c.get('facts', [])}
                for c in comparison.get('contracts', [])
            }
            raw['comparison_by_contract'] = {
                f'contract_{i}': {f['name']: f['value'] for f in c.get('facts', [])}
                for i, c in enumerate(sorted(comparison.get('contracts', []), key=lambda c: c.get('document_name', '')))
            }
        view[item.agent_name] = raw
    return view


def lookup(view, field):
    for key in field.split('.'):
        view = view[int(key)] if isinstance(view, list) else view[key]
    return view


def compare_value(expected, actual, absolute=1e-6, relative=1e-9):
    if type(expected) in (int, float):
        return type(actual) in (int, float) and math.isfinite(actual) and math.isclose(expected, actual, abs_tol=absolute, rel_tol=relative)
    return type(expected) is type(actual) and expected == actual


def evaluate(case, output, recorder, latency_ms):
    snapshot = recorder.snapshot()
    actual_agents = [e.metadata['agent_name'] for e in snapshot.events if e.stage == 'specialist_execution']
    result = EvaluationResult(case_id=case.case_id, category=case.category, operation_id=snapshot.operation_id,
        execution_status=output.status.value, actual_agents=actual_agents, latency_ms=latency_ms,
        input_allowed=output.input_guardrails.passed, warnings=output.warnings,
        execution=output.model_dump(mode='json'), trace=json.loads(json.dumps(asdict(snapshot))))
    counts = event_counts(snapshot.events)
    result.llm_calls, result.rag_calls, result.tool_calls = counts['llm_calls'], counts['rag_calls'], counts['tool_calls']
    if case.expected_agents is not None:
        result.routing_correct = actual_agents == case.expected_agents and output.routing.selected_agents == case.expected_agents
        result.false_agent_invocations = len(set(actual_agents) - set(case.expected_agents))
        result.missing_agent_invocations = len(set(case.expected_agents) - set(actual_agents))
    if set(actual_agents) & set(case.forbidden_agents):
        result.errors.append('Forbidden agent invoked')
    view = result_view(output)
    for expectation in case.expected_values:
        try:
            actual = lookup(view, expectation.field)
            passed = compare_value(expectation.expected, actual, expectation.absolute_tolerance, expectation.relative_tolerance)
        except (KeyError, IndexError, TypeError, ValueError):
            actual, passed = None, False
        result.value_checks.append(ValueCheck(**expectation.model_dump(), actual=actual, passed=passed))
    commercial = view.get('CommercialAgent', {})
    evidence = [*commercial.get('contract_facts', []), *commercial.get('commercial_findings', [])]
    for expected in case.expected_sources:
        result.source_checks.append(any(e['source']['document_name'] == expected.document_name
            and expected.section_contains.casefold() in (e['source'].get('section') or '').casefold()
            and expected.evidence_contains.casefold() in e['evidence'].casefold() for e in evidence))
    names = {e.metadata.get('tool_name') for e in snapshot.events if e.stage == 'tool_execution' and e.metadata.get('tool_call_count')}
    if case.expected_tool_names is not None:
        result.tool_checks = names == set(case.expected_tool_names)
    result.llm_call_check = result.llm_calls <= case.expected_max_llm_calls
    if case.expected_rag_calls is not None:
        result.rag_call_check = result.rag_calls == case.expected_rag_calls
    codes = {v.code for guard in (output.input_guardrails, output.output_guardrails) for v in [*guard.violations, *guard.warnings]}
    codes.update(commercial.get('warning_codes', []))
    result.guardrail_checks = (result.input_allowed == case.expected_input_allowed and
        set(case.expected_warning_codes) <= codes and output.output_guardrails.passed)
    result.unsupported_answer_failure = any(s.casefold() in output.content.casefold() for s in case.forbidden_content)
    checks = {'routing': result.routing_correct, 'tools': result.tool_checks, 'LLM budget': result.llm_call_check,
              'RAG count': result.rag_call_check, 'guardrails': result.guardrail_checks,
              'unsupported answer': not result.unsupported_answer_failure}
    if case.expected_status is not None:
        checks['execution status'] = result.execution_status == case.expected_status
    result.errors += [name for name, passed in checks.items() if passed is False]
    result.errors += ['value: ' + c.field for c in result.value_checks if not c.passed]
    result.errors += [f'source: {i}' for i, passed in enumerate(result.source_checks) if not passed]
    result.passed = not result.errors
    return result


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent / 100
    lower, upper = math.floor(index), math.ceil(index)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def summarize(results):
    def rate(passed, total):
        return passed / total if total else None
    values = [v for r in results for v in r.value_checks if type(v.expected) in (int, float)]
    sources = [v for r in results for v in r.source_checks]
    routing = [r.routing_correct for r in results if r.routing_correct is not None]
    times = [r.latency_ms for r in results]
    n, passed = len(results), sum(r.passed for r in results)
    return EvaluationSummary(total_cases=n, passed_cases=passed, failed_cases=n-passed, pass_rate=rate(passed,n),
        routing_accuracy=rate(sum(routing),len(routing)),
        false_agent_invocations=sum(r.false_agent_invocations for r in results),
        missing_agent_invocations=sum(r.missing_agent_invocations for r in results),
        numeric_assertions_total=len(values), numeric_assertions_passed=sum(v.passed for v in values),
        calculation_accuracy=rate(sum(v.passed for v in values),len(values)),
        source_assertions_total=len(sources), source_assertions_passed=sum(sources),
        unsupported_answer_failures=sum(r.unsupported_answer_failure for r in results),
        average_latency_ms=rate(sum(times),n), p50_latency_ms=percentile(times,50), p95_latency_ms=percentile(times,95),
        max_latency_ms=max(times) if times else None, total_llm_calls=sum(r.llm_calls for r in results),
        average_llm_calls_per_case=rate(sum(r.llm_calls for r in results),n),
        total_rag_calls=sum(r.rag_calls for r in results), total_tool_calls=sum(r.tool_calls for r in results),
        failed_operations=sum(r.execution_status in {'error','failed','validation_failed'} for r in results),
        blocked_inputs=sum(not r.input_allowed for r in results), allowed_inputs=sum(r.input_allowed for r in results),
        guardrail_failures=sum(not r.guardrail_checks for r in results))


class EvaluationRunner:
    def __init__(self, supervisor_factory):
        self.supervisor_factory = supervisor_factory

    def run(self, cases):
        results = []
        for case in cases:
            recorder = PerformanceRecorder(uuid4().hex[:12], 'offline', 'none', 'supervisor')
            started = perf_counter()
            try:
                evaluation_inputs = None
                if case.alternative_evaluation is not None:
                    evaluation_inputs = AlternativeEvaluationInputs(
                        coverage_volume_gwh=case.alternative_evaluation.coverage_volume_gwh,
                        coverage_price_eur_mwh=case.alternative_evaluation.coverage_price_eur_mwh,
                        revised_remaining_forecast_consumption_gwh=case.alternative_evaluation.revised_remaining_forecast_consumption_gwh,
                    )
                output = self.supervisor_factory(case, recorder).run(
                    case.query, 120, evaluation_inputs=evaluation_inputs
                )
                recorder.finish(output.status.value)
                result = evaluate(case, output, recorder, (perf_counter()-started)*1000)
            except Exception as error:
                recorder.finish('failed')
                counts = event_counts(recorder.snapshot().events)
                result = EvaluationResult(case_id=case.case_id, category=case.category, operation_id=recorder.operation_id,
                    status='error', errors=[f'{type(error).__name__}: {error}'], latency_ms=(perf_counter()-started)*1000,
                    llm_calls=counts['llm_calls'], rag_calls=counts['rag_calls'], tool_calls=counts['tool_calls'],
                    trace=json.loads(json.dumps(asdict(recorder.snapshot()))))
            results.append(result)
        return results


def write_report(results, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    summary = summarize(results)
    payload = {'dataset_version':'0.9', 'profile':'offline_fixture_embeddings', 'summary':summary.model_dump(),
               'results':[r.model_dump(mode='json') for r in results]}
    (directory/'evaluation.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Evaluation Summary', '', 'Profile: offline fixtures; latency excludes index preparation.', '',
             *[f'- {key}: {value}' for key,value in summary.model_dump().items()], '', '## Failures', '']
    lines += [f'- {r.case_id}: {"; ".join(r.errors)}' for r in results if not r.passed] or ['None.']
    (directory/'evaluation.md').write_text('\n'.join(lines),encoding='utf-8')
    return summary
