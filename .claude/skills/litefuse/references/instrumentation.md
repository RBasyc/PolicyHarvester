---
name: litefuse-observability
description: Instrument LLM applications with Litefuse tracing. Use when setting up Litefuse, adding observability to LLM calls, or auditing existing instrumentation.
---

# Litefuse Observability

Instrument LLM applications with Litefuse tracing, following best practices and tailored to your use case.

## When to Use

- Setting up Litefuse in a new project
- Auditing existing Litefuse instrumentation
- Adding observability to LLM calls

## Workflow

### 1. Assess Current State

Check the project:

- Is the Langfuse SDK installed? (the SDK is published as the `langfuse` package on npm/PyPI)
- What LLM frameworks are used? (OpenAI SDK, LangChain, LlamaIndex, Vercel AI SDK, etc.)
- Is there existing instrumentation?

**No integration yet:** Set up Litefuse using a framework integration if available. Integrations capture more context automatically and require less code than manual instrumentation.

**Integration exists:** Audit against baseline requirements below.

### 2. Verify Baseline Requirements

Every trace should have these fundamentals:

| Requirement               | Check                                                                                    | Why                                                    |
| ------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| Model name                | Is the LLM model captured?                                                               | Enables model comparison and filtering                 |
| Token usage               | Are input/output tokens tracked?                                                         | Enables automatic cost calculation                     |
| Good trace names          | Are names descriptive? (`chat-response`, not `trace-1`)                                  | Makes traces findable and filterable                   |
| Span hierarchy            | Are multi-step operations nested properly?                                               | Shows which step is slow or failing                    |
| Correct observation types | Are generations marked as generations?                                                   | Enables model-specific analytics                       |
| Sensitive data masked     | Is PII/confidential data excluded or masked?                                             | Prevents data leakage                                  |
| Trace input/output        | Does the trace capture meaningful input/output? Is input explicitly set to show only relevant data (e.g., user message), not all function args? | Makes traces readable in the UI and avoids leaking sensitive args |

Framework integrations (OpenAI, LangChain, etc.) handle model name, tokens, and observation types automatically. Prefer integrations over manual instrumentation.

Docs: https://litefuse.ai/docs/tracing

### 3. Explore Traces First

Once baseline instrumentation is working, encourage the user to explore their traces in the Litefuse UI before adding more context:

"Your traces are now appearing in Litefuse. Take a look at a few of them—see what data is being captured, what's useful, and what's missing. This will help us decide what additional context to add."

This helps the user:

- Understand what they're already getting
- Form opinions about what's missing
- Ask better questions about what they need

### 4. Discover Additional Context Needs

Determine what additional instrumentation would be valuable. **Infer from code when possible, only ask when unclear.**

**Infer from code:**

| If you see in code...                                | Infer             | Suggest                   |
| ---------------------------------------------------- | ----------------- | ------------------------- |
| Conversation history, chat endpoints, message arrays | Multi-turn app    | `session_id`              |
| User authentication, `user_id` variables             | User-aware app    | `user_id` on traces       |
| Multiple distinct endpoints/features                 | Multi-feature app | `feature` tag             |
| Customer/tenant identifiers                          | Multi-tenant app  | `customer_id` or tier tag |
| Feedback collection, ratings                         | Has user feedback | Capture as scores         |

**Only ask when not obvious from code:**

- "How do you know when a response is good vs bad?" → Determines scoring approach
- "What would you want to filter by in a dashboard?" → Surfaces non-obvious tags
- "Are there different user segments you'd want to compare?" → Customer tiers, plans, etc.

**Additions and their value:**

| Addition            | Why                                         | Docs                                                |
| ------------------- | ------------------------------------------- | --------------------------------------------------- |
| `session_id`        | Groups conversations together               | https://litefuse.ai/docs/tracing-features/sessions |
| `user_id`           | Enables user filtering and cost attribution | https://litefuse.ai/docs/tracing-features/users    |
| User feedback score | Enables quality filtering and trends        | https://litefuse.ai/docs/scores/overview           |
| `feature` tag       | Per-feature analytics                       | https://litefuse.ai/docs/tracing-features/tags     |
| `customer_tier` tag | Cost/quality breakdown by segment           | https://litefuse.ai/docs/tracing-features/tags     |

These are NOT baseline requirements—only add what's relevant based on inference or user input.

### 5. Guide to UI

After adding context, point users to relevant UI features:

- Traces view: See individual requests
- Sessions view: See grouped conversations (if session_id added)
- Dashboard: Build filtered views using tags
- Scores: Filter by quality metrics

## Framework Integrations

Prefer these over manual instrumentation:

| Framework     | Integration            | Docs                                                 |
| ------------- | ---------------------- | ---------------------------------------------------- |
| OpenAI SDK    | Drop-in replacement    | https://litefuse.ai/docs/integrations/openai        |
| LangChain     | Callback handler       | https://litefuse.ai/docs/integrations/langchain     |
| LlamaIndex    | Callback handler       | https://litefuse.ai/docs/integrations/llama-index   |
| Vercel AI SDK | OpenTelemetry exporter | https://litefuse.ai/docs/integrations/vercel-ai-sdk |
| LiteLLM       | Callback or proxy      | https://litefuse.ai/docs/integrations/litellm       |

Full list: https://litefuse.ai/docs/integrations

## Always Explain Why

When suggesting additions, explain the user benefit:

```
"I recommend adding session_id to your traces.

Why: This groups messages from the same conversation together.
You'll be able to see full conversation flows in the Sessions view,
making it much easier to debug multi-turn interactions.

Learn more: https://litefuse.ai/docs/tracing-features/sessions"
```

## Common Mistakes

| Mistake                                        | Problem                                             | Fix                                                                               |
| ---------------------------------------------- | --------------------------------------------------- | --------------------------------------------------------------------------------- |
| No `flush()` in scripts                        | Traces never sent                                   | Call `langfuse.flush()` before exit                                               |
| Flat traces                                    | Can't see which step failed                         | Use nested spans for distinct steps                                               |
| Generic trace names                            | Hard to filter                                      | Use descriptive names: `chat-response`, `doc-summary`                             |
| Logging sensitive data                         | Data leakage risk                                   | Mask PII before tracing                                                           |
| Not explicitly setting input with `@observe`   | All function args become trace input (including API keys, configs) | Python: use `langfuse.update_current_span(input=...)`. JS/TS: use `updateActiveObservation({ input: ... })`. Set only the relevant input (e.g., user message) |
| Manual instrumentation when integration exists | More code, less context                             | Use framework integration                                                         |
| SDK import before env vars loaded              | SDK initializes with missing/wrong credentials      | Import the SDK AFTER loading environment variables (e.g., after `load_dotenv()`)  |
| Wrong import order with OpenAI                 | SDK can't patch the OpenAI client                   | Import the SDK and call its setup BEFORE importing OpenAI client                  |
