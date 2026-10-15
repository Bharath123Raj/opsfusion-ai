import argparse
import json
from pathlib import Path
from .core import Event, fuse
from .demo import generate


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def load_events(path):
    events = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if line.strip():
            try:
                events.append(Event(**json.loads(line)))
            except (ValueError, TypeError) as error:
                raise ValueError(f'{path}:{number}: {error}') from error
    return events


def main():
    parser = argparse.ArgumentParser(description='OpsFusion evidence fusion baseline')
    sub = parser.add_subparsers(dest='command', required=True)
    demo = sub.add_parser('demo'); demo.add_argument('--seed', type=int, default=42)
    demo.add_argument('--output', type=Path, default=Path('.'))
    run = sub.add_parser('fuse'); run.add_argument('--events', type=Path, required=True)
    run.add_argument('--output', type=Path, default=Path('reports/incidents.json'))
    args = parser.parse_args()
    if args.command == 'demo':
        events, truth = generate(args.seed)
        args.output.joinpath('data').mkdir(parents=True, exist_ok=True)
        args.output.joinpath('data/events.jsonl').write_text('\n'.join(json.dumps(e.to_dict()) for e in events)+'\n', encoding='utf-8')
        write_json(args.output/'data/ground_truth.json', {'synthetic': True, 'incidents': truth})
        result = fuse(events)
        write_json(args.output/'reports/incidents.json', result)
        expected = {(e['asset_id'], e['type']) for e in truth}
        actual = {(e['asset_id'], e['type']) for e in result['incidents']}
        write_json(args.output/'reports/demo_check.json', {'scope': 'six scripted synthetic fixture scenarios; not an ML benchmark',
            'passed': expected == actual, 'expected_incidents': len(expected), 'found_incidents': len(actual),
            'missing': sorted(expected-actual), 'unexpected': sorted(actual-expected), 'seed': args.seed})
        print(f'Demo: {len(events)} events, {len(actual)} incidents. Fixture check: {"PASS" if expected == actual else "FAIL"}')
        if expected != actual:
            raise SystemExit(1)
    else:
        result = fuse(load_events(args.events))
        write_json(args.output, result)
        print(f'Wrote {len(result["incidents"])} incidents to {args.output}')

if __name__ == '__main__':
    main()
