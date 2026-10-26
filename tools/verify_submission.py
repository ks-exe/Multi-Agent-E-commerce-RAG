"""Offline checks for evidence integrity, required artifacts, and deployment IDs."""
import hashlib
import json
import re
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/submission'


def main():
    grader_hash = hashlib.sha256((ROOT / 'tests/test_agent.py').read_bytes()).hexdigest()
    assert grader_hash == 'b0aa1db9370be03e393a908a3f3272ba67f01cef90b6611ea1862091523205a5'
    scores = {'task2': '40/40', 'task3': '20/20', 'task4': '15/15',
              'task5': '25/25', 'task6': '20/20', 'all': '120/120'}
    for name in [*scores, 'deploy', 'infrastructure', 'parallel', 'live']:
        metadata = json.loads((EVIDENCE / 'logs' / (name + '.json')).read_text())
        transcript = (EVIDENCE / 'logs' / (name + '.txt')).read_bytes()
        assert hashlib.sha256(transcript).hexdigest() == metadata['transcript_sha256'], name
        assert metadata['exit_code'] == 0 and metadata['passed'], name
        if name in scores:
            assert metadata['score'] == scores[name]
            assert ('Score: ' + scores[name] + ' pts') in transcript.decode('utf-8')
    images = [*scores, 'deploy', 'infrastructure', 'parallel', 'parallel-summary', 'live',
              's3-vector-indexes', 'agentcore-runtime', 'agentcore-configuration',
              'agentcore-memory', 'xray-service-map', 'xray-agent-spans']
    images += [prefix + domain + suffix for domain in ('returns', 'shipping', 'warranty')
               for prefix, suffix in [('kb-', ''), ('kb-', '-sync'), ('vector-', '')]]
    for name in images:
        data = (EVIDENCE / 'images' / (name + '.png')).read_bytes()
        assert data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) > 10000, name
    deployment = json.loads((EVIDENCE / 'deployment.json').read_text())
    settings = dotenv_values(ROOT / '.env')
    allowed = {'AWS_REGION', 'PROJECT_NAME', 'AGENT_LOG_GROUP', 'RETURNS_KB_ID',
               'SHIPPING_KB_ID', 'WARRANTY_KB_ID', 'AGENTCORE_RUNTIME_ARN',
               'GUARDRAIL_ID', 'GUARDRAIL_VERSION', 'AGENTCORE_MEMORY_ID', 'AGENTCORE_MEMORY_ARN'}
    assert set(settings) == allowed and all(settings.values()), 'Unexpected or empty .env keys'
    for key, value in deployment['runtime']['environmentVariables'].items():
        assert settings[key] == value, key
    assert settings['AGENTCORE_RUNTIME_ARN'] == deployment['runtime']['agentRuntimeArn']
    assert settings['AGENTCORE_MEMORY_ARN'] == deployment['memory']['arn']
    traces = json.loads((EVIDENCE / 'live-verification.json').read_text())
    live_ids = re.findall(r'^Trace ID: (1-[0-9a-f-]+)$',
                          (EVIDENCE / 'logs/live.txt').read_text(encoding='utf-8'), re.M)
    assert live_ids == [t['trace_id'] for t in traces], 'Live transcript and trace verification differ'
    assert len(traces) == 5 and all(t['routing_passed'] and t['routing_tools_traced']
                                   and t['memory_event_count'] > 0 for t in traces)
    assert sum(t['span_count'] for t in traces) == 433
    policy = traces[1]
    assert policy['retrievers_ran_concurrently'] and policy['span_count'] == 111
    graph = json.loads((EVIDENCE / 'service-map.json').read_text())
    assert graph['trace_id'] == policy['trace_id']
    services = [s['Service'] for s in graph['Services']]
    by_id = {s['ReferenceId']: s['Name'] for s in services}
    orchestrator = next(s for s in services if s['Name'] == 'NovaMart-Orchestrator')
    connected = {by_id[e['ReferenceId']] for e in orchestrator['Edges']}
    assert {'PolicyAgent', 'CommunicationAgent', 'ReturnsPolicyRetrieverAgent',
            'ShippingPolicyRetrieverAgent', 'WarrantyPolicyRetrieverAgent'} <= connected
    for path in [ROOT / 'README.md', ROOT / 'docs/SUBMISSION.md', EVIDENCE / 'README.md']:
        for link in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', path.read_text(encoding='utf-8-sig')):
            if not link.startswith(('http:', 'https:', '#')):
                assert (path.parent / link.split('#')[0]).exists(), link
    print(f'PASS: original grader unchanged; 10 execution logs verified; {len(images)} screenshots present.')
    print('PASS: .env contains only approved resource identifiers matching the AWS snapshot.')
    print('PASS: five workflows, 433 spans, memory events, parallel retrieval, and connected X-Ray workers verified.')


if __name__ == '__main__':
    main()
