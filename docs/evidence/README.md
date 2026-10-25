# Evidence provenance

The AWS snapshot was captured on **24 October 2026 UTC**, in `us-east-1`, using
synthetic NovaMart customers and orders. It is a historical execution record,
not a continuous health check.

| File | Source and transformation |
| --- | --- |
| `grader-output.txt` | Original grader console output; ANSI formatting and deployment IDs removed. |
| `verification.json` | Verified workflow routes, saved responses, memory event counts, and span counts; session/customer IDs omitted. |
| `policy-trace.json` | Retrieved X-Ray documents flattened into unique span IDs, parent IDs, names and timestamps; account metadata and payloads omitted. Includes the private source document's SHA-256. |
| `guardrail-results.json` | Eight captured live Bedrock guardrail checks, using synthetic inputs. |
| `knowledge-bases.json` | Actual ingestion statistics and retrieval scores; resource identifiers omitted. |

`tools/build_portfolio.py --refresh-evidence` exports these fields from the local
AWS artifacts. The default `python tools/build_portfolio.py` embeds the committed
public data into `docs/index.html`; it makes no AWS calls.

`overview.png`, `trace.png`, and `conversations.png` are real headless-browser
screenshots of that viewer, captured with `tools/capture_portfolio.mjs`. The
viewer plots actual span timestamps and displays captured model responses.
Scenario questions are concise descriptions of the requests, while response
text is preserved from the run.

`aws-xray-service-map.png` and `aws-policy-agent-spans.png` are **actual AWS
Console screenshots**, captured from a fresh deployed policy request on
26 October 2026 PKT (25 October UTC). Its trace ID is
`1-6ade8235-b9e542f95bb170852565722e`. They show the per-request service graph
and the filtered agent span list. Only account navigation and whitespace were
cropped; no trace text, counts, status, or timing data was edited. The saved
24 October viewer snapshot above remains unchanged.

The 398-span total includes agent, tool, runtime, and AWS SDK spans across five
requests. The policy request accounts for 101 spans. Three-way overlap is
calculated as `min(end times) - max(start times)` for the three retriever agents.

The 120/120 score measures the original course rubric. Additional behavioral and
live checks provide independent evidence; no production-scale reliability,
latency, or retrieval-accuracy claim follows from this small scenario set.
