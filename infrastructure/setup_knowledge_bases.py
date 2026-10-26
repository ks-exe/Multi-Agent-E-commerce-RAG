"""Provision, ingest and verify NovaMart's three S3 Vectors Knowledge Bases."""
import json
import sys
import time
from pathlib import Path
import boto3

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
import config
from runtime_support import save_settings

def main():
    agent = boto3.client('bedrock-agent', region_name=config.AWS_REGION)
    retrieval = boto3.client('bedrock-agent-runtime', region_name=config.AWS_REGION)
    vectors = boto3.client('s3vectors', region_name=config.AWS_REGION)
    s3 = boto3.client('s3', region_name=config.AWS_REGION)
    role = config._exports[config.PROJECT_NAME + '-KnowledgeBaseRoleArn']
    vector_bucket = vectors.get_vector_bucket(
        vectorBucketName=config.VECTOR_STORE_BUCKET)['vectorBucket']
    existing = {kb['name']: kb for page in agent.get_paginator('list_knowledge_bases').paginate()
                for kb in page.get('knowledgeBaseSummaries', [])}
    jobs = {}
    for domain, index_name in config.POLICY_INDEX_NAMES.items():
        prefix = 'policies/' + domain + '/'
        files = [o for page in s3.get_paginator('list_objects_v2').paginate(
            Bucket=config.POLICY_BUCKET, Prefix=prefix) for o in page.get('Contents', [])]
        if not files:
            raise ValueError('No policy documents at ' + prefix)
        index = vectors.get_index(vectorBucketName=config.VECTOR_STORE_BUCKET,
                                  indexName=index_name)['index']
        if index['dimension'] != 1024:
            raise ValueError('Vector index must match 1024-dimensional Titan embeddings.')
        name = 'novamart-' + domain + '-policy-kb'
        if name in existing:
            kb_id = existing[name]['knowledgeBaseId']
            kb = agent.get_knowledge_base(knowledgeBaseId=kb_id)['knowledgeBase']
            settings = kb['storageConfiguration'].get('s3VectorsConfiguration', {})
            if not (settings.get('indexArn') == index['indexArn'] or (
                    settings.get('vectorBucketArn') == vector_bucket['vectorBucketArn']
                    and settings.get('indexName') == index_name)):
                raise ValueError(f'{name} points to an unexpected backing store.')
        else:
            response = agent.create_knowledge_base(
                name=name, roleArn=role,
                knowledgeBaseConfiguration={'type': 'VECTOR', 'vectorKnowledgeBaseConfiguration': {
                    'embeddingModelArn': f'arn:aws:bedrock:{config.AWS_REGION}::foundation-model/{config.EMBEDDING_MODEL_ID}',
                    'embeddingModelConfiguration': {'bedrockEmbeddingModelConfiguration': {
                        'dimensions': 1024, 'embeddingDataType': 'FLOAT32'}}}},
                storageConfiguration={'type': 'S3_VECTORS', 's3VectorsConfiguration': {
                    'indexArn': index['indexArn']}})
            kb_id = response['knowledgeBase']['knowledgeBaseId']
        deadline = time.monotonic() + 300
        while True:
            kb = agent.get_knowledge_base(knowledgeBaseId=kb_id)['knowledgeBase']
            if kb['status'] == 'ACTIVE':
                break
            if kb['status'] == 'FAILED' or time.monotonic() > deadline:
                raise RuntimeError(json.dumps(kb, default=str))
            time.sleep(5)
        sources = [d for page in agent.get_paginator('list_data_sources').paginate(
            knowledgeBaseId=kb_id) for d in page.get('dataSourceSummaries', [])]
        name = domain + '-policy-documents'
        source = next((d for d in sources if d['name'] == name), None)
        if source:
            ds_id = source['dataSourceId']
        else:
            response = agent.create_data_source(
                knowledgeBaseId=kb_id, name=name,
                dataSourceConfiguration={'type': 'S3', 's3Configuration': {
                    'bucketArn': 'arn:aws:s3:::' + config.POLICY_BUCKET, 'inclusionPrefixes': [prefix]}},
                vectorIngestionConfiguration={'chunkingConfiguration': {
                    'chunkingStrategy': 'FIXED_SIZE',
                    'fixedSizeChunkingConfiguration': {'maxTokens': 300, 'overlapPercentage': 15}}})
            ds_id = response['dataSource']['dataSourceId']
        job = agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)['ingestionJob']
        jobs[domain] = {'kb_id': kb_id, 'data_source_id': ds_id,
                        'job_id': job['ingestionJobId'], 'source_files': len(files)}
        save_settings(**{domain.upper() + '_KB_ID': kb_id})
        print(f'{domain}: KB {kb_id}, ingestion {job["ingestionJobId"]}, {len(files)} source files', flush=True)
    deadline = time.monotonic() + 900
    pending = set(jobs)
    while pending and time.monotonic() < deadline:
        for domain in list(pending):
            item = jobs[domain]
            job = agent.get_ingestion_job(
                knowledgeBaseId=item['kb_id'], dataSourceId=item['data_source_id'],
                ingestionJobId=item['job_id'])['ingestionJob']
            if job['status'] == 'COMPLETE':
                if job.get('statistics', {}).get('numberOfDocumentsFailed', 0):
                    raise RuntimeError(json.dumps(job, default=str))
                item['statistics'] = job.get('statistics', {})
                pending.remove(domain)
                print(f'{domain}: ingestion COMPLETE; {item["statistics"]}', flush=True)
            elif job['status'] in ('FAILED', 'STOPPED'):
                raise RuntimeError(json.dumps(job, default=str))
        if pending:
            time.sleep(10)
    if pending:
        raise TimeoutError('Ingestion timed out: ' + ', '.join(pending))
    queries = {'returns': 'What is the return window for Premium customers?',
               'shipping': 'How much is expedited shipping for Premium members?',
               'warranty': 'How long is the electronics warranty for Premium customers?'}
    for domain, query in queries.items():
        result = retrieval.retrieve(
            knowledgeBaseId=jobs[domain]['kb_id'], retrievalQuery={'text': query},
            retrievalConfiguration={'vectorSearchConfiguration': {'numberOfResults': 3}})
        chunks = result.get('retrievalResults', [])
        if not chunks:
            raise RuntimeError(domain + ' returned no passages after ingestion.')
        jobs[domain]['retrieval_scores'] = [r.get('score') for r in chunks]
        print(f'{domain}: {len(chunks)} retrieved passages, scores {jobs[domain]["retrieval_scores"]}')
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    (ROOT / 'artifacts' / 'knowledge-bases.json').write_text(json.dumps(jobs, indent=2))

if __name__ == '__main__':
    main()
