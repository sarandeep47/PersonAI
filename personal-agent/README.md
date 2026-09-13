# Personal AI Email Agent

A local AI agent that monitors your Gmail, filters important emails using a local LLM, and forwards them to Telegram. You can also send emails by messaging your Telegram bot.

## Setup Guide

### Step 1 — Install Ollama and pull the model

1. Download Ollama from https://ollama.com/download
2. Install it and open a terminal, then run:
   ```
   ollama pull llama3.2:3b
   ```
   Wait for the download (about 2GB).

### Step 2 — Install Python dependencies

Make sure you have Python 3.10+ installed, then run:
```
pip install -r requirements.txt
```

### Step 3 — Set up Gmail App Password

You need an App Password (not your real Gmail password):
1. Go to https://myaccount.google.com/security
2. Enable **2-Step Verification** if not already on
3. Search for **"App passwords"** in the search bar
4. Create a new app password → select "Mail" and "Windows Computer"
5. Copy the 16-character password — you'll use this in config.py

Also enable IMAP in Gmail:
1. Open Gmail → Settings (gear icon) → See all settings
2. Go to **Forwarding and POP/IMAP** tab
3. Enable **IMAP Access** → Save Changes

### Step 4 — Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** you receive
4. Start a chat with your new bot (search its username and click Start)
5. Get your chat ID by visiting:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Send a message to your bot first, then visit this URL. Find `"chat":{"id":XXXXXXX}` — that number is your chat ID.

### Step 5 — Fill in config.py

Open `config.py` and fill in:
- `EMAIL_ADDRESS` — your Gmail address
- `EMAIL_APP_PASSWORD` — the 16-char app password from Step 3
- `TELEGRAM_BOT_TOKEN` — from Step 4
- `TELEGRAM_CHAT_ID` — from Step 4
- Optionally add VIP senders to `VIP_SENDERS`

### Step 6 — Run the agent

```
python main.py
```

The agent will:
- Send you a Telegram message confirming it's online
- Check your emails immediately on startup
- Check every 10 minutes automatically

## Telegram Commands & Capabilities

| Command / Capability | What it does |
|---|---|
| `/check` | Manually trigger an email check right now |
| `/send TO: email@x.com \| SUBJECT: Hello \| BODY: Message here` | Send an email |
| Natural language email requests | Schedule or send emails via Telegram confirmation flow |
| Natural language calendar requests | Schedule events (`schedule_calendar`) or view upcoming events (`list_calendar`) |

### Google Calendar Integration

**Supported Features:**
- Schedule Calendar events with title, date, start time, duration, and optional attendees via Telegram inline confirmation
- List upcoming Calendar events for specified datetime ranges
- Shared Google OAuth authentication mechanism for Gmail and Google Calendar

**Out of Scope / Not Supported:**
- Calendar event editing or deletion
- Recurring events
- Conflict detection

### Reminder System Integration

**Supported Features:**
- Standard reminders with natural datetime parsing (e.g., *"Remind me tomorrow at 9 AM to call Priya"*, *"Remind me in 30 minutes"*)
- Calendar-relative reminders using existing Google Calendar OAuth (e.g., *"Remind me 30 minutes before my RAG meeting"*)
- Telegram inline confirmation flow (`✅ Confirm` / `❌ Cancel`)
- Persistent SQLite alarm database (`alarms` table)
- Background daemon checker thread (`AlarmCheckerThread`, 30s polling cycle)
- Telegram delivery retry protection (retains pending state if delivery fails)

**Out of Scope / Current Limitations:**
- No recurring reminders
- No snooze functionality
- No reminder editing
- No external schedulers (uses native SQLite + Python background daemon thread)

## Customize

- Change `CHECK_INTERVAL_MINUTES` in config.py to check more/less often
- Add emails to `VIP_SENDERS` to always forward them regardless of LLM decision
- Change `OLLAMA_MODEL` if you want to try a different model

## Project Roadmap

This project was built in six deliberate phases. Development stopped at Phase 6 by design.

### Completed

| Phase | Description |
|---|---|
| Phase 1 | **Core Solidification** — Structured tool-calling pipeline (`ToolCall` schema), hallucination-blocking validation layer, JSON-parse retry logic, eval harness |
| Phase 2 | **Multi-Task Planning** — `TaskPlan` schema for compound requests, per-task validation, sequential execution with inline Telegram confirmation and stop-on-failure |
| Phase 3 | **Google Calendar Integration** — Schedule and list Google Calendar events via OAuth; relative date correction; shared OAuth token with Gmail |
| Phase 4 | **Reminder System** — SQLite-backed alarms, natural-language datetime parsing, calendar-relative reminders, background `AlarmCheckerThread` (30 s polling) |
| Phase 5 | **Task Manager** — Add, list, complete, and delete tasks; short-term context resolution (pronouns like "complete it" resolve to the last mentioned task) |
| Phase 6 | **Conversation Memory** — Persistent contact storage with fuzzy matching (≥85% similarity), deleted-contact history scrubbing, short-term context entities across calendar/task/email domains |

### Scoped, Not Built (Intentional)

| Phase | Description | Reason not built |
|---|---|---|
| Phase 7 | **Daily Briefing** — `/briefing` command aggregating upcoming calendar events, unread emails, pending tasks, and active reminders into a single Telegram summary | Time and scope constraints; the core assistant was already solid and well-tested at Phase 6. |
| Phase 8 | **OCR-Based Job Application Drafting** — LinkedIn job screenshot → OCR extraction → tailored application email draft | The highest-risk failure mode identified was the LLM fabricating or exaggerating qualifications in a real outbound email to a recruiter. This risk was judged not worth the value added given the project's existing email validation safeguards were designed to prevent exactly this class of hallucination. |

## Known Limitations

- **Draft Revision Consistency**: Small local LLMs (e.g. `llama3.2:3b`) can occasionally claim a draft change was made in their reasoning without actually modifying the output text. The system detects when a regenerated draft is identical to the original and flags this to the user with a note ("I couldn't confidently make that change — could you rephrase what you'd like edited?") rather than presenting an unchanged draft as a successful edit.
- **Output Quality Variance**: Because this project uses a small local model (`llama3.2:3b`) rather than a large cloud model, email draft quality and specificity can vary between runs of the identical input due to sampling randomness — e.g. sometimes using a generic 'Dear All' greeting instead of the actual recipient's name. Confirmed via repeated-run testing. This is an inherent tradeoff of local-first inference (privacy, zero API cost, offline capability) versus larger cloud models, not a code defect.

