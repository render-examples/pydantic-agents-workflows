# Plan: Fixes after the RenderWorkflows migration

These items came out of the review of the RenderWorkflows migration. Fix them in the next PR.

## 1. Return `tokens_used` when there are no claims

`verify_claims` in `backend/pipeline/verification.py` returns early when the claim list is empty. That early return has no `tokens_used` key, so `run_qa_pipeline` fails with a `KeyError` when it reads `verification_result["tokens_used"]`. This can happen when the answer is "I don't know" and has no claims. The bug existed before the migration.

Add `"tokens_used": 0` to the early return:

```python
if not claims:
    return {"verified_claims": [], "verification_rate": 0.0, "cost_usd": 0.0, "tokens_used": 0}
```

Add a test that calls `verify_claims([])` and checks that the result has `tokens_used` equal to `0`.

## 2. Confirm that a separate worker finds the right model

The tests in `tests/test_workflow_tasks.py` run each chained task in the same process, so the chained task always uses the same model instance as the parent. They do not prove that a separate worker resolves each agent's model correctly.

Run one question end to end with real keys:

1. Add a `.env` with the OpenAI and Anthropic keys.
2. Start the local task server:

   ```bash
   render workflows dev -- uv run render-workflows workflows.app:app
   ```

3. Start the API with `RENDER_USE_LOCAL_DEV=1` and ask one question from the frontend.
4. Confirm that the answer has sources, claims, an accuracy score, and quality scores.
5. Run `render workflows tasks runs list 'answer__model.request' --local --output json`. Confirm that each run completed and has the entry run as its `parentTaskRunId`. Repeat for `claims`, `accuracy`, `eval_openai`, and `eval_anthropic`.

This also completes steps 8.2 to 8.4 of `plan.md`.

## 3. Ingest docstring

The module docstring in `workflows/app.py` says that `ingest_all` fans out across subtasks. This is still correct, because the ingest tasks still use `ctx.run`. No change is needed.
