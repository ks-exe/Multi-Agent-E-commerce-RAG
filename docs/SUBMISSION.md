# Course submission checklist

- [x] Completed source, configuration template, infrastructure, and dependencies.
- [x] Original grader retained unchanged, with captured **120/120** output.
- [x] Five deployed scenarios checked against workflow state and memory events.
- [x] Distributed trace evidence, including three concurrent policy retrievers.
- [x] README, reproducible portfolio viewer, and three viewer screenshots.
- [x] **AWS Console X-Ray trace graph and named agent spans** captured for Task 7.

The starter asks for a screenshot showing the Orchestrator-to-Worker call chain
after a live request. Two actual AWS Console captures are now included:

- [Service graph for the request](images/aws-xray-service-map.png).
- [Orchestrator, Policy, three retrievers, and Communication spans](images/aws-policy-agent-spans.png).

These were captured on 26 October 2026 PKT (25 October UTC), from the deployed
policy request `1-6ade8235-b9e542f95bb170852565722e`. AWS reports 101 spans and
zero faults, errors, or throttles. The remaining three screenshots show the
separate local evidence viewer.

## Reproduce the console screenshots

1. Sign in to the deployment account and select **us-east-1**.
2. From the project folder, run `.\.venv\Scripts\python.exe -X utf8
   src/agent_orchestrator.py test` in PowerShell to generate fresh trace data.
3. Open the trace URL printed after the Premium return-policy question. The
   legacy X-Ray URL redirects to **CloudWatch → Traces → Trace details**.
4. In **Spans**, expand **Trace details**, then click its **Full screen** icon to
   capture the complete service graph. Keep the trace ID and metrics visible.
5. Exit full screen, select **List**, enter `invoke_agent` in **Filter spans**,
   and select **Use: "invoke_agent"** to apply the filter. Widen the **Name**
   column if needed. Capture all six agents with their OK status and durations.
6. Press **Win + Shift + S** to capture the desired area. Crop the account
   navigation bar and save the PNG files. The current captures already appear
   in the README; repeat only if you want a newer run.

The supplied map is the **per-request service graph** in Trace details. The
aggregate **Trace Map** was empty with the account's 1% indexing setting, while
the direct trace URL retrieved the full request successfully. The application's
100% sampling setting is separate from indexing. No indexing changes were made
to obtain these screenshots.

Submit the repository URL if the classroom submission form accepts GitHub links:

https://github.com/ks-exe/Multi-Agent-E-commerce-RAG

Ensure the reviewer can access the repository and follow any additional fields
or attachment requirements on the classroom submission page.
