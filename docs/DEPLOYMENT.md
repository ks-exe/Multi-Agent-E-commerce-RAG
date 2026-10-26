# Deploy NovaMart to AWS

The recorded deployment was verified in `us-east-1` on 24 October 2026.
Use a dedicated development account or environment for these synthetic fixtures.
The commands provision AWS resources and incur AWS usage charges.

## Prerequisites

- Python 3.13, AWS CLI v2, and AWS credentials with permission to provision the
  CloudFormation template and manage Bedrock/AgentCore resources.
- Access to Claude Haiku 4.5, Claude Sonnet 4.5, and Titan Text Embeddings v2 in
  the configured region. Complete Anthropic's first-use form where required.
- `uv` on `PATH` for the Linux ARM64 deployment artifact build.

After cloning and activating a virtual environment:

```bash
python -m pip install -r requirements.txt
python -m pip install uv
aws sts get-caller-identity --region us-east-1
```

The repository includes a credential-free `.env` deployment snapshot for the
reviewer. For a deployment in your own AWS account, replace it with the template
and populate your own IDs. Preserve your existing local settings if you have
already deployed this project. In PowerShell:

```powershell
Copy-Item .env.example .env
```

Keep `PROJECT_NAME=udacity-agentcore` and `AWS_REGION=us-east-1` for the supplied
scripts. The root `.env` stores public deployment identifiers and is included
for review; it is excluded from the runtime package. AWS credentials come from
the normal AWS credential chain and must never be added to `.env`.

## Foundation and Knowledge Bases

```bash
python infrastructure/deploy_stack.py
python infrastructure/seed_data.py
python infrastructure/setup_knowledge_bases.py
python config.py
```

`deploy_stack.py` validates a CloudFormation change set, deploys
`infrastructure/starter_stack.yaml`, and waits for completion. The stack provides
three DynamoDB tables, a policy document bucket, S3 Vectors indexes, execution
roles, and logging resources.

`seed_data.py` resets four synthetic customers and twelve orders and uploads six
policy documents. Run it intentionally: reseeding changes order dates and can
replace recorded demo return state.

KB setup creates or finds the Returns, Shipping, and Warranty Knowledge Bases,
starts ingestion, waits for completion, verifies retrieval, and saves IDs in
`.env`. Each uses Titan Text Embeddings v2, a 1,024-dimensional cosine vector
index, and its corresponding `policies/returns/`, `policies/shipping/`, or
`policies/warranty/` S3 prefix.

## Trace storage

The ADOT exporter sends OTLP traces to X-Ray's CloudWatch-backed destination.
Inspect the current configuration first:

```bash
python infrastructure/configure_trace_storage.py --account-id YOUR_ACCOUNT_ID
```

If needed, apply the reviewed configuration:

```bash
python infrastructure/configure_trace_storage.py --account-id YOUR_ACCOUNT_ID --apply
```

The script verifies the account, configures the scoped delivery policy, sets the
regional destination to `CloudWatchLogs`, and waits for `ACTIVE`. This setting
affects future traces across that account and region. It does not alter the
existing trace indexing rule. Application sampling and trace indexing are
separate: recording all spans does not guarantee every trace appears in indexed
searches or the service map.

## Runtime deployment

```bash
python src/agent_orchestrator.py deploy
```

Deployment builds the agent graph, creates or reuses the published guardrail,
packages Linux ARM64 dependencies, deploys a PUBLIC/HTTP AgentCore runtime,
creates summary memory with seven-day event expiry, and configures logs and
traces. PUBLIC describes the network mode; runtime invocation still requires
AWS IAM authorization. Runtime and memory IDs are saved in `.env`.

The optional starter Gateway is disabled by default. The application uses
Strands tools directly and does not require that Gateway or separate Lambda
tool functions.

## Validate and capture evidence

```bash
python tests/test_workflow.py
python tests/test_agent.py all
python tests/test_live_guardrail.py
```

The offline tests exercise routing, terminal tool behavior, concurrent retrieval,
state versions, session ownership, and return boundaries. The original grader
reads AWS configuration. Live guardrail checks call Bedrock.

Capture deployed scenarios in PowerShell:

```powershell
New-Item -ItemType Directory -Force artifacts | Out-Null
python -X utf8 tests/test_agent.py all | Tee-Object -FilePath artifacts/test-results.txt
python -X utf8 src/agent_orchestrator.py test | Tee-Object -FilePath artifacts/live-scenarios.txt
python infrastructure/capture_traces.py
```

On macOS/Linux, use `mkdir -p artifacts` and `tee` in place of `New-Item` and
`Tee-Object`. The scenario command calls the deployed runtime and records a
return against synthetic order data. It prints session IDs and X-Ray trace IDs.
The capture script retrieves CloudWatch-backed X-Ray documents, reads DynamoDB
state and AgentCore events, and checks route order and retriever overlap.

The full raw outputs stay in ignored `artifacts/`. The committed evidence in
`docs/evidence/` is the dated portfolio snapshot; see its [provenance](evidence/README.md).

## Rebuild the portfolio screenshots

The viewer works offline and contains no AWS credentials. Rebuild from the
committed public evidence:

```bash
python tools/build_portfolio.py
node tools/capture_portfolio.mjs
```

Screenshot capture requires Node.js 22+ and Chrome or Edge. It launches a hidden,
headless browser with a separate temporary profile under `.build/`. Set
`BROWSER_PATH` if the executable is not in a standard location. The script
verifies tab navigation, all saved responses, and mobile overflow before exiting.

To export a new verified AWS run, use `python tools/build_portfolio.py
--refresh-evidence` after generating the source artifacts. Review the dated
labels and documentation before publishing a new snapshot.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Missing credentials / wrong account | Run `aws sts get-caller-identity`; check the active AWS profile. |
| Claude access denied | Confirm Anthropic first-use requirements, model availability, and IAM permissions. |
| Missing CloudFormation exports | Deploy the foundation before running `config.py`; keep the supplied project name. |
| KB returns no passages | Check all three ingestion jobs and their S3 prefixes. |
| OTLP export returns HTTP 400 | Check the regional X-Ray destination and delivery resource policy. |
| Trace missing from service map | Check time range, region, and indexing configuration; use direct trace retrieval for verification. |
| Grader displays Claude 3 labels | These are original labels. The model constants and deployed run use Claude 4.5. |

The setup scripts manage resources in both CloudFormation and service APIs.
Deleting only the stack does not remove all AgentCore, Knowledge Base, memory,
or guardrail resources; review those separately when retiring a deployment.
