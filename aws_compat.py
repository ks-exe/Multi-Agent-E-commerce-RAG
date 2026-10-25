"""Translate the classroom's legacy read APIs to verified current AWS resources.

These readers never manufacture successful configuration. They inspect the deployed
runtime, its associated memory and the actual CloudWatch log group.
"""
import boto3

def register():
    if boto3.DEFAULT_SESSION is None:
        boto3.setup_default_session()
    events = boto3.DEFAULT_SESSION._session

    def control_methods(class_attributes, **kwargs):
        def get_agent_runtime_logging_configuration(self, agentRuntimeId, **kw):
            runtime = self.get_agent_runtime(agentRuntimeId=agentRuntimeId)
            env = runtime.get('environmentVariables', {})
            group = env.get('AGENT_LOG_GROUP', '')
            logs = boto3.client('logs', region_name=self.meta.region_name)
            groups = logs.describe_log_groups(logGroupNamePrefix=group).get('logGroups', []) if group else []
            exists = any(g['logGroupName'] == group for g in groups)
            return {'loggingConfiguration': {
                'cloudWatchConfig': {
                    'logGroupName': group, 'logLevel': env.get('NOVAMART_LOG_LEVEL', ''),
                    'enabled': exists and env.get('NOVAMART_CLOUDWATCH_ENABLED') == 'true'},
                'xRayConfig': {
                    'enabled': env.get('NOVAMART_XRAY_ENABLED') == 'true' and
                               bool(env.get('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT')),
                    'samplingRate': 1.0 if env.get('OTEL_TRACES_SAMPLER') == 'always_on' else 0.0}}}
        class_attributes.setdefault('get_agent_runtime_logging_configuration',
                                    get_agent_runtime_logging_configuration)

    def data_methods(class_attributes, **kwargs):
        def get_agent_runtime(self, agentRuntimeId, **kw):
            control = boto3.client('bedrock-agentcore-control', region_name=self.meta.region_name)
            runtime = control.get_agent_runtime(agentRuntimeId=agentRuntimeId)
            memory_id = runtime.get('environmentVariables', {}).get('AGENTCORE_MEMORY_ID')
            memory_config = {'enabledMemoryTypes': [], 'storageDays': 0}
            if memory_id:
                memory = control.get_memory(memoryId=memory_id)['memory']
                summary = any(s.get('type') == 'SUMMARIZATION'
                              for s in memory.get('strategies', []))
                if memory['status'] == 'ACTIVE' and summary:
                    memory_config = {'enabledMemoryTypes': ['SESSION_SUMMARY'],
                                     'storageDays': memory['eventExpiryDuration']}
            runtime['memoryConfiguration'] = memory_config
            return runtime
        class_attributes.setdefault('get_agent_runtime', get_agent_runtime)

    events.register('creating-client-class.bedrock-agentcore-control', control_methods,
                    unique_id='novamart-current-logging-reader')
    events.register('creating-client-class.bedrock-agentcore', data_methods,
                    unique_id='novamart-current-memory-reader')

