"""Build the evidence viewer; --refresh-evidence exports private AWS artifacts.

No AWS calls or third-party dependencies. Public evidence uses an explicit field
allowlist. The default rebuilds HTML from committed evidence.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs' / 'evidence'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')


def flatten_span(span, parent=None):
    yield {
        'id': span['id'], 'parent_id': span.get('parent_id', parent),
        'name': span['name'], 'start': span['start_time'],
        'end': span.get('end_time', span['start_time']),
    }
    for child in span.get('subsegments', []):
        yield from flatten_span(child, span['id'])


def refresh_evidence():
    private = ROOT / 'artifacts'
    live = read_json(private / 'live-verification.json')
    names = ['Return request', 'Premium policy', 'Discount arithmetic', 'Account tier', 'Order status']
    questions = [
        'I want to return order ORD-27176 because the left speaker is defective.',
        'What is the return policy for Premium customers?',
        'How much are 5 items at $29.99 with 10% off?',
        'What is my current tier? Am I Premium?',
        'What is the status of order ORD-28001?',
    ]
    if len(live) != 5 or not all(row['routing_passed'] and row['routing_tools_traced'] for row in live):
        raise ValueError('Expected five successfully traced, verified scenarios.')
    scenarios = []
    for index, row in enumerate(live):
        public = {key: row[key] for key in (
            'trace_id', 'route_history', 'routing_passed', 'response', 'workflow_version',
            'memory_event_count', 'span_count', 'routing_tools_traced')}
        public.update(name=names[index], question=questions[index])
        scenarios.append(public)
    policy = live[1]
    trace_path = private / 'traces' / (policy['trace_id'] + '.json')
    trace = read_json(trace_path)
    spans = {}
    for item in trace['Spans']:
        for span in flatten_span(json.loads(item['Document'])):
            previous = spans.get(span['id'])
            if not previous or (not previous['parent_id'] and span['parent_id']):
                spans[span['id']] = span
    ordered = sorted(spans.values(), key=lambda item: item['start'])
    captured = datetime.fromtimestamp(ordered[0]['start'], timezone.utc).isoformat()
    write_json(EVIDENCE / 'policy-trace.json', {
        'trace_id': policy['trace_id'], 'captured_at': captured,
        'source_sha256': hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        'description': 'X-Ray span names, hierarchy and timestamps. Account metadata and span payloads omitted.',
        'duration_seconds': trace['Duration'], 'spans': ordered,
        'retriever_intervals': policy['retriever_intervals'],
        'retrievers_ran_concurrently': policy['retrievers_ran_concurrently'],
    })
    raw = (private / 'test-results.txt').read_bytes()
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    grader = re.sub(r'\x1b\[[0-9;]*m', '', raw.decode(encoding))
    if '120/120' not in grader:
        raise ValueError('The captured grader output does not show 120/120.')
    for row in read_json(private / 'knowledge-bases.json').values():
        grader = grader.replace(row['kb_id'], '[KB ID omitted]')
    grader = re.sub(r'arn:aws[^\s]+', '[AWS ARN omitted]', grader)
    grader = re.sub(r'\b\d{12}\b', '[AWS account omitted]', grader)
    grader = '\n'.join(line.rstrip() for line in grader.splitlines()).strip() + '\n'
    (EVIDENCE / 'grader-output.txt').write_text(
        'Captured AWS grader output, 2026-10-24 UTC. ANSI formatting removed; resource IDs omitted.\n' + grader,
        encoding='utf-8', newline='\n')
    guardrails = read_json(private / 'guardrail-results.json')
    if not all(check['passed'] for check in guardrails):
        raise ValueError('Captured guardrail evidence includes a failed check.')
    write_json(EVIDENCE / 'guardrail-results.json', guardrails)
    write_json(EVIDENCE / 'knowledge-bases.json', {
        domain: {key: row[key] for key in ('source_files', 'statistics', 'retrieval_scores')}
        for domain, row in read_json(private / 'knowledge-bases.json').items()
    })
    write_json(EVIDENCE / 'verification.json', {
        'captured_at': captured, 'region': 'us-east-1',
        'evidence_type': 'Deployed AWS run using synthetic customer and order fixtures',
        'grader': {'earned': 120, 'possible': 120,
                   'source_sha256': hashlib.sha256((ROOT / 'tests' / 'test_agent.py').read_bytes()).hexdigest()},
        'guardrail_checks_passed': len(guardrails),
        'total_spans': sum(row['span_count'] for row in scenarios), 'scenarios': scenarios,
    })
    print('Exported public evidence using an explicit field allowlist.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-evidence', action='store_true')
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if args.refresh_evidence:
        refresh_evidence()
    payload = {'verification': read_json(EVIDENCE / 'verification.json'),
               'trace': read_json(EVIDENCE / 'policy-trace.json'),
               'guardrails': read_json(EVIDENCE / 'guardrail-results.json')}
    template = (ROOT / 'tools' / 'portfolio.html').read_text(encoding='utf-8')
    serialized = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    (ROOT / 'docs' / 'index.html').write_text(
        template.replace('__EVIDENCE_JSON__', serialized), encoding='utf-8', newline='\n')
    print('Built docs/index.html. Open locally; no AWS credentials or server required.')


if __name__ == '__main__':
    main()
