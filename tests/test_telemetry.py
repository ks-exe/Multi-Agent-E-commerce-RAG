"""Check trace continuity across concurrent agent calls and failure recording."""
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from unittest.mock import patch

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from telemetry import agent_call


class AgentTracingTests(unittest.TestCase):
    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer('test')
        self.patch = patch('opentelemetry.trace.get_tracer', return_value=self.tracer)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.provider.shutdown)

    def test_parallel_calls_share_parent_without_merging_siblings(self):
        barrier = threading.Barrier(3)
        names = ['ReturnsPolicyRetrieverAgent', 'ShippingPolicyRetrieverAgent',
                 'WarrantyPolicyRetrieverAgent']

        def invoke(name):
            with agent_call(name):
                barrier.wait(timeout=5)
                with self.tracer.start_as_current_span('retrieve ' + name):
                    pass

        with self.tracer.start_as_current_span('PolicyAgent') as parent:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(copy_context().run, invoke, name) for name in names]
                for future in futures:
                    future.result()
        spans = self.exporter.get_finished_spans()
        calls = [s for s in spans if s.name.startswith('delegate ')]
        self.assertEqual(len(calls), 3)
        self.assertLess(max(s.start_time for s in calls), min(s.end_time for s in calls))
        for call in calls:
            self.assertEqual(call.context.trace_id, parent.get_span_context().trace_id)
            self.assertEqual(call.parent.span_id, parent.get_span_context().span_id)
            self.assertEqual(call.kind, trace.SpanKind.CLIENT)
            self.assertEqual(call.attributes['novamart.agent.execution'], 'in_process')
            child = next(s for s in spans if s.name == 'retrieve ' + call.attributes['peer.service'])
            self.assertEqual(child.parent.span_id, call.context.span_id)

    def test_failure_is_recorded_and_propagates(self):
        with self.assertRaisesRegex(RuntimeError, 'retrieval unavailable'):
            with agent_call('PolicyAgent'):
                raise RuntimeError('retrieval unavailable')
        span, = self.exporter.get_finished_spans()
        self.assertEqual(span.status.status_code, trace.StatusCode.ERROR)
        self.assertEqual(span.events[0].name, 'exception')


if __name__ == '__main__':
    unittest.main(verbosity=2)
