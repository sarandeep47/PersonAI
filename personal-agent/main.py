# personal-agent/main.py
import time
import uuid

import os
from tools.email_reader import fetch_unread_emails
from tools.telegram import send_telegram_message, get_telegram_updates, answer_callback_query, download_telegram_file
from tools.email_sender import send_email_raw
from agent.core import call_agent, revise_draft
import db.session as db
import config

# Track last processed Telegram update ID
last_update_id = 0

# ──────────────────────────────────────────────
# TELEGRAM LISTENER & AGENT INTERACTION
# ──────────────────────────────────────────────

def handle_message(chat_id: str, text: str, attachment_path: str = None):
    """Handle incoming Telegram text or attachment message."""
    text_lower = text.lower().strip()

    # Get prior conversation history BEFORE adding current user message (prevents message duplication)
    history = db.get_history(chat_id, limit=6)

    # Log current user message in chat history
    db.add_message(chat_id, "user", text)

    # --- Commands ---
    if text_lower in ["/help", "/start"]:
        msg = (
            "👋 *Your Personal AI Agent*\n\n"
            "You can message me naturally:\n\n"
            "📤 *Send email:*\n"
            "\"email john@gmail.com about tomorrow's meeting\"\n\n"
            "📎 *Attachments:*\n"
            "Attach your resume or document with a message to email it!\n\n"
            "🔍 *Search emails:*\n"
            "\"did anyone email me about invoice?\"\n\n"
            "⚡ *Commands:*\n"
            "/help — show this help message"
        )
        send_telegram_message(msg, chat_id=chat_id)
        return

    # Check if user is currently replying to an edit prompt
    if attachment_path:
        # If user attached a file, override any active edit state to start a fresh draft with attachment
        db.delete_pending_action(f"editing_{chat_id}")

    edit_state = db.get_pending_action(f"editing_{chat_id}")
    if edit_state:
        db.delete_pending_action(f"editing_{chat_id}")
        draft = edit_state["payload"]
        send_telegram_message("✏️ Adjusting draft...", chat_id=chat_id)
        
        tool_call = revise_draft(draft, text)
        if tool_call.tool == "send_email":
            if tool_call.args.get("is_unchanged"):
                msg = "⚠️ I couldn't confidently make that change — could you rephrase what you'd like edited?"
                send_telegram_message(msg, chat_id=chat_id)
                db.add_message(chat_id, "assistant", msg)
                _present_email_confirmation(chat_id, draft, attachment_path=attachment_path or draft.get("attachment_path"))
            else:
                _present_email_confirmation(chat_id, tool_call.args, attachment_path=attachment_path or draft.get("attachment_path"))
        else:
            msg = tool_call.args.get("message", "Could not revise draft.")
            send_telegram_message(msg, chat_id=chat_id)
            db.add_message(chat_id, "assistant", msg)
        return

    # Normal Agent flow via call_agent
    send_telegram_message("⏳ Thinking...", chat_id=chat_id)
    
    try:
        tool_call = call_agent(text, history=history)
        print(f"[Agent] Tool: {tool_call.tool} | Reasoning: {tool_call.reasoning}")

        if tool_call.tool == "send_email":
            args = tool_call.args
            _present_email_confirmation(chat_id, args, attachment_path=attachment_path)

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
                db.add_message(chat_id, "assistant", summary)
            else:
                msg = f"No emails found matching `{query}`."
                send_telegram_message(msg, chat_id=chat_id)
                db.add_message(chat_id, "assistant", msg)

        elif tool_call.tool == "none":
            msg = tool_call.args.get("message", "How can I help you with your emails?")
            send_telegram_message(msg, chat_id=chat_id)
            db.add_message(chat_id, "assistant", msg)

        else:
            msg = f"Selected tool `{tool_call.tool}`. Feature in progress."
            send_telegram_message(msg, chat_id=chat_id)
            db.add_message(chat_id, "assistant", msg)

    except Exception as e:
        print(f"[Agent] Error handling message: {e}")
        msg = "⚠️ Sorry, something went wrong processing that request."
        send_telegram_message(msg, chat_id=chat_id)
        db.add_message(chat_id, "assistant", msg)


def _present_email_confirmation(chat_id: str, args: dict, attachment_path: str = None):
    """Present email draft with inline confirmation buttons."""
    db.delete_pending_action(f"editing_{chat_id}")
    to = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")

    if not to or "@" not in to:
        msg = "❌ Please provide a valid recipient email address."
        send_telegram_message(msg, chat_id=chat_id)
        db.add_message(chat_id, "assistant", msg)
        return

    action_id = f"draft_{uuid.uuid4().hex[:8]}"
    db.save_pending_action(action_id, chat_id, "confirm_send", {
        "to": to,
        "subject": subject,
        "body": body,
        "attachment_path": attachment_path
    })

    attachment_info = ""
    if attachment_path and os.path.exists(attachment_path):
        filename = os.path.basename(attachment_path)
        attachment_info = f"\n\n📎 *Attachment:* `{filename}`"

    msg = (
        f"📧 *Draft Email — Ready for Confirmation*\n\n"
        f"*To:* `{to}`\n"
        f"*Subject:* {subject}\n\n"
        f"{body}"
        f"{attachment_info}\n\n"
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

    # Log presented draft as assistant response in conversation history
    assistant_summary = f"Drafted email to {to} with subject '{subject}':\n{body}"
    db.add_message(chat_id, "assistant", assistant_summary)


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
        
        success = send_email_raw(
            payload["to"],
            payload["subject"],
            payload["body"],
            attachment_path=payload.get("attachment_path")
        )
        if success:
            send_telegram_message(f"✅ Email successfully sent to *{payload['to']}*!", chat_id=chat_id)
        else:
            send_telegram_message("❌ Failed to send email. Check configuration/logs.", chat_id=chat_id)
        db.delete_pending_action(action_id)
        db.delete_pending_action(f"editing_{chat_id}")

    elif cmd == "edit":
        payload = action["payload"]
        answer_callback_query(cq_id, "Editing draft...")
        db.save_pending_action(f"editing_{chat_id}", chat_id, "editing", payload, ttl_seconds=300)
        send_telegram_message("✏️ Reply to this message with your instructions on what to change in the draft.", chat_id=chat_id)

    elif cmd == "cancel":
        answer_callback_query(cq_id, "Draft cancelled.")
        db.delete_pending_action(action_id)
        db.delete_pending_action(f"editing_{chat_id}")
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

        # Handle text or media message
        if "message" in update:
            message = update["message"]
            text = (message.get("text") or message.get("caption") or "").strip()
            chat_id = str(message.get("chat", {}).get("id", config.TELEGRAM_CHAT_ID))

            attachment_path = None
            if "document" in message:
                doc = message["document"]
                file_id = doc.get("file_id")
                file_name = doc.get("file_name", "attachment.pdf")
                if file_id:
                    attachment_path = download_telegram_file(file_id, file_name)
                    if not text:
                        text = f"I have attached my resume: {file_name}"
            elif "photo" in message:
                photos = message["photo"]
                if photos:
                    largest_photo = photos[-1]
                    file_id = largest_photo.get("file_id")
                    if file_id:
                        attachment_path = download_telegram_file(file_id, "attached_image.jpg")
                        if not text:
                            text = "I have attached an image."

            if text or attachment_path:
                print(f"[Telegram] [{chat_id}] User message: {text} | Attachment: {attachment_path}")
                handle_message(chat_id, text, attachment_path=attachment_path)

        # Handle button clicks
        elif "callback_query" in update:
            print(f"[Telegram] Callback Query received.")
            handle_callback_query(update["callback_query"])


def main():
    print("=" * 50)
    print("  Personal AI Agent Starting (Fast Mode)...")
    print("=" * 50)

    send_telegram_message(
        "🚀 *Agent is online (Fast Mode)!*\n\n"
        "You can message me to draft or send emails:\n\n"
        "\"email john@gmail.com about tomorrow's meeting\"\n\n"
        "Type /help for more info."
    )

    print("[Telegram] Listening for updates...")
    print("[Agent] Running in direct Telegram mode. Press Ctrl+C to stop.\n")
    try:
        while True:
            listen_for_telegram_messages()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Agent] Stopping...")
        send_telegram_message("🔴 Agent is offline.")


if __name__ == "__main__":
    main()

