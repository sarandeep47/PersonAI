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

## Telegram Commands

| Command | What it does |
|---|---|
| `/check` | Manually trigger an email check right now |
| `/send TO: email@x.com \| SUBJECT: Hello \| BODY: Message here` | Send an email |
| Any natural text | The AI agent handles it (e.g., "Email John that I'll be late") |

## Customize

- Change `CHECK_INTERVAL_MINUTES` in config.py to check more/less often
- Add emails to `VIP_SENDERS` to always forward them regardless of LLM decision
- Change `OLLAMA_MODEL` if you want to try a different model

## Known Limitations

- **Draft Revision Consistency**: Small local LLMs (e.g. `llama3.2:3b`) can occasionally claim a draft change was made in their reasoning without actually modifying the output text. The system detects when a regenerated draft is identical to the original and flags this to the user with a note ("I couldn't confidently make that change — could you rephrase what you'd like edited?") rather than presenting an unchanged draft as a successful edit.

