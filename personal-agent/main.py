# personal-agent/main.py
import time
import uuid
import threading
from apscheduler.schedulers.background import BackgroundScheduler

from tools.email_reader import fetch_unread_emails
from tools.telegram import send_telegram_message, get_telegram_updates, answer_callback_query, format_email_for_telegram
from tools.email_sender import send_email_raw
from importance_filter import filter_emails_batch
from agent.core import call_agent, revise_draft
import db.session as db
import config

# Track last processed Telegram update ID
last_update_id = 0

# ──────────────────────────────────────────────
# EMAIL MONITORING — runs on a schedule
# ──────────────────────────────────────────────

def check_and_forward_new_emails():
    """
    Fetch new emails, filter important ones, push summary to Telegram with inline reply buttons.
    """
    print("[Monitor] Checking for new emails...")
    emails = fetch_unread_emails()

    # Filter out already seen emails using SQLite database
    new_emails = [e for e in emails if not db.is_email_seen(e["id"])]

    if not new_emails:
        print("[Monitor] No new emails.")
        return

    print(f"[Monitor] {len(new_emails)} new email(s). Filtering with 4-tier categorization...")

    # VIP check
    vip_emails = []
    pending_emails = []
    for email in new_emails:
        sender_lower = email.get("sender", "").lower()
        is_vip = any(vip.lower() in sender_lower for vip in config.VIP_SENDERS if vip)
        if is_vip:
            vip_emails.append((email, "VIP sender", "urgent"))
        else:
            pending_emails.append(email)

    # Batch LLM filter
    batch_results = filter_emails_batch(pending_emails) if pending_emails else []

    important_emails = []
    for email, reason, tier in vip_emails:
        important_emails.append((email, reason, tier))

    for email, (important, reason, tier) in zip(pending_emails, batch_results):
        if important:
            important_emails.append((email, reason, tier))
        else:
            print(f"[Monitor] Skipped ({tier}): {email['subject']} — {reason}")

    # Mark all new emails as seen in SQLite
    for email in new_emails:
        db.mark_email_seen(email["id"])

    if not important_emails:
        print("[Monitor] No important emails found.")
        return

    send_telegram_message(f"📬 *{len(important_emails)} new important email(s):*")

    for email, reason, tier in important_emails:
        msg = format_email_for_telegram(email, reason, tier=tier)
        
        # Action button for inline interaction
        action_id = f"email_{uuid.uuid4().hex[:8]}"
        db.save_pending_action(action_id, config.TELEGRAM_CHAT_ID, "reply_email", {
            "email_id": email["id"],
            "sender": email["sender"],
            "subject": email["subject"],
            "body": email["body"]
        })
        
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "💬 Quick Reply", "callback_data": f"reply_prompt:{action_id}"},
                    {"text": "🔕 Dismiss", "callback_data": f"dismiss:{action_id}"}
                ]
            ]
        }
        send_telegram_message(msg, reply_markup=reply_markup)
        print(f"[Monitor] Forwarded ({tier}): {email['subject']}")


# ──────────────────────────────────────────────
# TELEGRAM LISTENER & AGENT INTERACTION
# ──────────────────────────────────────────────

def handle_message(chat_id: str, text: str):
    """Handle incoming Telegram text message."""
    text_lower = text.lower().strip()

    # Log user message in chat history
    db.add_message(chat_id, "user", text)

    # --- Commands ---
    if text_lower == "/check":
        send_telegram_message("🔍 Checking emails now...", chat_id=chat_id)
        check_and_forward_new_emails()
        return

    if text_lower in ["/help", "/start"]:
        send_telegram_message(
            "👋 *Your Personal AI Agent*\n\n"
            "You can message me naturally:\n\n"
            "📤 *Send email:*\n"
            "\"email john@gmail.com about tomorrow's meeting\"\n\n"
            "🔍 *Search emails:*\n"
            "\"did anyone email me about invoice?\"\n\n"
            "⚡ *Commands:*\n"
            "/check — force instant email check\n"
            "/help — show this help message",
            chat_id=chat_id
        )
        return

    # Check if user is currently replying to an edit prompt
    edit_state = db.get_pending_action(f"editing_{chat_id}")
    if edit_state:
        db.delete_pending_action(f"editing_{chat_id}")
        draft = edit_state["payload"]
        send_telegram_message("✏️ Adjusting draft...", chat_id=chat_id)
        
        tool_call = revise_draft(draft, text)
        if tool_call.tool == "send_email":
            if tool_call.args.get("is_unchanged"):
                send_telegram_message("⚠️ I couldn't confidently make that change — could you rephrase what you'd like edited?", chat_id=chat_id)
                _present_email_confirmation(chat_id, draft)
            else:
                _present_email_confirmation(chat_id, tool_call.args)
        else:
            send_telegram_message(tool_call.args.get("message", "Could not revise draft."), chat_id=chat_id)
        return

    # Normal Agent flow via call_agent
    send_telegram_message("⏳ Thinking...", chat_id=chat_id)
    history = db.get_history(chat_id, limit=6)
    
    try:
        tool_call = call_agent(text, history=history)
        print(f"[Agent] Tool: {tool_call.tool} | Reasoning: {tool_call.reasoning}")

        if tool_call.tool == "send_email":
            args = tool_call.args
            _present_email_confirmation(chat_id, args)

        elif tool_call.tool == "search_inbox":
            query = tool_call.args.get("query", "")
            send_telegram_message(f"🔍 Searching inbox for: `{query}`...", chat_id=chat_id)
            emails = fetch_unread_emails()  # or filter by search query
            matching = [e for e in emails if query.lower() in e["subject"].lower() or query.lower() in e["body"].lower()]
            if matching:
                summary = f"Found {len(matching)} matching email(s):\n\n"
                for m in matching[:5]:
                    summary += f"• *{m['sender']}*: {m['subject']}\n"
                send_telegram_message(summary, chat_id=chat_id)
            else:
                send_telegram_message(f"No emails found matching `{query}`.", chat_id=chat_id)

        elif tool_call.tool == "none":
            msg = tool_call.args.get("message", "How can I help you with your emails?")
            send_telegram_message(msg, chat_id=chat_id)
            db.add_message(chat_id, "assistant", msg)

        else:
            msg = f"Selected tool `{tool_call.tool}`. Feature in progress."
            send_telegram_message(msg, chat_id=chat_id)

    except Exception as e:
        print(f"[Agent] Error handling message: {e}")
        send_telegram_message("⚠️ Sorry, something went wrong processing that request.", chat_id=chat_id)


def _present_email_confirmation(chat_id: str, args: dict):
    """Present email draft with inline confirmation buttons."""
    to = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")

    if not to or "@" not in to:
        send_telegram_message("❌ Please provide a valid recipient email address.", chat_id=chat_id)
        return

    action_id = f"draft_{uuid.uuid4().hex[:8]}"
    db.save_pending_action(action_id, chat_id, "confirm_send", {
        "to": to,
        "subject": subject,
        "body": body
    })

    msg = (
        f"📧 *Draft Email — Ready for Confirmation*\n\n"
        f"*To:* `{to}`\n"
        f"*Subject:* {subject}\n\n"
        f"{body}\n\n"
        f"────────────────\n"
        f"Tap an action below:"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Send", "callback_data": f"confirm:{action_id}"},
                {"text": "✏️ Edit", "callback_data": f"edit:{action_id}"},
                {"text": "❌ Cancel", "callback_data": f"cancel:{action_id}"}
            ]
        ]
    }
    send_telegram_message(msg, reply_markup=reply_markup, chat_id=chat_id)


def handle_callback_query(cq: dict):
    """Handle callback button clicks from inline keyboards."""
    cq_id = cq["id"]
    data = cq.get("data", "")
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id", config.TELEGRAM_CHAT_ID))

    if ":" not in data:
        answer_callback_query(cq_id, "Invalid action.")
        return

    cmd, action_id = data.split(":", 1)
    action = db.get_pending_action(action_id)

    if not action and cmd not in ["dismiss"]:
        answer_callback_query(cq_id, "Action expired or already completed.")
        send_telegram_message("⚠️ That confirmation has expired.", chat_id=chat_id)
        return

    if cmd == "confirm":
        payload = action["payload"]
        answer_callback_query(cq_id, "Sending email...")
        send_telegram_message("📤 Sending email...", chat_id=chat_id)
        
        success = send_email_raw(payload["to"], payload["subject"], payload["body"])
        if success:
            send_telegram_message(f"✅ Email successfully sent to *{payload['to']}*!", chat_id=chat_id)
        else:
            send_telegram_message("❌ Failed to send email. Check configuration/logs.", chat_id=chat_id)
        db.delete_pending_action(action_id)

    elif cmd == "edit":
        payload = action["payload"]
        answer_callback_query(cq_id, "Editing draft...")
        db.save_pending_action(f"editing_{chat_id}", chat_id, "editing", payload, ttl_seconds=300)
        send_telegram_message("✏️ Reply to this message with your instructions on what to change in the draft.", chat_id=chat_id)

    elif cmd == "cancel":
        answer_callback_query(cq_id, "Draft cancelled.")
        db.delete_pending_action(action_id)
        send_telegram_message("❌ Draft cancelled.", chat_id=chat_id)

    elif cmd == "reply_prompt":
        answer_callback_query(cq_id, "Quick Reply")
        payload = action["payload"]
        db.save_pending_action(f"editing_{chat_id}", chat_id, "editing", {
            "to": payload["sender"],
            "subject": f"Re: {payload['subject']}",
            "body": ""
        }, ttl_seconds=300)
        send_telegram_message(f"💬 What would you like to reply to *{payload['sender']}*?", chat_id=chat_id)

    elif cmd == "dismiss":
        answer_callback_query(cq_id, "Dismissed.")


def listen_for_telegram_messages():
    """Poll Telegram for new messages and inline keyboard updates."""
    global last_update_id

    updates = get_telegram_updates(offset=last_update_id + 1 if last_update_id else None)

    for update in updates:
        last_update_id = update["update_id"]

        # Handle text message
        if "message" in update:
            message = update["message"]
            text = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id", config.TELEGRAM_CHAT_ID))

            if text:
                print(f"[Telegram] [{chat_id}] User: {text}")
                handle_message(chat_id, text)

        # Handle button clicks
        elif "callback_query" in update:
            print(f"[Telegram] Callback Query received.")
            handle_callback_query(update["callback_query"])


def telegram_polling_loop():
    """Continuously poll Telegram."""
    print("[Telegram] Listening for updates...")
    while True:
        try:
            listen_for_telegram_messages()
        except Exception as e:
            print(f"[Telegram] Error in loop: {e}")
        time.sleep(2)


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  Personal AI Agent Starting...")
    print("=" * 50)

    send_telegram_message(
        "🚀 *Agent is online!*\n\n"
        "I'll automatically forward important emails to you.\n"
        "You can also message me to draft or send emails:\n\n"
        "\"email john@gmail.com about tomorrow's meeting\"\n\n"
        "Type /help for more info."
    )

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        check_and_forward_new_emails,
        "interval",
        minutes=config.CHECK_INTERVAL_MINUTES,
        id="email_monitor",
    )
    scheduler.start()
    print(f"[Scheduler] Auto email check every {config.CHECK_INTERVAL_MINUTES} min.")

    # Run initial check on startup
    check_and_forward_new_emails()

    t = threading.Thread(target=telegram_polling_loop, daemon=True)
    t.start()

    print("[Agent] Running. Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Agent] Stopping...")
        scheduler.shutdown()
        send_telegram_message("🔴 Agent is offline.")


if __name__ == "__main__":
    main()
