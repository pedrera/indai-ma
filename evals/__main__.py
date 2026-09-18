import argparse
import logging
from pathlib import Path
from evals.offline import OfflineEnvironment
from evals.runner import EvaluationRunner, load_cases, write_report


def main():
    parser = argparse.ArgumentParser(description='Local deterministic evaluations')
    parser.add_argument('--mode', choices=['fast','full'], default='fast')
    parser.add_argument('--category', choices=['procurement','commercial','risk','supervisor','guardrails'])
    parser.add_argument('--output', type=Path, default=Path('evals/results/latest'))
    args = parser.parse_args()
    cases = load_cases(category=args.category, mode=args.mode)
    logging.getLogger('indai_ma.performance').setLevel(logging.WARNING)
    with OfflineEnvironment() as env:
        results = EvaluationRunner(env.supervisor).run(cases)
    summary = write_report(results, args.output)
    print(summary.model_dump_json(indent=2))
    for result in results:
        if not result.passed:
            print(result.case_id + ': ' + '; '.join(result.errors))
    return 1 if summary.failed_cases else 0


if __name__ == '__main__':
    raise SystemExit(main())
