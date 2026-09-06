# Telegram AI Agent — Implementation Plan (Ollama-based)

A phase-by-phase build plan with concrete system prompts you can drop into your agent orchestrator. Each phase is independently demoable.

---

## Phase 1: Solidify the Core Agent Loop

### Goal
Make tool-calling reliable before adding features. This is 80% of what makes a local-LLM agent "feel" production-grade.

### Implementation steps

1. **Define tool schemas** (Pydantic):

```python
from pydantic import BaseModel, Field
from typing import Literal

class SendEmailArgs(BaseModel):
    to: str = Field(..., description="Recipient email address")
    subject: str
    body: str

class SearchInboxArgs(BaseModel):
    query: str
    max_results: int = 5

class ReadEmailArgs(BaseModel):
    email_id: str

class DraftReplyArgs(BaseModel):
    email_id: str
    instructions: str

class ToolCall(BaseModel):
    tool: Literal["send_email", "search_inbox", "read_email", "draft_reply", "none"]
    args: dict
    reasoning: str = Field(..., description="Why this tool was chosen")
```

2. **Force JSON output** — use Ollama's `format: "json"` parameter in the `/api/chat` call so the model can't return free text when you need structured output.

3. **Validation + retry loop**:

```python
def call_agent(user_message, history):
    response = ollama_chat(system=AGENT_SYSTEM_PROMPT, messages=history + [user_message], format="json")
    try:
        parsed = ToolCall.model_validate_json(response)
    except ValidationError as e:
        # one retry with the error fed back
        correction = f"Your last output was invalid JSON or missing fields. Error: {e}. Return ONLY valid JSON matching the schema."
        response = ollama_chat(system=AGENT_SYSTEM_PROMPT, messages=history + [user_message, {"role":"assistant","content":response}, {"role":"user","content":correction}], format="json")
        parsed = ToolCall.model_validate_json(response)
    return parsed
```

### System Prompt — Phase 1 (Intent Router + Tool Selector)

```
You are an email assistant agent running locally via Ollama. Your job is to read the user's message and decide which single tool to call next. You do NOT execute actions yourself — you only select a tool and provide arguments. A separate system will execute the tool and give you the result.

Available tools:
1. send_email(to: str, subject: str, body: str) — Sends an email. Only use this when the user has given clear intent to send, and you have all three fields. If any field is missing, use tool "none" and ask the user for the missing info instead.
2. search_inbox(query: str, max_results: int) — Searches the user's inbox for emails matching a query.
3. read_email(email_id: str) — Reads the full content of a specific email by ID.
4. draft_reply(email_id: str, instructions: str) — Drafts a reply to an email without sending it.
5. none — Use this when no tool is needed (e.g. the user is just chatting, asking a question you can answer directly, or you need to ask a clarifying question first).

Rules:
- NEVER call send_email unless the user has explicitly confirmed intent to send AND you have a valid recipient, subject, and body. If unsure, choose "none" and ask a clarifying question.
- Only call ONE tool per turn. Do not chain tools yourself — the system will call you again with the tool result if more steps are needed.
- Always include a short "reasoning" field explaining why you picked this tool.
- Respond with ONLY valid JSON matching this schema, no other text:
{"tool": "<tool_name>", "args": {...}, "reasoning": "<short explanation>"}

Examples:
User: "email john@x.com about the meeting tomorrow, tell him it's moved to 3pm"
Response: {"tool": "send_email", "args": {"to": "john@x.com", "subject": "Meeting Update", "body": "Hi John, the meeting has been moved to 3pm tomorrow. Thanks!"}, "reasoning": "User gave explicit recipient, clear intent to send, and enough detail to draft a complete email."}

User: "did anyone email me about the invoice?"
Response: {"tool": "search_inbox", "args": {"query": "invoice", "max_results": 5}, "reasoning": "User is asking about existing emails, not sending. A search is needed first."}

User: "send an email to my boss"
Response: {"tool": "none", "args": {}, "reasoning": "Missing recipient email, subject, and body content. Need to ask the user for details before drafting."}
```

### Deliverable
Build a test set of 20-30 prompts covering clear requests, ambiguous requests, and multi-step requests. Log tool-selection accuracy. Target >90% correct tool choice.

---

## Phase 2: Safety & UX (Confirmation Gates)

### Goal
No irreversible action fires without explicit human approval — this is the detail that makes interviewers sit up.

### Implementation steps

1. After the agent returns `send_email`, **do not call the Gmail API yet**. Instead render a Telegram message with inline buttons:

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def build_confirmation(draft):
    text = f"📧 *Draft Email*\nTo: {draft.to}\nSubject: {draft.subject}\n\n{draft.body}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Send", callback_data=f"confirm_send:{draft.id}"),
        InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{draft.id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{draft.id}"),
    ]])
    return text, keyboard
```

2. Store the pending draft in a short-lived table (SQLite `pending_actions`) keyed by draft ID, with a TTL (e.g. 10 minutes) so stale drafts auto-expire.

3. On "Edit," re-prompt the agent with the user's correction and the previous draft as context (Phase 2 system prompt below).

4. **Rate limiting**: track sends per user per hour in the same DB; hard-cap it (e.g. 20/hour) and reject with a friendly message if exceeded.

### System Prompt — Phase 2 (Edit/Revision mode)

```
You are revising a previously drafted email based on user feedback. You will be given the original draft and the user's requested change. Return an updated draft using the same JSON schema as before. Do not ask clarifying questions unless the user's feedback is too vague to act on (e.g. "make it better" with no direction — in that case, ask what tone or content they want changed).

Original draft:
To: {to}
Subject: {subject}
Body: {body}

User's requested change: "{user_feedback}"

Respond with ONLY valid JSON:
{"tool": "send_email", "args": {"to": "...", "subject": "...", "body": "..."}, "reasoning": "what you changed and why"}
```

### Deliverable
You can live-demo this without fear — every send requires a tap. Add a screen recording of the confirm/edit/cancel flow to your README.

---

## Phase 3: Real Integrations (Breadth)

### Goal
Show multi-tool orchestration, not just one API wrapped in a chatbot.

### Add

1. **Gmail OAuth2** (`google-auth-oauthlib`) — replace app-password auth. Store refresh tokens encrypted (e.g. `cryptography.fernet`) per user in your DB.
2. **Calendar tool**:

```python
class CreateEventArgs(BaseModel):
    title: str
    start_time: str  # ISO 8601
    end_time: str
    attendees: list[str] = []

class CheckAvailabilityArgs(BaseModel):
    date: str
    duration_minutes: int
```

3. **RAG over inbox** — embed recent emails locally (e.g. `sentence-transformers/all-MiniLM-L6-v2`, runs fine on CPU) into Chroma or FAISS, so the agent can answer questions grounded in actual email content instead of hallucinating.

### System Prompt — Phase 3 (Multi-tool orchestrator)

```
You are a personal assistant agent with access to email, calendar, and inbox search tools, running locally via Ollama. You solve multi-step requests by calling ONE tool at a time. After each tool result is returned to you, decide the next tool to call, or return "done" with a final summary for the user.

Available tools:
1. send_email(to, subject, body)
2. search_inbox(query, max_results)
3. read_email(email_id)
4. draft_reply(email_id, instructions)
5. create_event(title, start_time, end_time, attendees)
6. check_availability(date, duration_minutes)
7. search_emails_semantic(query) — searches inbox by meaning, not just keywords, using embeddings. Use this for vague queries like "what did John say about the contract".
8. done — use when the task is fully complete or you need to hand a final answer/summary back to the user.

Rules:
- Break multi-step requests into individual tool calls. Example: "check my calendar tomorrow and email John a time that works" requires check_availability, THEN send_email — one call per turn.
- Never call send_email or create_event without enough information. Use "done" with a clarifying question if info is missing.
- Ground your answers in tool results — do not invent email content, dates, or names not present in a tool result.
- Respond with ONLY valid JSON: {"tool": "<name>", "args": {...}, "reasoning": "..."}

You will receive the conversation history plus a running log of tool calls and their results in this session. Use that log to avoid repeating calls and to know what step you're on.
```

### Deliverable
Demo a chained request end-to-end: "check my availability tomorrow afternoon and email Sarah a time that works" → check_availability → send_email (with confirmation gate from Phase 2 still active).

---

## Phase 4: Reliability & Observability

### Goal
This phase is what turns "cool demo" into "engineered system" — most portfolio projects skip it entirely, so it's high leverage for a resume.

### Add

1. **Retry/backoff** on both Ollama calls and Gmail/Calendar API calls:

```python
from tenacity import retry, wait_exponential, stop_after_attempt

@retry(wait=wait_exponential(min=1, max=10), stop=stop_after_attempt(3))
def call_ollama(payload):
    ...
```

2. **Async job queue** for scheduled/background work (Celery + Redis, or lighter-weight `APScheduler` if you want to keep infra simple):

```python
@scheduler.scheduled_job("cron", hour=8, minute=0)
def morning_digest():
    for user in get_active_users():
        unread = search_inbox_tool(user, query="is:unread", max_results=10)
        summary = call_ollama(system=DIGEST_PROMPT, user_message=str(unread))
        send_telegram_message(user.chat_id, summary)
```

### System Prompt — Phase 4 (Daily digest / summarizer)

```
You are summarizing a user's unread emails into a short morning briefing for Telegram. You will be given a list of emails (sender, subject, snippet). Produce a concise digest.

Rules:
- Group by priority: flag anything that looks urgent or time-sensitive first (e.g. contains "deadline", "urgent", "asap", meeting requests, invoices due).
- Limit to 5-8 bullet points max. If there are more emails, summarize the rest as "+N more, mostly newsletters/notifications."
- Use plain, scannable language — this is read on a phone in a few seconds.
- Do not fabricate details not present in the email data provided.
- Output format (plain text, not JSON, since this is shown directly to the user):

📬 *Morning Digest — {date}*
🔴 Urgent:
- ...
🟡 Needs response:
- ...
🟢 FYI:
- ...
```

3. **Evaluation harness** — a script that runs your fixed test-prompt set against the agent nightly/on-demand and reports:
   - Tool-selection accuracy (%)
   - JSON validity rate (%)
   - Average retries needed

```python
def run_eval(test_cases):
    results = []
    for case in test_cases:
        output = call_agent(case["input"], history=[])
        correct = output.tool == case["expected_tool"]
        results.append({"input": case["input"], "correct": correct, "output": output.dict()})
    accuracy = sum(r["correct"] for r in results) / len(results)
    return accuracy, results
```

4. **Dockerize**:

```yaml
# docker-compose.yml (sketch)
services:
  bot:
    build: .
    depends_on: [redis, ollama]
  ollama:
    image: ollama/ollama
    volumes: ["ollama_data:/root/.ollama"]
  redis:
    image: redis:7
volumes:
  ollama_data:
```

### Deliverable
A README section with real numbers: "94% tool-selection accuracy across 50 test cases, 0.3 avg retries per call, one-command deploy via `docker-compose up`."

---

## Phase 5: Presentation Polish

1. **60–90 second demo video**: show a normal send, a cancel, an edit, and one chained multi-tool request (calendar + email).
2. **README structure**:
   - Architecture diagram (agent loop, tool layer, confirmation gate, job queue)
   - Design decisions section — explicitly explain the local-model tradeoffs (why Ollama, why you added schema validation/retry logic, quantization choice)
   - Eval numbers from Phase 4
3. **Resume bullet** (updated for the full build):

> "Built a self-hosted agentic Telegram assistant using Ollama (Llama 3.1) for local LLM inference, with a custom tool-calling reliability layer (JSON-schema validation, retry/fallback correction) achieving 94% tool-selection accuracy across a 50-case eval suite; integrated OAuth-secured Gmail/Calendar with confirmation-gated actions and rate limiting to prevent unintended sends; added async scheduled digests via Celery/Redis and full Docker deployment."

---

## Suggested build order if time-constrained
**Phase 1 → Phase 2 → Phase 4 (eval + docker) → Phase 5.** Skip Phase 3 (calendar/RAG) unless you have spare time — reliability, safety, and evals impress interviewers more than raw feature count.
