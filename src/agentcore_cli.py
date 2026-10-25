"""Small adapter for the requested configure_runtime/deploy interface.

Uses AgentCore's current control API and uv to build a Linux ARM64 code artifact.
This module is project code; it is not an API supplied by the separate aws/agentcore-cli.
"""
import hashlib
import json
import os
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path

import boto3
import config
from runtime_support import wait_runtime

_settings = {}
_runtime_arn = ''

def configure_runtime(*, network_mode, protocol, execution_role_arn, env_vars):
    _settings.clear()
    _settings.update(network_mode=network_mode, protocol=protocol,
                     execution_role_arn=execution_role_arn, env_vars=dict(env_vars))

def deploy():
    global _runtime_arn
    if not _settings:
        raise RuntimeError('configure_runtime must be called before deploy.')
    root = Path(__file__).resolve().parents[1]
    requirements = root / 'requirements.txt'
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()[:16]
    packages = root / '.build' / ('packages-' + digest)
    packages.mkdir(parents=True, exist_ok=True)
    if not (packages / '.complete').exists():
        uv = shutil.which('uv')
        if not uv:
            raise RuntimeError('Install uv before building the ARM64 deployment artifact.')
        subprocess.run([
            uv, 'pip', 'install', '--python-platform', 'aarch64-manylinux2014',
            '--python-version', '3.13', '--only-binary', ':all:',
            '--target', str(packages), '-r', str(requirements)], check=True)
        (packages / '.complete').write_text(digest)
    archive = root / '.build' / ('deployment-' + uuid.uuid4().hex + '.zip')
    def add_file(bundle, path, name):
        info = zipfile.ZipInfo(name)
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        bundle.writestr(info, path.read_bytes(), compresslevel=1)

    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
        count = 0
        for directory, dirs, names in os.walk(packages):
            dirs[:] = [name for name in dirs if name != '__pycache__']
            for filename in names:
                if filename == '.complete':
                    continue
                path = Path(directory) / filename
                add_file(bundle, path, path.relative_to(packages).as_posix())
                count += 1
                if count % 500 == 0:
                    print(f'  Packaged {count} dependency files', flush=True)
        for path in (root / 'src').glob('*.py'):
            add_file(bundle, path, 'src/' + path.name)
        for name in ('config.py', 'aws_compat.py'):
            add_file(bundle, root / name, name)
    print(f'  Artifact ready: {archive.stat().st_size // 1024 // 1024} MiB', flush=True)
    s3 = boto3.client('s3', region_name=config.AWS_REGION)
    key = 'agentcore-artifacts/' + archive.name
    s3.upload_file(str(archive), config.POLICY_BUCKET, key)
    artifact = {'codeConfiguration': {
        'code': {'s3': {'bucket': config.POLICY_BUCKET, 'prefix': key}},
        'runtime': 'PYTHON_3_13', 'entryPoint': ['src/runtime_http.py']}}
    control = boto3.client('bedrock-agentcore-control', region_name=config.AWS_REGION)
    name = (config.PROJECT_NAME + '-runtime').replace('-', '_')
    current = None
    for page in control.get_paginator('list_agent_runtimes').paginate():
        for runtime in page.get('agentRuntimes', []):
            if runtime['agentRuntimeName'] == name:
                current = wait_runtime(control, runtime['agentRuntimeId'])
    env = dict(current.get('environmentVariables', {})) if current else {}
    env.update(_settings['env_vars'])
    request = dict(agentRuntimeArtifact=artifact, roleArn=_settings['execution_role_arn'],
                   environmentVariables=env,
                   networkConfiguration={'networkMode': _settings['network_mode']},
                   protocolConfiguration={'serverProtocol': _settings['protocol']})
    if current:
        response = control.update_agent_runtime(agentRuntimeId=current['agentRuntimeId'], **request)
    else:
        response = control.create_agent_runtime(agentRuntimeName=name, **request)
    _runtime_arn = response['agentRuntimeArn']
    ready = wait_runtime(control, response['agentRuntimeId'])
    endpoints = control.list_agent_runtime_endpoints(agentRuntimeId=ready['agentRuntimeId'])
    if not any(e.get('name') == 'DEFAULT' for e in endpoints.get('runtimeEndpoints', [])):
        control.create_agent_runtime_endpoint(agentRuntimeId=ready['agentRuntimeId'], name='DEFAULT')
    import time
    deadline = time.monotonic() + 300
    while True:
        endpoint = control.get_agent_runtime_endpoint(
            agentRuntimeId=ready['agentRuntimeId'], endpointName='DEFAULT')
        if endpoint['status'] == 'READY':
            break
        if 'FAILED' in endpoint['status'] or time.monotonic() > deadline:
            raise RuntimeError(f'Runtime endpoint did not become READY: {endpoint}')
        time.sleep(5)
    print('  Runtime deployed: ' + _runtime_arn)

def deployed_runtime_arn():
    if not _runtime_arn:
        raise RuntimeError('Runtime has not been deployed.')
    return _runtime_arn

