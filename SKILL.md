---
name: project-vega
description: Work on Project Vega, the Coaction Binding Authority underwriting assistant. Use when modifying this repo's FastAPI backend, React/Vite frontend, Strands underwriting agent, Bedrock KB retrieval, GPT-5.2/OpenAI parameter handling, DynamoDB sessions, Cognito auth, streaming chat, citations, prompts, guardrails, or pre-push verification workflows.
---

# Project Vega

Project Vega is Coaction's Binding Authority underwriting assistant platform. The product is a chat app for underwriting questions over Coaction manuals and internal guidelines. It uses a FastAPI backend, React/Vite frontend, Cognito auth, DynamoDB sessions, Bedrock Knowledge Base retrieval, and a Strands-based underwriting agent.

## Current Runtime Shape

- Backend entrypoint: `app/main.py`
- Agent API: `app/routers/agent_router.py`
- Orchestration service: `services/agent_service.py`
- Main agent: `agents/underwriting_agent.py`
- Retrieval tool: `agents/tools/retriever.py`
- Prompt template: `agents/prompts.py`
- Domain models: `domain/models.py`
- Frontend chat: `frontend/src/components/chat/`
- Frontend API helpers: `frontend/src/lib/chat.ts`, `frontend/src/store/api/`
- Active profile: `profiles/coaction-underwriting.json`

The frontend uses server-sent events via `/v1/agents/{agent_id}/invoke/stream`. Keep the non-streaming `/invoke` path working for compatibility and tests.

## Product Rules

- The assistant answers only Binding Authority, underwriting, coverage, class code, form, endorsement, manual, guideline, and Coaction-related questions.
- Generic writing tasks such as "rephrase this" must be rejected unless the text is clearly insurance or underwriting related.
- Coverage availability answers must be conservative. Do not answer "Yes" unless the exact coverage or option term appears in retrieved manual text.
- For "Where is X mentioned?", point to a section only if exact term `X` appears in retrieved evidence.
- When advising underwriter contact, use exactly: `For authoritative guidance, please contact your Coaction underwriter.`
- Do not expose internal guidelines as public citations.

## Model Rules

- The runtime is pinned to `gpt-5.2` in `services/agent_service.py`; the UI must not show or allow model selection.
- GPT-5.x OpenAI Chat Completions params use `max_completion_tokens`, not `max_tokens`.
- GPT-5.x should still receive `temperature`; current default is `0.6`.
- O-series compatibility should omit `temperature`.
- Do not reintroduce frontend `model_id` overrides unless the product requirement changes.

## Citations

The citation system has two layers:

- The retriever injects `Citation ID: S1`, `S2`, etc. into model context for public manual chunks.
- The model emits a hidden `<used_sources>` JSON block. The backend strips it and resolves only source IDs that match retrieved metadata.

Rules:

- Cap citations at 3.
- Never invent URLs or accept model-generated random links.
- Do not fall back to all retrieved sources.
- The visible frontend citation block format is fixed:

```text
Citations -
Source Manual:
Class Code:
Link:
```

- Class code comes from retriever metadata when available; property/manual sections may show `N/A`.
- Session persistence stores citations as structured metadata, not injected answer text.

## Streaming

Streaming response flow:

1. `ChatPanel.tsx` calls `streamAgentResponse()` in `frontend/src/lib/chat.ts`.
2. `streamAgentResponse()` posts to `/v1/agents/{agent_id}/invoke/stream`.
3. `agent_router.py` returns SSE events.
4. `AgentService.stream_invoke()` yields `session`, `delta`, and final response events.
5. `UnderwritingAgent.stream()` streams visible answer text and withholds hidden citation/follow-up blocks.

Keep the UI loading state as jumping dots only. Do not show "Thinking" text or `<thinking>` content.

## Retrieval

- `search_manuals` is the only knowledge tool.
- Use `build_scoped_search_manuals_tool()` for per-invocation source capture; avoid relying only on global or contextvar state for citations.
- `_format_retrieved_documents()` must preserve `content_text` in source metadata so answer validation can verify exact evidence.
- Internal guideline chunks use `INTERNAL_DO_NOT_CITE`.
- Public manual chunks receive stable `S1`, `S2`, etc. IDs per invocation.

## Frontend

- React/Vite app lives under `frontend/`.
- Keep UI utilitarian and work-focused.
- Do not add a model picker or model labels.
- Use `lucide-react` icons where icons are needed.
- Render citations from `message.citations`, not by appending markdown to the answer.
- The app uses RTK Query for most API calls; streaming uses direct `fetch` because RTK Query does not naturally expose response body streams.
- After streaming final response, invalidate the `Session` list tag so the sidebar updates.

## Auth And Sessions

- Auth uses Cognito JWTs.
- Frontend auth token lives under `AUTH_STORAGE_KEY` in `frontend/src/lib/authSession.ts`.
- `baseApi.ts` and streaming fetches must send both:
  - `Authorization: Bearer <token>`
  - `X-Amzn-Bedrock-AgentCore-Runtime-Custom-Authorization: Bearer <token>`
- Sessions are stored in DynamoDB through `DynamoDBAdapter`.
- Persist assistant messages with structured `citations` so session reloads render source blocks correctly.

## Prompt Work

When editing `agents/prompts.py`:

- Keep rules explicit and testable.
- Preserve the hidden `<used_sources>` protocol.
- Add prompt rules and deterministic backend checks together when behavior is safety-critical.
- Off-topic and missing-data wording should match backend policy where possible.
- Do not ask the model to show raw links in the main answer.

## Verification

Use the repo script before finishing code changes:

```powershell
python scripts\pre_push_check.py
```

This runs Ruff check, Ruff format check, pytest, frontend lint, and frontend typecheck.

Useful focused checks:

```powershell
python -m pytest
npm.cmd run lint --prefix frontend
npm.cmd run typecheck --prefix frontend
```

For frontend production build:

```powershell
npm.cmd run build
```

On Windows sandboxed runs, Vite/Tailwind native dependencies may fail with `spawn EPERM` or native module load errors. If the build is necessary and fails for that reason, request approval to rerun outside the sandbox.

## Editing Guidance

- Prefer existing patterns over new abstractions.
- Use `apply_patch` for manual edits.
- Use `rg` for search.
- Keep unrelated user changes intact.
- Add tests when changing prompt-critical backend behavior, citations, streaming, auth, or model params.
- Restart or reload the backend after changing profiles, prompts, or agent service behavior because agents/profiles are cached.
