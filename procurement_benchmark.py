"""Explicit CLI benchmark; importing this module never calls a provider."""

import argparse
import json
import math
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path
from statistics import median
from uuid import uuid4

from comparison_store import ComparisonStore, DEFAULT_STORE
from diagnostics import PerformanceRecorder
from execution_metrics import build_execution_comparison_record
from llm_client import get_llm_provider
from procurement_agent import ProcurementAgent, ProviderDecisionModel
from procurement_deterministic import ProcurementDeterministicWorkflow, ProviderSynthesisModel
from procurement_planner import ProcurementAgentPlanner, ProviderPlannerModel
from runtime_config import LLMRuntimeConfig


MODES = ("deterministic", "planner_agent", "react_agent")
CASES = {
    "short_spot": "Demanda de gas natural de 120 GWh, suministro contratado de 95 GWh y precio spot de 42 EUR/MWh.",
    "short_no_spot": "Demanda de gas natural de 120 GWh y suministro contratado de 95 GWh.",
    "long": "Demanda de gas natural de 90 GWh, suministro contratado de 100 GWh y precio spot de 42 EUR/MWh.",
    "balanced": "Demanda de gas natural de 100 GWh, suministro contratado de 100 GWh y precio spot de 42 EUR/MWh.",
    "scenario": "Demanda de gas natural de 120 GWh, suministro contratado de 95 GWh y precio spot de 42 EUR/MWh. Evalúa un escenario de demanda +10%.",
}


def make_runner(mode, provider, recorder):
    runner, adapter = {
        "deterministic": (ProcurementDeterministicWorkflow, ProviderSynthesisModel),
        "planner_agent": (ProcurementAgentPlanner, ProviderPlannerModel),
        "react_agent": (ProcurementAgent, ProviderDecisionModel),
    }[mode]
    return runner(adapter(provider), recorder=recorder)


def run_benchmark(store, *, provider_name, model, config, repetitions=3,
                  modes=MODES, cases=None, provider_factory=get_llm_provider,
                  runner_factory=make_runner, progress=print):
    if repetitions < 1 or not modes or any(mode not in MODES for mode in modes):
        raise ValueError("Choose positive repetitions and supported modes.")
    cases = CASES if cases is None else cases
    if not cases:
        raise ValueError("At least one case is required.")
    batch_id = uuid4().hex
    progress(f"Batch: {batch_id}; runs: {repetitions * len(modes) * len(cases)}")
    for repetition in range(1, repetitions + 1):
        # Rotate order to reduce systematic first-mode warm-up bias.
        offset = (repetition - 1) % len(modes)
        ordered_modes = tuple(modes[offset:]) + tuple(modes[:offset])
        for case_id, request in cases.items():
            for mode in ordered_modes:
                recorder = PerformanceRecorder(uuid4().hex, provider_name, model, mode)
                status, reason, content = "failed", "execution_failed", ""
                try:
                    provider = provider_factory(provider_name, model, recorder=recorder,
                                                runtime_config=config)
                    result = runner_factory(mode, provider, recorder).run(
                        request, config.timeout_seconds
                    )
                    status, reason, content = result.status.value, result.termination_reason, result.content
                except Exception as error:
                    # Do not persist raw exceptions, which can contain endpoint secrets.
                    reason = type(error).__name__
                finally:
                    recorder.finish(status)
                snapshot = recorder.snapshot()
                record = replace(build_execution_comparison_record(snapshot, content),
                                 termination_reason=reason)
                validation_events = [
                    e for e in snapshot.events
                    if e.stage == "final_response_validation"
                ]
                store.append(
                    record, batch_id=batch_id, case_id=case_id, repetition=repetition,
                    provider=provider_name, model=model, config=asdict(config),
                    skipped_actions=sum(e.metadata.get("observation_type") == "tool_skipped"
                                        for e in snapshot.events),
                    fallback=any(e.stage == "final_response_validation" and
                                 e.metadata.get("deterministic_fallback") for e in snapshot.events),
                    plan_failed=reason == "plan_validation_failed",
                    validation_reasons=(
                        [reason for e in validation_events
                         for reason in e.metadata["validation_reasons"]]
                        if validation_events and all(
                            "validation_reasons" in e.metadata for e in validation_events
                        ) else None
                    ),
                )
                progress(f"{case_id} / {mode} / {repetition}: {status}")
    return batch_id


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["case_id"], row["record"]["mode"], row["provider"],
                row["model"], json.dumps(row["config"], sort_keys=True))].append(row)
    summary = []
    for (case_id, mode, provider, model, config), runs in sorted(groups.items()):
        completed = [r for r in runs if r["record"]["status"] == "completed"]
        times = sorted(r["record"]["total_wall_time"] for r in completed)
        summary.append({
            "case_id": case_id, "mode": mode, "provider": provider, "model": model,
            "config": json.loads(config), "runs": len(runs),
            "completed": len(completed), "not_completed": len(runs) - len(completed),
            "plan_failures": sum(r["plan_failed"] for r in runs),
            "fallbacks": sum(r["fallback"] for r in runs),
            "skipped_actions": sum(r["skipped_actions"] for r in runs),
            "llm_calls": sum(r["record"]["llm_call_count"] for r in runs),
            "median_seconds_completed": median(times) if times else None,
            "p95_seconds_completed": times[math.ceil(.95 * len(times)) - 1] if times else None,
        })
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--report", metavar="BATCH_ID", help="Read a saved batch without LLM calls")
    parser.add_argument("--execute", action="store_true", help="Explicitly enable LLM requests")
    parser.add_argument("--provider", choices=("lmstudio", "openai"))
    parser.add_argument("--model")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--cases", nargs="+", choices=tuple(CASES), default=list(CASES))
    args = parser.parse_args()
    if args.report:
        rows = ComparisonStore(args.database).read(args.report)
        if not rows:
            parser.error("No runs found for this batch.")
        print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))
        return
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    modes, cases = tuple(dict.fromkeys(args.modes)), {k: CASES[k] for k in args.cases}
    print(f"Planned executions: {args.repetitions * len(modes) * len(cases)}")
    if not args.execute:
        print("Dry run. Use --execute --provider PROVIDER --model MODEL to call the LLM.")
        return
    if not args.provider or not args.model:
        parser.error("--execute requires explicit --provider and --model")
    batch_id = run_benchmark(ComparisonStore(args.database), provider_name=args.provider,
                             model=args.model, config=LLMRuntimeConfig.from_environment(),
                             repetitions=args.repetitions, modes=modes, cases=cases)
    print(f"Report: python procurement_benchmark.py --database {args.database} --report {batch_id}")


if __name__ == "__main__":
    main()
