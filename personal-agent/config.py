# personal-agent/config.py
# Fill in your credentials here

# --- Gmail & Google OAuth ---
EMAIL_ADDRESS = "sade74off@gmail.com"
EMAIL_APP_PASSWORD = "afcp sslm qoym wkxh"  # Not your real password! See README for how to get this.
IMAP_SERVER = "imap.gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

GOOGLE_CREDENTIALS_FILE = "credentials.json"
GOOGLE_TOKEN_FILE = "token.json"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]

# --- Telegram ---
TELEGRAM_BOT_TOKEN = "8691121331:AAGiw3NMqJgZkwoYMXSX7FTFPQmFFyk5zjM"   # Get from @BotFather
TELEGRAM_CHAT_ID = "7766604001"                  # Your personal chat ID with the bot

# --- Ollama / LLM ---
OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_BASE_URL = "http://localhost:11434"

# --- Agent Behavior ---
CHECK_INTERVAL_MINUTES = 10       # How often to check emails
MAX_EMAILS_PER_CHECK = 20         # Max emails to process per cycle

# --- VIP Senders (always treated as important regardless of LLM decision) ---
VIP_SENDERS = [
    # "boss@company.com",
    # "mom@gmail.com",
]
