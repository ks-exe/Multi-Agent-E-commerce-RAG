"""Current AgentCore API operations shared by deployment and compatibility readers."""
import os
import time
import boto3

def save_settings(**values):
    from pathlib import Path
    from dotenv import set_key
    import config
    env_path = Path(__file__).resolve().parents[1] / '.env'
    for key, value in values.items():
        set_key(str(env_path), key, str(value))
        os.environ[key] = str(value)
        setattr(config, key, str(value))

def wait_runtime(control, runtime_id, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime = control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = runtime['status']
        if status == 'READY':
            return runtime
        if status in ('CREATE_FAILED', 'UPDATE_FAILED', 'FAILED'):
            raise RuntimeError(f"Runtime {status}: {runtime.get('failureReason', '')}")
        print(f"  Runtime status: {status}", flush=True)
        time.sleep(10)
    raise TimeoutError('AgentCore Runtime did not become READY.')

def update_runtime_environment(runtime_arn, updates):
    control = boto3.client('bedrock-agentcore-control',
                           region_name=runtime_arn.split(':')[3])
    runtime_id = runtime_arn.rsplit('/', 1)[1]
    current = wait_runtime(control, runtime_id)
    environment = dict(current.get('environmentVariables', {}))
    environment.update(updates)
    if environment != current.get('environmentVariables', {}):
        request = {key: current[key] for key in (
            'agentRuntimeArtifact', 'roleArn', 'networkConfiguration',
            'protocolConfiguration', 'authorizerConfiguration',
            'requestHeaderConfiguration', 'lifecycleConfiguration')
                   if current.get(key)}
        control.update_agent_runtime(agentRuntimeId=runtime_id,
                                     environmentVariables=environment, **request)
        current = wait_runtime(control, runtime_id)
    return current

def apply_observability_config(runtime_arn, logging_config):
    """Apply real runtime environment configuration and provision the target log stream."""
    import config
    logs = boto3.client('logs', region_name=config.AWS_REGION)
    cw = logging_config['cloudWatchConfig']
    try:
        logs.create_log_group(logGroupName=cw['logGroupName'])
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    logs.put_retention_policy(logGroupName=cw['logGroupName'], retentionInDays=14)
    try:
        logs.create_log_stream(logGroupName=cw['logGroupName'], logStreamName='application')
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    runtime = update_runtime_environment(runtime_arn, {
        'AGENT_LOG_GROUP': cw['logGroupName'],
        'NOVAMART_LOG_LEVEL': cw['logLevel'],
        'NOVAMART_CLOUDWATCH_ENABLED': str(cw['enabled']).lower(),
        'NOVAMART_XRAY_ENABLED': str(logging_config['xRayConfig']['enabled']).lower(),
        'OTEL_TRACES_SAMPLER': 'always_on',
        'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT':
            f'https://xray.{config.AWS_REGION}.amazonaws.com/v1/traces',
    })
    # Runtime stdout has an additional service-managed log group.
    prefix = '/aws/bedrock-agentcore/runtimes/' + runtime['agentRuntimeId']
    for page in logs.get_paginator('describe_log_groups').paginate(logGroupNamePrefix=prefix):
        for group in page.get('logGroups', []):
            logs.put_retention_policy(logGroupName=group['logGroupName'], retentionInDays=14)
            if config._exports.get(config.PROJECT_NAME + '-LogEncryptionKeyArn'):
                logs.associate_kms_key(
                    logGroupName=group['logGroupName'],
                    kmsKeyId=config._exports[config.PROJECT_NAME + '-LogEncryptionKeyArn'])
    print(f"  CloudWatch: {cw['logGroupName']} ({cw['logLevel']}); X-Ray sampling: 100%")
    destination = boto3.client('xray', region_name=config.AWS_REGION).get_trace_segment_destination()
    if destination.get('Destination') != 'CloudWatchLogs':
        print('  Trace export prerequisite pending: the regional X-Ray destination must be '
              'CloudWatchLogs for the AWS OTLP endpoint. Review infrastructure/configure_trace_storage.py.')

