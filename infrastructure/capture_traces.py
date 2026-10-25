"""Retrieve real CloudWatch-backed X-Ray traces and verify deployed scenario evidence."""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import boto3
import config


def read_scenarios(path):
    data = path.read_bytes()
    content = data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
    entries = []
    for line in content.splitlines():
        match = re.match(r'Session: ([\w-]+) \| Customer: ([\w-]+)', line)
        if match:
            entries.append({'session_id': match[1], 'customer_id': match[2]})
        match = re.match(r'Trace ID: (1-[0-9a-f]{8}-[0-9a-f]{24})', line)
        if match and entries:
            entries[-1]['trace_id'] = match[1]
    if len(entries) != 5 or any('trace_id' not in entry for entry in entries):
        raise ValueError('The five deployed scenarios must finish and return trace IDs before verification.')
    return entries


def retrieve(xray, trace_ids):
    start = datetime.fromtimestamp(min(int(value.split('-')[1], 16) for value in trace_ids) - 120,
                                   timezone.utc)
    deadline = time.monotonic() + 600
    traces = {}
    while time.monotonic() < deadline:
        token = xray.start_trace_retrieval(
            TraceIds=[value for value in trace_ids if value not in traces],
            StartTime=start, EndTime=datetime.now(timezone.utc))['RetrievalToken']
        while time.monotonic() < deadline:
            page = xray.list_retrieved_traces(RetrievalToken=token, TraceFormat='XRAY')
            status = page['RetrievalStatus']
            print('Trace retrieval: ' + status, flush=True)
            if status == 'COMPLETE':
                break
            if status in ('FAILED', 'CANCELLED', 'TIMEOUT'):
                raise RuntimeError('Trace retrieval ended with ' + status)
            time.sleep(10)
        else:
            xray.cancel_trace_retrieval(RetrievalToken=token)
            raise TimeoutError('Trace retrieval did not complete.')
        while True:
            for trace in page.get('Traces', []):
                if trace.get('Spans'):
                    traces[trace['Id']] = trace
            if not page.get('NextToken'):
                break
            page = xray.list_retrieved_traces(
                RetrievalToken=token, TraceFormat='XRAY', NextToken=page['NextToken'])
        if set(trace_ids) <= set(traces):
            return traces
        print(f'Waiting for ingestion: {len(traces)}/{len(trace_ids)} traces available', flush=True)
        time.sleep(20)
    raise TimeoutError('Some scenario traces have not reached CloudWatch trace storage.')


def span_documents(trace):
    documents = {}

    def visit(value):
        if isinstance(value, dict):
            if 'id' in value and 'name' in value and 'start_time' in value:
                documents[value['id']] = value
            for child in value.get('subsegments', []):
                visit(child)

    for span in trace['Spans']:
        visit(json.loads(span['Document']))
    return list(documents.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario-log', type=Path, default=ROOT / 'artifacts' / 'live-scenarios.txt')
    args = parser.parse_args()
    entries = read_scenarios(args.scenario_log)
    traces = retrieve(boto3.client('xray', region_name=config.AWS_REGION),
                      [entry['trace_id'] for entry in entries])
    table = boto3.resource('dynamodb', region_name=config.AWS_REGION).Table(config.WORKFLOW_STATE_TABLE)
    memory = boto3.client('bedrock-agentcore', region_name=config.AWS_REGION)
    expected_routes = [
        ['inventory_agent', 'refund_agent'], ['policy_agent'], [],
        ['inventory_agent'], ['inventory_agent', 'refund_agent'],
    ]
    target = ROOT / 'artifacts' / 'traces'
    target.mkdir(parents=True, exist_ok=True)
    for entry, workers in zip(entries, expected_routes):
        state = table.get_item(Key={'session_id': entry['session_id']}, ConsistentRead=True)['Item']
        expected = ['initialize_session'] + ['route_to_' + name for name in workers] + [
            'route_to_communication_agent']
        entry['route_history'] = state['route_history']
        entry['routing_passed'] = state['route_history'] == expected
        entry['response'] = state.get('communication_agent', '')
        entry['workflow_version'] = int(state['version'])
        entry['memory_event_count'] = sum(len(page.get('events', [])) for page in
            memory.get_paginator('list_events').paginate(memoryId=os.environ['AGENTCORE_MEMORY_ID'],
                actorId=entry['customer_id'], sessionId=entry['session_id'], includePayloads=False))
        trace = traces[entry['trace_id']]
        (target / (entry['trace_id'] + '.json')).write_text(json.dumps(trace, indent=2), encoding='utf-8')
        documents = span_documents(trace)
        entry['span_count'] = len(documents)
        entry['span_names'] = sorted({span['name'] for span in documents})
        entry['routing_tools_traced'] = all('execute_tool ' + name in entry['span_names']
                                           for name in expected)
        entry['trace_url'] = (f'https://{config.AWS_REGION}.console.aws.amazon.com/xray/home'
                              f'?region={config.AWS_REGION}#/traces/{entry["trace_id"]}')
        if workers == ['policy_agent']:
            intervals = {}
            for name in ('ReturnsPolicyRetrieverAgent', 'ShippingPolicyRetrieverAgent', 'WarrantyPolicyRetrieverAgent'):
                matches = [span for span in documents if name in span.get('name', '')]
                if matches:
                    span = min(matches, key=lambda value: value['start_time'])
                    intervals[name] = {'start': span['start_time'], 'end': span.get('end_time', span['start_time'])}
            entry['retriever_intervals'] = intervals
            entry['retrievers_ran_concurrently'] = len(intervals) == 3 and (
                max(value['start'] for value in intervals.values()) <
                min(value['end'] for value in intervals.values()))
        print(json.dumps(entry), flush=True)
    (ROOT / 'artifacts' / 'live-verification.json').write_text(json.dumps(entries, indent=2), encoding='utf-8')
    if not all(entry['routing_passed'] and entry['routing_tools_traced'] and entry['response'] and entry['memory_event_count'] > 0
               and entry['span_count'] > 0 for entry in entries):
        raise SystemExit('A live routing, response, memory or tracing check failed.')
    if not entries[1].get('retrievers_ran_concurrently'):
        raise SystemExit('The policy trace does not demonstrate three concurrent retrievers.')
    print('Verified all five deployed workflows, persisted memory events, and concurrent policy retriever spans.')


if __name__ == '__main__':
    main()
