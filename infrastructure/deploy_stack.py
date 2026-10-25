"""Validate a CloudFormation change set, then deploy the NovaMart foundation."""
import json
import time
from pathlib import Path
import boto3
from botocore.exceptions import ClientError

def main():
    cf = boto3.client('cloudformation', region_name='us-east-1')
    name = 'udacity-agentcore'
    template = Path(__file__).with_name('starter_stack.yaml').read_text()
    cf.validate_template(TemplateBody=template)
    try:
        stack = cf.describe_stacks(StackName=name)['Stacks'][0]
        kind = 'CREATE' if stack['StackStatus'] == 'REVIEW_IN_PROGRESS' else 'UPDATE'
    except ClientError as error:
        if error.response['Error']['Code'] != 'ValidationError' or 'does not exist' not in str(error):
            raise
        kind = 'CREATE'
    response = cf.create_change_set(
        StackName=name, ChangeSetName='novamart-' + str(int(time.time())),
        ChangeSetType=kind, TemplateBody=template, Capabilities=['CAPABILITY_NAMED_IAM'])
    change_id = response['Id']
    deadline = time.monotonic() + 300
    while True:
        change = cf.describe_change_set(ChangeSetName=change_id)
        if change['Status'] in ('CREATE_COMPLETE', 'FAILED'):
            break
        if time.monotonic() > deadline:
            raise TimeoutError('Change set validation timed out.')
        time.sleep(5)
    if change['Status'] == 'FAILED':
        if "didn't contain changes" in change.get('StatusReason', ''):
            print('Foundation already matches the template.')
            return
        raise RuntimeError(change.get('StatusReason'))
    events = cf.describe_events(ChangeSetName=change_id)
    validation = [e for e in events.get('OperationEvents', []) if e.get('EventType') == 'VALIDATION_ERROR']
    if any(e.get('ValidationFailureMode') == 'FAIL' for e in validation):
        raise RuntimeError(json.dumps(validation, default=str))
    print('Validated changes: ' + ', '.join(
        c['ResourceChange']['LogicalResourceId'] for c in change.get('Changes', [])), flush=True)
    cf.execute_change_set(ChangeSetName=change_id)
    deadline = time.monotonic() + 1200
    last = None
    while time.monotonic() < deadline:
        stack = cf.describe_stacks(StackName=name)['Stacks'][0]
        status = stack['StackStatus']
        if status != last:
            print('Stack status: ' + status, flush=True)
            last = status
        if status in ('CREATE_COMPLETE', 'UPDATE_COMPLETE'):
            target = Path(__file__).resolve().parents[1] / 'artifacts'
            target.mkdir(exist_ok=True)
            (target / 'foundation.json').write_text(json.dumps(stack, default=str, indent=2))
            return
        if 'ROLLBACK' in status or status.endswith('FAILED'):
            failures = cf.describe_events(StackName=name, Filters={'FailedEvents': True})
            raise RuntimeError(json.dumps(failures, default=str))
        time.sleep(10)
    raise TimeoutError('Foundation deployment timed out.')

if __name__ == '__main__':
    main()
