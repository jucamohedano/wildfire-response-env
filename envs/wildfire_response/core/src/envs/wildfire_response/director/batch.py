"""Serial mixed-case model evaluation; stop immediately on provider or local limits."""
import argparse
import json
from pathlib import Path

from ..core.scenarios import get_task
from ..eval.run import dump
from .policy import DEFAULT_MODEL, OpenRouterTransport, ProviderHTTPError
from .run import run

SCENARIOS = ('train-009', 'train-010', 'train-007', 'train-011', 'train-006', 'train-008')


class RequestBudgetExceeded(RuntimeError):
    pass


class BudgetTransport:
    def __init__(self, transport, max_requests):
        if type(max_requests) is not int or not 1 <= max_requests <= 64:
            raise ValueError('Batch request budget must be from 1 to 64')
        self.transport, self.max_requests, self.requests = transport, max_requests, 0

    def __call__(self, payload):
        if self.requests >= self.max_requests:
            raise RequestBudgetExceeded('Local batch request budget reached; not a provider limit')
        self.requests += 1
        return self.transport(payload)


def batch(output, max_requests=24, transport=None, scenarios=SCENARIOS):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh batch output directory')
    tasks = [get_task(identifier) for identifier in scenarios]
    if not tasks or any(task['split'] != 'train' for task in tasks):
        raise ValueError('The exploratory batch accepts training cases only')
    underlying = transport if transport is not None else OpenRouterTransport()
    budget = BudgetTransport(underlying, max_requests)
    output.mkdir(parents=True)
    result = dict(status='running', model=DEFAULT_MODEL, profile='risk-aware',
                  max_requests=max_requests, requests=0, completed=[], planned=list(scenarios),
                  provider_status=None, stop_reason=None,
                  limitations='Exploratory training-only prompt evaluation, not weight training or a held-out ranking')
    dump(output / 'summary.json', result)
    for task in tasks:
        identifier = task['id']
        print(json.dumps(dict(starting=identifier, family=task['family'], requests_remaining=max_requests-budget.requests)), flush=True)
        try:
            summary = run(output / identifier, identifier, decisions=32, transport=budget, profile='risk-aware')
            result['completed'].append(summary)
        except ProviderHTTPError as error:
            result.update(status='stopped', provider_status=error.status_code,
                          stop_reason='provider_limit' if error.status_code in (402, 429) else 'provider_access_or_service_error',
                          stopped_scenario=identifier)
        except RequestBudgetExceeded:
            result.update(status='stopped', stop_reason='local_request_budget', stopped_scenario=identifier)
        except (RuntimeError, ValueError) as error:
            result.update(status='stopped', stop_reason='transport_or_runtime_error', stopped_scenario=identifier,
                          error_type=type(error).__name__)
        result['requests'] = budget.requests
        dump(output / 'summary.json', result)
        if result['status'] == 'stopped':
            print(json.dumps(dict(status='stopped', reason=result['stop_reason'],
                                  provider_status=result['provider_status'], requests=budget.requests)), flush=True)
            return result
    result['status'] = 'complete'
    dump(output / 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-requests', type=int, default=24)
    args = parser.parse_args()
    result = batch(args.output, args.max_requests)
    if result['status'] == 'stopped':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
