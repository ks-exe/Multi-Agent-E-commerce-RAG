"""Executable AgentCore HTTP server: /ping and /invocations on port 8080."""
import json
import uuid
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent_orchestrator import (build_inventory_agent, build_refund_agent,
                                build_policy_agent, build_communication_agent,
                                build_orchestrator_agent)
from telemetry import request_span, flush

app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload, context):
    customer_id = payload.get('customer_id', '')
    message = payload.get('message', '')
    session_id = getattr(context, 'session_id', None) or payload.get('session_id') or str(uuid.uuid4())
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise ValueError('customer_id is required.')
    if not isinstance(message, str) or not message.strip():
        raise ValueError('message is required.')
    try:
        with request_span(session_id) as trace_id:
            orchestrator = build_orchestrator_agent(
                build_inventory_agent(), build_refund_agent(),
                build_policy_agent(), build_communication_agent())
            response = orchestrator(f'[Session ID: {session_id}] [Customer ID: {customer_id}] {message}')
    finally:
        # Export failed requests too, so startup/model-access errors remain traceable.
        flush()
    return {'response': response, 'session_id': session_id, 'trace_id': trace_id}

if __name__ == '__main__':
    app.run()

