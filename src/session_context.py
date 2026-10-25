"""Guardrail enforcement and persistent, customer-scoped AgentCore conversation events."""
import os
from datetime import datetime, timezone
import boto3
import config

def screen_content(text, source):
    if not config.GUARDRAIL_ID:
        return text, False
    client = boto3.client('bedrock-runtime', region_name=config.AWS_REGION)
    response = client.apply_guardrail(
        guardrailIdentifier=config.GUARDRAIL_ID, guardrailVersion=config.GUARDRAIL_VERSION,
        source=source, content=[{'text': {'text': text}}])
    def blocked(value):
        if isinstance(value, dict):
            return value.get('action') == 'BLOCKED' or any(blocked(v) for v in value.values())
        if isinstance(value, list):
            return any(blocked(v) for v in value)
        return False
    output = '\n'.join(item.get('text', '') for item in response.get('outputs', []))
    is_blocked = blocked(response.get('assessments', []))
    if response.get('action') == 'GUARDRAIL_INTERVENED':
        return output or 'I cannot assist with that request.', is_blocked or not output
    return output or text, False

def load_conversation(customer_id, session_id):
    memory_id = os.environ.get('AGENTCORE_MEMORY_ID')
    if not memory_id:
        return []
    client = boto3.client('bedrock-agentcore', region_name=config.AWS_REGION)
    events = []
    for page in client.get_paginator('list_events').paginate(
            memoryId=memory_id, actorId=customer_id, sessionId=session_id,
            includePayloads=True):
        events.extend(page.get('events', []))
    events.sort(key=lambda event: event['eventTimestamp'])
    return [part['conversational'] for event in events[-10:]
            for part in event.get('payload', []) if 'conversational' in part]

def save_conversation(customer_id, session_id, request, response):
    memory_id = os.environ.get('AGENTCORE_MEMORY_ID')
    if not memory_id:
        return
    client = boto3.client('bedrock-agentcore', region_name=config.AWS_REGION)
    client.create_event(
        memoryId=memory_id, actorId=customer_id, sessionId=session_id,
        eventTimestamp=datetime.now(timezone.utc),
        payload=[{'conversational': {'role': 'USER', 'content': {'text': request}}},
                 {'conversational': {'role': 'ASSISTANT', 'content': {'text': response}}}])
