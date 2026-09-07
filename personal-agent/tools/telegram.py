# personal-agent/tools/telegram.py
import requests
from langchain.tools import tool
import config


def send_telegram_message(text: str, reply_markup: dict = None, chat_id: str = None) -> bool:
    """
    Send a message to a Telegram chat via Bot API with optional inline reply_markup.
    Returns True on success, False on failure.
    """
    target_chat_id = chat_id or config.TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        # Fallback retry without Markdown formatting in case of Telegram markdown syntax error
        try:
            payload.pop("parse_mode", None)
            r2 = requests.post(url, json=payload, timeout=10)
            r2.raise_for_status()
            return True
        except Exception as ex:
            print(f"[Telegram] Error sending message: {ex}")
            return False



import os

def download_telegram_file(file_id: str, custom_filename: str = None) -> str:
    """
    Download a file attachment from Telegram Bot API and save it locally.
    Returns the absolute path of the saved file or None on failure.
    """
    try:
        get_file_url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getFile"
        res = requests.get(get_file_url, params={"file_id": file_id}, timeout=10)
        res.raise_for_status()
        file_path_info = res.json().get("result", {}).get("file_path")
        
        if not file_path_info:
            return None
            
        download_url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{file_path_info}"
        file_res = requests.get(download_url, timeout=30)
        file_res.raise_for_status()
        
        download_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads")
        os.makedirs(download_dir, exist_ok=True)
        
        filename = custom_filename or os.path.basename(file_path_info)
        saved_path = os.path.join(download_dir, filename)
        
        with open(saved_path, "wb") as f:
            f.write(file_res.content)
            
        print(f"[Telegram] Downloaded attachment to: {saved_path}")
        return saved_path
    except Exception as e:
        print(f"[Telegram] Error downloading file {file_id}: {e}")
        return None




def get_telegram_updates(offset: int = None) -> list[dict]:
    """
    Poll Telegram for new messages and callback queries.
    Returns list of update dicts.
    """
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 5, "allowed_updates": ["message", "callback_query"]}
    if offset:
        params["offset"] = offset
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json().get("result", [])
    except Exception as e:
        print(f"[Telegram] Error getting updates: {e}")
        return []


def answer_callback_query(callback_query_id: str, text: str = None):
    """Answer callback query from Telegram inline button interaction."""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[Telegram] Error answering callback query: {e}")


def format_email_for_telegram(email: dict, reason: str = "", tier: str = "urgent") -> str:
    """Format an email dict into a clean Telegram message."""
    tier_emoji = {"urgent": "🔴", "needs_reply": "🟡", "fyi": "🟢"}.get(tier, "📧")
    msg = (
        f"{tier_emoji} *Important Email*\n\n"
        f"*From:* {email.get('sender', '')}\n"
        f"*Subject:* {email.get('subject', '')}\n"
        f"*Date:* {email.get('date', '')}\n"
    )
    if reason:
        msg += f"*Why important:* {reason}\n"
        
    body_preview = email.get('body', '')[:400]
    msg += f"\n*Preview:*\n{body_preview}"
    return msg


@tool
def notify_telegram(message: str) -> str:
    """
    Send a notification message to your Telegram.
    Use this to forward important emails or agent updates to Telegram.
    Input is the message text to send.
    """
    success = send_telegram_message(message)
    if success:
        return "Telegram notification sent successfully."
    return "Failed to send Telegram notification."
