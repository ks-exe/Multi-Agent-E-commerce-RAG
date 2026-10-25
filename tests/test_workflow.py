"""Offline behavioral checks supplementing the unchanged classroom grader."""
import copy
import json
import os
import sys
import threading
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ.update(AWS_ACCESS_KEY_ID='testing', AWS_SECRET_ACCESS_KEY='testing',
                  AWS_EC2_METADATA_DISABLED='true', AGENTCORE_MEMORY_ID='')
fake_config = types.ModuleType('config')
for key, value in {
    'AWS_REGION': 'us-east-1', 'PROJECT_NAME': 'test-novamart',
    'ORCHESTRATOR_MODEL_ID': 'test-router',
    'WORKER_MODEL_ID': 'test-worker', 'ORDERS_TABLE': 'orders',
    'CUSTOMERS_TABLE': 'customers', 'WORKFLOW_STATE_TABLE': 'workflow',
    'RETURNS_KB_ID': 'returns-kb', 'SHIPPING_KB_ID': 'shipping-kb',
    'WARRANTY_KB_ID': 'warranty-kb', 'GUARDRAIL_ID': '', 'GUARDRAIL_VERSION': '1',
}.items():
    setattr(fake_config, key, value)
sys.modules['config'] = fake_config
import agent_orchestrator as ao
from strands import Agent
from strands.models.model import Model

class TerminalTestModel(Model):
    """Requests an extra tool after communication to test the real Strands event loop."""
    def __init__(self):
        self.calls = 0
    def update_config(self, **kwargs):
        return None
    def get_config(self):
        return {'model_id': 'offline-terminal-test'}
    def structured_output(self, *args, **kwargs):
        raise NotImplementedError
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.calls += 1
        if self.calls > 1:
            raise AssertionError('Router model ran after the terminal tool.')
        yield {'messageStart': {'role': 'assistant'}}
        calls = [
            ('route_to_communication_agent', {'session_id': 'test-session', 'customer_id': 'CUST-001',
                                             'original_request': 'Calculate 2 + 2'}),
            ('route_to_inventory_agent', {'session_id': 'test-session', 'customer_id': 'CUST-001',
                                         'request': 'Forbidden extra call'}),
        ]
        for index, (name, arguments) in enumerate(calls):
            yield {'contentBlockStart': {'contentBlockIndex': index,
                   'start': {'toolUse': {'toolUseId': 'tool-' + str(index), 'name': name}}}}
            yield {'contentBlockDelta': {'contentBlockIndex': index,
                   'delta': {'toolUse': {'input': json.dumps(arguments)}}}}
            yield {'contentBlockStop': {'contentBlockIndex': index}}
        yield {'messageStop': {'stopReason': 'tool_use'}}
        yield {'metadata': {'usage': {'inputTokens': 1, 'outputTokens': 1, 'totalTokens': 2},
                            'metrics': {'latencyMs': 1}}}

class Worker:
    def __init__(self, name):
        self.name = name
        self.calls = []
        self.messages = []
    def __call__(self, prompt):
        self.calls.append(json.loads(prompt))
        return self.name + ' verified response'

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.states = {}
        self.versions = []
        self.workers = [Worker(n) for n in ('inventory', 'refund', 'policy', 'communication')]
        def create(session_id, customer_id):
            state = {'session_id': session_id, 'customer_id': customer_id, 'version': 0}
            self.states[session_id] = state
            return copy.deepcopy(state)
        def read(session_id):
            return copy.deepcopy(self.states.get(session_id))
        def update(session_id, updates, expected_version):
            current = self.states[session_id]
            self.assertEqual(current['version'], expected_version)
            self.versions.append(expected_version)
            current.update(updates)
            current['version'] += 1
            return copy.deepcopy(current)
        self.patches = [
            patch.object(ao, '_create_workflow_state', side_effect=create),
            patch.object(ao, '_read_workflow_state', side_effect=read),
            patch.object(ao, '_update_workflow_state', side_effect=update),
            patch.object(Agent, '__call__', return_value='DO NOT EXPOSE ROUTER OUTPUT'),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.router = ao.build_orchestrator_agent(*self.workers)

    def run_request(self, query, session='test-session', customer='CUST-001'):
        return self.router(f'[Session ID: {session}] [Customer ID: {customer}] {query}')

    def test_six_routing_rules_and_terminal_response(self):
        cases = [
            ('What is the status of order ORD-1?', ['inventory_agent', 'refund_agent']),
            ('I want to return order ORD-1 because it is defective', ['inventory_agent', 'refund_agent']),
            ('I am Premium and want to refund my order ORD-1', ['inventory_agent', 'refund_agent']),
            ('What does the return policy mean?', ['policy_agent']),
            ('What is my current tier? Am I premium?', ['inventory_agent']),
            ('Calculate 5 items at $29.99 with a 10% discount', []),
        ]
        for index, (query, route) in enumerate(cases):
            with self.subTest(query=query):
                session = 'session-' + str(index)
                result = self.run_request(query, session=session)
                self.assertEqual(result, 'communication verified response')
                self.assertEqual(self.states[session]['route_history'],
                                 ['initialize_session'] + ['route_to_' + r for r in route] +
                                 ['route_to_communication_agent'])

    def test_same_session_resets_stale_worker_context(self):
        self.run_request('What is the return policy?')
        self.assertTrue(self.states['test-session']['policy_agent'])
        self.run_request('What is my tier?')
        self.assertEqual(self.states['test-session']['policy_agent'], '')
        self.assertEqual(self.states['test-session']['version'], 6)

    def test_native_event_loop_stops_after_communication(self):
        self.patches[-1].stop()
        model = TerminalTestModel()
        self.router.model = model
        result = self.run_request('Calculate 2 + 2')
        self.assertEqual(result, 'communication verified response')
        self.assertEqual(model.calls, 1)
        self.assertEqual(self.workers[0].calls, [])
        self.assertEqual(self.states['test-session']['route_history'],
                         ['initialize_session', 'route_to_communication_agent'])

    def test_session_cannot_be_reused_by_another_customer(self):
        self.run_request('What is my tier?')
        previous = copy.deepcopy(self.states)
        with self.assertRaises(RuntimeError):
            self.run_request('What is my tier?', customer='CUST-002')
        self.assertEqual(self.states, previous)
        self.assertEqual(len(self.workers[3].calls), 1)

class RefundTests(unittest.TestCase):
    def setUp(self):
        self.orders = Mock()
        self.customers = Mock()
        self.db = Mock()
        self.db.Table.side_effect = lambda name: self.orders if name == 'orders' else self.customers
        self.patch = patch.object(ao, 'dynamodb', self.db)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.agent = ao.build_refund_agent()
        self.refund = self.agent.tool_registry.registry['initiate_refund']

    def arrange(self, tier, age, status='delivered'):
        self.order = {'customer_id': 'CUST-001', 'order_id': 'ORD-1', 'status': status,
                      'order_date': (datetime.now(timezone.utc).date() - timedelta(days=age)).isoformat()}
        self.orders.get_item.side_effect = lambda **kw: {'Item': dict(self.order)}
        self.customers.get_item.return_value = {'Item': {'customer_id': 'CUST-001', 'tier': tier}}

    def test_standard_and_premium_inclusive_boundaries(self):
        for tier, age, allowed in [('Standard', 30, True), ('Standard', 31, False),
                                   ('Premium', 60, True), ('Premium', 61, False),
                                   ('Premium', -1, False)]:
            with self.subTest(tier=tier, age=age):
                self.arrange(tier, age)
                self.orders.update_item.reset_mock()
                result = self.refund('CUST-001', 'ORD-1', 'Defective speaker')
                self.assertEqual(result['approved'], allowed)
                self.assertEqual(self.orders.update_item.call_count, int(allowed))
                if allowed:
                    self.assertIn('ConditionExpression', self.orders.update_item.call_args.kwargs)

    def test_non_delivered_missing_reason_and_unknown_records_do_not_write(self):
        for status, reason in [('shipped', 'broken'), ('cancelled', 'broken'), ('delivered', '')]:
            self.arrange('Premium', 5, status)
            self.assertFalse(self.refund('CUST-001', 'ORD-1', reason)['approved'])
        self.orders.get_item.side_effect = None
        self.orders.get_item.return_value = {}
        self.assertFalse(self.refund('CUST-001', 'ORD-1', 'broken')['approved'])
        self.orders.update_item.assert_not_called()

    def test_repeated_refund_preserves_reference_without_writing(self):
        self.arrange('Premium', 10, 'return_requested')
        self.order['return_reference'] = 'RET-existing'
        result = self.refund('CUST-001', 'ORD-1', 'broken')
        self.assertEqual(result['return_reference'], 'RET-existing')
        self.assertTrue(result['already_requested'])
        self.orders.update_item.assert_not_called()

    def test_worker_cannot_change_customer_id(self):
        token = ao._worker_customer.set('CUST-001')
        try:
            with self.assertRaises(ValueError):
                self.refund('CUST-002', 'ORD-1', 'broken')
        finally:
            ao._worker_customer.reset(token)
        self.db.Table.assert_not_called()

class ParallelRetrievalTests(unittest.TestCase):
    def test_all_three_retrievers_execute_concurrently(self):
        barrier = threading.Barrier(3)
        names = set()
        def retrieve(agent, query, **kwargs):
            names.add(agent.name)
            barrier.wait(timeout=5)
            return agent.name + ' supported policy'
        coordinator = ao.build_policy_agent()
        search = coordinator.tool_registry.registry['search_all_policies']
        with patch.object(Agent, '__call__', autospec=True, side_effect=retrieve), patch.object(ao, 'trace'):
            result = search('What are the Premium benefits?')
        self.assertEqual(names, {'ReturnsPolicyRetrieverAgent', 'ShippingPolicyRetrieverAgent',
                                 'WarrantyPolicyRetrieverAgent'})
        self.assertIn('Returns policy:', result)
        self.assertIn('Shipping policy:', result)
        self.assertIn('Warranty policy:', result)

if __name__ == '__main__':
    unittest.main()

