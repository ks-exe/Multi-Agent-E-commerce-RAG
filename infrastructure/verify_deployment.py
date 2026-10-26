"""Read live AWS metadata and save the reviewer deployment snapshot (no secrets)."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config


def main():
    session = boto3.Session(region_name=config.AWS_REGION)
    options = Config(connect_timeout=10, read_timeout=60,
                     retries={'mode': 'standard', 'max_attempts': 3})
    clients = {name: session.client(name, config=options) for name in (
        'bedrock-agent', 'bedrock-agentcore-control', 'bedrock', 's3vectors', 'cloudformation', 'logs', 'xray')}
    agent, control = clients['bedrock-agent'], clients['bedrock-agentcore-control']
    vector_bucket = clients['s3vectors'].get_vector_bucket(
        vectorBucketName=config.VECTOR_STORE_BUCKET)['vectorBucket']
    result = {'captured_utc': datetime.now(timezone.utc).isoformat(), 'region': config.AWS_REGION,
              'knowledge_bases': {}}
    stack = clients['cloudformation'].describe_stacks(StackName=config.PROJECT_NAME)['Stacks'][0]
    result['stack'] = {'name': stack['StackName'], 'status': stack['StackStatus']}
    assert stack['StackStatus'] in ('CREATE_COMPLETE', 'UPDATE_COMPLETE')
    for domain, kb_id in {'returns': config.RETURNS_KB_ID, 'shipping': config.SHIPPING_KB_ID,
                          'warranty': config.WARRANTY_KB_ID}.items():
        kb = agent.get_knowledge_base(knowledgeBaseId=kb_id)['knowledgeBase']
        index = clients['s3vectors'].get_index(vectorBucketName=config.VECTOR_STORE_BUCKET,
                                              indexName=config.POLICY_INDEX_NAMES[domain])['index']
        storage = kb['storageConfiguration']
        settings = storage['s3VectorsConfiguration']
        assert storage['type'] == 'S3_VECTORS'
        assert settings.get('indexArn') == index['indexArn'] or (
            settings.get('indexName') == index['indexName'] and
            settings.get('vectorBucketArn') == vector_bucket['vectorBucketArn'])
        embedding = kb['knowledgeBaseConfiguration']['vectorKnowledgeBaseConfiguration']
        assert embedding['embeddingModelArn'].endswith('/amazon.titan-embed-text-v2:0')
        assert kb['status'] == 'ACTIVE' and index['dimension'] == 1024
        sources = [s for page in agent.get_paginator('list_data_sources').paginate(knowledgeBaseId=kb_id)
                   for s in page['dataSourceSummaries']]
        assert len(sources) == 1
        source_id = sources[0]['dataSourceId']
        source = agent.get_data_source(knowledgeBaseId=kb_id, dataSourceId=source_id)['dataSource']
        source_config = source['dataSourceConfiguration']['s3Configuration']
        assert source_config['inclusionPrefixes'] == [f'policies/{domain}/']
        assert source_config['bucketArn'] == 'arn:aws:s3:::' + config.POLICY_BUCKET
        jobs = [j for page in agent.get_paginator('list_ingestion_jobs').paginate(
            knowledgeBaseId=kb_id, dataSourceId=source_id) for j in page['ingestionJobSummaries']]
        latest = max(jobs, key=lambda job: job['startedAt'])
        job = agent.get_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=source_id,
                                     ingestionJobId=latest['ingestionJobId'])['ingestionJob']
        assert job['status'] == 'COMPLETE' and job['statistics']['numberOfDocumentsFailed'] == 0
        result['knowledge_bases'][domain] = {
            'id': kb_id, 'name': kb['name'], 'status': kb['status'],
            'embedding': embedding, 'storageConfiguration': storage, 'resolvedVectorIndex': index,
            'data_source': {'id': source_id, 'name': source['name'], 'configuration': source_config},
            'latest_ingestion': job}
        print(f'PASS {kb["name"]}: {kb_id} ACTIVE; Titan Text Embeddings v2')
        print(f'  Backing bucket: {config.VECTOR_STORE_BUCKET}')
        print(f'  Index: {index["indexName"]}; dimension=1024; distance={index["distanceMetric"]}')
        print(f'  Source: s3://{config.POLICY_BUCKET}/policies/{domain}/')
        print(f'  Sync: COMPLETE; failed documents=0; job={job["ingestionJobId"]}')
    runtime = control.get_agent_runtime(agentRuntimeId=config.AGENTCORE_RUNTIME_ARN.split('/')[-1])
    required = ('AWS_REGION', 'PROJECT_NAME', 'RETURNS_KB_ID', 'SHIPPING_KB_ID', 'WARRANTY_KB_ID',
                'AGENT_LOG_GROUP', 'GUARDRAIL_ID', 'GUARDRAIL_VERSION')
    assert runtime['status'] == 'READY'
    assert runtime['networkConfiguration']['networkMode'] == 'PUBLIC'
    assert runtime['protocolConfiguration']['serverProtocol'] == 'HTTP'
    assert runtime['roleArn'] == config.AGENTCORE_ROLE_ARN
    assert all(runtime['environmentVariables'].get(name) == str(getattr(config, name)) for name in required)
    assert config.GUARDRAIL_VERSION.isdigit()
    result['runtime'] = {key: runtime[key] for key in ('agentRuntimeArn', 'agentRuntimeVersion',
        'status', 'roleArn', 'networkConfiguration', 'protocolConfiguration')}
    result['runtime']['environmentVariables'] = {key: runtime['environmentVariables'][key] for key in required}
    print(f'PASS runtime version {runtime["agentRuntimeVersion"]}: READY / PUBLIC / HTTP; required environment matches .env/config.')
    memory = control.get_memory(memoryId=os.environ['AGENTCORE_MEMORY_ID'])['memory']
    assert memory['status'] == 'ACTIVE' and memory['eventExpiryDuration'] == 7
    assert any(s['type'] == 'SUMMARIZATION' for s in memory['strategies'])
    result['memory'] = memory
    print('PASS memory: ACTIVE; summarization strategy; 7-day retention.')
    guardrail = clients['bedrock'].get_guardrail(guardrailIdentifier=config.GUARDRAIL_ID,
                                               guardrailVersion=config.GUARDRAIL_VERSION)
    result['guardrail'] = {k: v for k, v in guardrail.items() if k != 'ResponseMetadata'}
    groups = clients['logs'].describe_log_groups(logGroupNamePrefix=config.AGENT_LOG_GROUP)['logGroups']
    result['log_group'] = next(g for g in groups if g['logGroupName'] == config.AGENT_LOG_GROUP)
    result['trace_storage'] = {k: v for k, v in clients['xray'].get_trace_segment_destination().items()
                               if k != 'ResponseMetadata'}
    env = runtime['environmentVariables']
    assert env['NOVAMART_LOG_LEVEL'] == 'INFO' and env['OTEL_TRACES_SAMPLER'] == 'always_on'
    result['tracing'] = {k: env[k] for k in ('NOVAMART_LOG_LEVEL', 'OTEL_TRACES_SAMPLER',
                                           'NOVAMART_CLOUDWATCH_ENABLED', 'NOVAMART_XRAY_ENABLED')}
    print(f'PASS guardrail: {config.GUARDRAIL_ID}, numbered version {config.GUARDRAIL_VERSION}.')
    print('PASS observability: CloudWatch INFO; always-on X-Ray sampling; trace storage checked.')
    target = ROOT / 'docs/submission/deployment.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, default=str) + '\n', encoding='utf-8')
    print('Saved actual API metadata to docs/submission/deployment.json')


if __name__ == '__main__':
    main()
