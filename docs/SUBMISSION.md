# Course submission checklist

- [x] Completed source, configuration template, infrastructure, and dependencies.
- [x] Original grader retained unchanged, with captured **120/120** output.
- [x] Five deployed scenarios checked against workflow state and memory events.
- [x] Distributed trace evidence, including three concurrent policy retrievers.
- [x] README, reproducible portfolio viewer, and three viewer screenshots.
- [ ] **AWS Console X-Ray Service Map screenshot** required by the starter's Task 7.

The starter explicitly asks for a screenshot showing the Orchestrator-to-Worker
call chain after a live request. The screenshots in `docs/images/` show the local
evidence viewer and do not replace that console deliverable.

## Capture the console deliverable

1. Sign in to the deployment account and select **us-east-1**.
2. Run `python src/agent_orchestrator.py test` to generate fresh trace data.
3. Open **X-Ray → Service map** (or the corresponding CloudWatch trace view),
   select the run's time range, and inspect the request graph.
4. Capture the full Orchestrator/Worker graph. Crop or mask unrelated account
   details before adding the screenshot as `docs/images/aws-xray-service-map.png`.
5. Add the image to the README and mark this checklist item complete.

If the graph is empty, check indexing and time range. The recorded application's
100% sampling setting is separate from AWS's trace indexing configuration.
The saved trace documents verify delivery even when indexed search is empty.

Submit the repository URL if the classroom submission form accepts GitHub links:

https://github.com/ks-exe/Multi-Agent-E-commerce-RAG

Ensure the reviewer can access the repository and follow any additional fields
or attachment requirements on the classroom submission page.
