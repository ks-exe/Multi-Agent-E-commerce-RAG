"""Review, then explicitly apply the regional CloudWatch trace-storage prerequisite.

This changes the X-Ray destination for the entire account/region, not just NovaMart.
The default command is read-only. Use --apply only after approving the printed plan.
Reference: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Transaction-Search-getting-started.html
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import boto3
import config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--account-id', required=True, help='Expected account; prevents applying to another account.')
    args = parser.parse_args()
    identity = boto3.client('sts', region_name=config.AWS_REGION).get_caller_identity()
    account = identity['Account']
    if account != args.account_id:
        raise ValueError('AWS credentials do not match the explicitly selected account.')
    partition = identity['Arn'].split(':')[1]
    region = config.AWS_REGION
    xray = boto3.client('xray', region_name=region)
    current = xray.get_trace_segment_destination()
    policy = {'Version': '2012-10-17', 'Statement': [{
        'Sid': 'TransactionSearchXRayAccess', 'Effect': 'Allow',
        'Principal': {'Service': 'xray.amazonaws.com'}, 'Action': 'logs:PutLogEvents',
        'Resource': [f'arn:{partition}:logs:{region}:{account}:log-group:{name}:*'
                     for name in ('aws/spans', '/aws/application-signals/data')],
        'Condition': {
            'ArnLike': {'aws:SourceArn': f'arn:{partition}:xray:{region}:{account}:*'},
            'StringEquals': {'aws:SourceAccount': account}}}]}
    plan = {'account': account, 'region': region,
            'currentDestination': current.get('Destination'),
            'proposedDestination': 'CloudWatchLogs',
            'policyName': config.PROJECT_NAME + '-xray-trace-delivery', 'policy': policy,
            'scope': 'All future traces in this AWS account and region',
            'indexingRuleChanges': None}
    print(json.dumps(plan, indent=2), flush=True)
    target = Path(__file__).resolve().parents[1] / 'artifacts'
    target.mkdir(exist_ok=True)
    (target / 'trace-storage-plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    if not args.apply:
        print('Review only; no AWS settings changed.')
        return
    boto3.client('logs', region_name=region).put_resource_policy(
        policyName=plan['policyName'], policyDocument=json.dumps(policy))
    if current.get('Destination') != 'CloudWatchLogs':
        xray.update_trace_segment_destination(Destination='CloudWatchLogs')
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        state = xray.get_trace_segment_destination()
        print(json.dumps({key: state.get(key) for key in ('Destination', 'Status')}), flush=True)
        if state.get('Destination') == 'CloudWatchLogs' and state.get('Status') == 'ACTIVE':
            return
        time.sleep(20)
    raise TimeoutError('CloudWatch trace storage is still transitioning; inspect X-Ray status before retrying.')


if __name__ == '__main__':
    main()
