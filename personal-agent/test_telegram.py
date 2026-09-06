# Quick test — run this to verify Telegram is working
import sys
sys.path.insert(0, '.')
from tools.telegram import send_telegram_message

result = send_telegram_message("✅ Test message from your Personal AI Agent! Setup is working.")
print("Success!" if result else "Failed — check your token and chat ID.")
