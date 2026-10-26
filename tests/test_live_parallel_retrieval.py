"""Supplement Task 5 with real Bedrock retrieval and concurrent agent execution.

No model or AWS response is mocked. Wrappers only record actual call results
and timings; the original classroom grader is unchanged.
"""
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import agent_orchestrator as ao
import config


def main():
    expected = {'Returns': config.RETURNS_KB_ID, 'Shipping': config.SHIPPING_KB_ID,
                'Warranty': config.WARRANTY_KB_ID}
    lock = threading.Lock()
    retrieved, intervals = {}, {}
    original_retrieve, original_span = ao.retrieve_from_knowledge_base, ao.agent_call

    def record_retrieval(kb_id, query):
        result = original_retrieve(kb_id, query)
        with lock:
            retrieved[kb_id] = result
        return result

    @contextmanager
    def record_agent(name):
        start = time.perf_counter()
        with original_span(name) as span:
            yield span
        with lock:
            intervals[name] = (start, time.perf_counter())

    print('Live Task 5 supplement: real Sonnet agents and three Bedrock Knowledge Bases.', flush=True)
    policy = ao.build_policy_agent()
    with patch.object(ao, 'retrieve_from_knowledge_base', side_effect=record_retrieval), \
            patch.object(ao, 'agent_call', side_effect=record_agent):
        result = policy.tool.search_all_policies(
            query='Explain NovaMart return windows, shipping charges and delivery times, '
                  'and warranty coverage for Standard and Premium customers. Cite policy sources.')
    assert result['status'] == 'success', result
    text = '\n'.join(item.get('text', '') for item in result['content'])
    for domain, kb_id in expected.items():
        assert retrieved.get(kb_id), f'{domain}: no real retrieval results'
        assert f'{domain} policy:\n' in text, f'{domain}: synthesized section missing'
        assert f'policies/{domain.lower()}/' in text, f'{domain}: source citation missing'
        print(f'PASS {domain}: KB {kb_id}, {len(retrieved[kb_id])} passages; synthesized section and source present.')
    assert len(intervals) == 3, intervals
    overlap = min(end for _, end in intervals.values()) - max(start for start, _ in intervals.values())
    assert overlap > 0, 'The three real agent calls did not overlap.'
    for name, (start, end) in sorted(intervals.items()):
        print(f'PASS {name}: {end - start:.3f}s')
    print(f'PASS all three retriever agents overlapped for {overlap:.3f}s.')
    print('\nSynthesized tool output:\n' + text)
    print('\nPASS live parallel retrieval: all three Knowledge Bases returned grounded results.')


if __name__ == '__main__':
    main()
