"""ADOT exports real Strands and AWS SDK spans to X-Ray, with correlated CloudWatch logs."""
import logging
import threading
from contextlib import contextmanager

_lock = threading.Lock()
_provider = None
_log_provider = None

def configure():
    global _provider, _log_provider
    with _lock:
        if _provider is not None:
            return _provider
        import boto3
        import config
        from amazon.opentelemetry.distro.exporter.otlp.aws.traces.otlp_aws_span_exporter import OTLPAwsSpanExporter
        from amazon.opentelemetry.distro.exporter.otlp.aws.logs.otlp_aws_log_record_exporter import OTLPAwsLogRecordExporter
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
        from opentelemetry.instrumentation.threading import ThreadingInstrumentor

        resource = Resource.create({'service.name': 'NovaMart-Orchestrator',
                                    'cloud.region': config.AWS_REGION})
        _provider = TracerProvider(resource=resource, sampler=ALWAYS_ON,
                                   id_generator=AwsXRayIdGenerator())
        session = boto3.DEFAULT_SESSION._session
        exporter = OTLPAwsSpanExporter(
            aws_region=config.AWS_REGION, session=session,
            endpoint=f'https://xray.{config.AWS_REGION}.amazonaws.com/v1/traces')
        _provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(_provider)
        _log_provider = LoggerProvider(resource=resource)
        log_exporter = OTLPAwsLogRecordExporter(
            aws_region=config.AWS_REGION, session=session,
            log_group=config.AGENT_LOG_GROUP, log_stream='application',
            endpoint=f'https://logs.{config.AWS_REGION}.amazonaws.com/v1/logs')
        _log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        logger = logging.getLogger('novamart')
        logger.setLevel(logging.INFO)
        logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=_log_provider))
        BotocoreInstrumentor().instrument()
        ThreadingInstrumentor().instrument()
        return _provider

def flush():
    if _provider:
        _provider.force_flush(timeout_millis=30000)
    if _log_provider:
        _log_provider.force_flush(timeout_millis=30000)

@contextmanager
def agent_call(agent_name):
    """Trace a logical agent dependency around its actual synchronous invocation.

    X-Ray infers named dependency nodes from CLIENT spans with peer.service.
    These agents execute in the same runtime, not separate network services;
    novamart.agent.execution makes that distinction explicit in the trace.
    Native Strands spans remain children, preserving model/tool timings.
    """
    from opentelemetry import trace
    with trace.get_tracer('novamart.agent-delegation').start_as_current_span(
            'delegate ' + agent_name, kind=trace.SpanKind.CLIENT,
            attributes={'peer.service': agent_name,
                        'gen_ai.agent.name': agent_name,
                        'novamart.agent.execution': 'in_process'}) as span:
        yield span

@contextmanager
def request_span(session_id):
    configure()
    from opentelemetry import trace
    with trace.get_tracer('novamart.workflow').start_as_current_span('NovaMart-Orchestrator') as span:
        span.set_attribute('session.id', session_id)
        value = f'{span.get_span_context().trace_id:032x}'
        trace_id = f'1-{value[:8]}-{value[8:]}'
        logging.getLogger('novamart').info('Request started; session=%s trace_id=%s', session_id, trace_id)
        try:
            yield trace_id
        finally:
            logging.getLogger('novamart').info('Request finished; session=%s trace_id=%s', session_id, trace_id)

