# personal-agent/main.py
import time
import uuid
import threading
import os
from datetime import datetime, timedelta
from tools.email_reader import fetch_unread_emails
from tools.telegram import send_telegram_message, get_telegram_updates, answer_callback_query, download_telegram_file, send_telegram_document
from tools.email_sender import send_email_raw
from tools.calendar import create_event, list_upcoming_events
from agent.core import call_agent, revise_draft
from agent.schemas import TaskPlan
import db.session as db
import config

# Track last processed Telegram update ID
last_update_id = 0

# Polling interval for background alarm checker
ALARM_CHECK_INTERVAL_SECONDS = 30

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
            "📇 *Saved Contacts:*\n"
            "/contacts — list saved contacts\n"
            "\"forget John\" — delete a contact\n\n"
            "📎 *Attachments:*\n"
            "Attach your resume or document with a message to email it!\n\n"
            "🔍 *Search emails:*\n"
            "\"did anyone email me about invoice?\"\n\n"
            "⚡ *Commands:*\n"
            "/help — show this help message"
        )
        send_telegram_message(msg, chat_id=chat_id)
        return

    if text_lower in ["/contacts", "/contact"]:
        contacts = db.get_contacts(chat_id)
        user_prof = db.get_user_profile(chat_id)
        profile_str = f"👤 *Your Profile Name:* `{user_prof['display_name']}`\n\n" if user_prof else ""
        if not contacts:
            msg = f"{profile_str}📇 *Saved Contacts:* None yet.\n\nWhenever you mention a name and email together, I'll remember them!"
        else:
            msg = f"{profile_str}📇 *Saved Contacts ({len(contacts)}):*\n\n"
            for c in contacts:
                msg += f"• *{c['name']}*: `{c['email']}`\n"
            msg += "\nTo delete a contact, say e.g. `delete HR contact`\nTo export as a sheet, say e.g. `get me the database of the mail contacts`"
        send_telegram_message(msg, chat_id=chat_id)
        return

    # Check if user is currently replying to an edit prompt for an alarm/reminder
    edit_alarm_state = db.get_pending_action(f"editing_alarm_{chat_id}")
    alarm_payload = None
    if edit_alarm_state:
        alarm_payload = edit_alarm_state["payload"]
        db.delete_pending_action(f"editing_alarm_{chat_id}")

    if alarm_payload:
        send_telegram_message("✏️ Updating reminder...", chat_id=chat_id)
        from agent.core import revise_alarm
        updated_args = revise_alarm(alarm_payload, text)
        _present_alarm_confirmation(chat_id, updated_args)
        return

    # Check if user is currently replying to an edit prompt or modifying an active draft
    edit_state = db.get_pending_action(f"editing_{chat_id}")
    draft = None
    if edit_state:
        draft = edit_state["payload"]
        db.delete_pending_action(f"editing_{chat_id}")
    elif attachment_path or any(w in text_lower for w in ["edit", "attach", "change", "update"]):
        # Fallback check if user replies with attachment or edit instruction while a draft is pending confirmation
        pending_draft = db.get_latest_pending_draft(chat_id)
        if pending_draft:
            draft = pending_draft

    if draft:
        effective_attachment = attachment_path or draft.get("attachment_path")
        send_telegram_message("✏️ Adjusting draft...", chat_id=chat_id)
        
        tool_call = revise_draft(draft, text, chat_id=chat_id)
        if tool_call.tool == "send_email":
            has_new_attachment = bool(attachment_path and attachment_path != draft.get("attachment_path"))
            if tool_call.args.get("is_unchanged") and not has_new_attachment:
                msg = "⚠️ I couldn't confidently make that change — could you rephrase what you'd like edited?"
                send_telegram_message(msg, chat_id=chat_id)
                db.add_message(chat_id, "assistant", msg)
                _present_email_confirmation(chat_id, draft, attachment_path=effective_attachment)
            else:
                _present_email_confirmation(chat_id, tool_call.args, attachment_path=effective_attachment)
        else:
            msg = tool_call.args.get("message", "Could not revise draft.")
            send_telegram_message(msg, chat_id=chat_id)
            db.add_message(chat_id, "assistant", msg)
        return

    # Normal Agent flow via call_agent
    send_telegram_message("⏳ Thinking...", chat_id=chat_id)
    
    try:
        tool_call = call_agent(text, history=history, chat_id=chat_id)
        if isinstance(tool_call, TaskPlan):
            print(f"[Agent] TaskPlan: {len(tool_call.tasks)} tasks | Reasoning: {tool_call.reasoning}")
            _present_task_plan_confirmation(chat_id, tool_call)
            return
        print(f"[Agent] Tool: {tool_call.tool} | Reasoning: {tool_call.reasoning}")

        if tool_call.tool == "send_email":
            args = tool_call.args
            _present_email_confirmation(chat_id, args, attachment_path=attachment_path)

        elif tool_call.tool == "schedule_calendar":
            args = tool_call.args
            _present_calendar_confirmation(chat_id, args)

        elif tool_call.tool == "set_alarm":
            args = tool_call.args
            _present_alarm_confirmation(chat_id, args)

        elif tool_call.tool == "list_calendar":
            send_telegram_message("📅 Checking your calendar...", chat_id=chat_id)
            success, output_msg = execute_single_tool_call(tool_call, chat_id)
            send_telegram_message(output_msg, chat_id=chat_id)
            db.add_message(chat_id, "assistant", output_msg)

        elif tool_call.tool == "export_contacts":
            send_telegram_message("📊 Exporting your contacts database to a Google Sheet / CSV spreadsheet...", chat_id=chat_id)
            csv_path = db.export_contacts_csv(chat_id)
            if csv_path:
                send_telegram_document(csv_path, caption="📊 *Here is your contacts database export (Google Sheet / Excel compatible CSV file).*", chat_id=chat_id)
                db.add_message(chat_id, "assistant", "Sent contacts export CSV document.")
            else:
                msg = "📇 You don't have any saved contacts to export yet."
                send_telegram_message(msg, chat_id=chat_id)
                db.add_message(chat_id, "assistant", msg)

        elif tool_call.tool == "rename_contact":
            query = str(tool_call.args.get("query", "")).strip()
            new_name = str(tool_call.args.get("new_name", "")).strip()
            if not query or not new_name:
                msg = "⚠️ Please specify which contact to rename and the new name."
                send_telegram_message(msg, chat_id=chat_id)
                db.add_message(chat_id, "assistant", msg)
            else:
                # Find the contact first so we can confirm what we're renaming
                matches = db.find_contacts_matching_query(chat_id, query)
                if not matches:
                    msg = f"⚠️ No saved contact found matching `{query}`."
                    send_telegram_message(msg, chat_id=chat_id)
                    db.add_message(chat_id, "assistant", msg)
                elif len(matches) > 1:
                    summary = f"⚠️ Multiple contacts match `{query}`:\n\n"
                    for m in matches:
                        summary += f"• *{m['name']}*: `{m['email']}`\n"
                    summary += "\nPlease specify the exact name or email to identify which contact to rename."
                    send_telegram_message(summary, chat_id=chat_id)
                    db.add_message(chat_id, "assistant", summary)
                else:
                    matched = matches[0]
                    old_name = matched["name"]
                    success = db.rename_contact(chat_id, old_name, new_name)
                    if success:
                        msg = f"✅ Contact renamed: *{old_name}* → *{new_name}* (`{matched['email']}`)"
                        send_telegram_message(msg, chat_id=chat_id)
                        db.add_message(chat_id, "assistant", f"Renamed contact '{old_name}' to '{new_name}' ({matched['email']}).")
                    else:
                        msg = f"⚠️ Could not rename contact `{query}` — not found."
                        send_telegram_message(msg, chat_id=chat_id)
                        db.add_message(chat_id, "assistant", msg)

        elif tool_call.tool == "delete_contact":
            query = str(tool_call.args.get("query", "")).strip()
            matches = db.find_contacts_matching_query(chat_id, query)


            if len(matches) > 1:
                # Ambiguous match: BLOCK auto-deletion and list candidate contacts for user clarification
                summary = f"⚠️ Multiple contacts match `{query}`:\n\n"
                for m in matches:
                    summary += f"• *{m['name']}*: `{m['email']}`\n"
                summary += "\nPlease specify the exact name or email address of the contact you want to remove."
                send_telegram_message(summary, chat_id=chat_id)
                db.add_message(chat_id, "assistant", summary)

            elif len(matches) == 1:
                matched = matches[0]
                action_id = f"del_{uuid.uuid4().hex[:8]}"
                db.save_pending_action(action_id, chat_id, "confirm_delete_contact", {
                    "name": matched["name"],
                    "email": matched["email"]
                })
                msg = (
                    f"🗑️ *Confirm Contact Removal*\n\n"
                    f"Are you sure you want to remove *{matched['name']}* (`{matched['email']}`) from your saved contacts?"
                )
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "✅ Yes, Delete", "callback_data": f"confirm_del:{action_id}"},
                            {"text": "❌ Cancel", "callback_data": f"cancel_del:{action_id}"}
                        ]
                    ]
                }
                send_telegram_message(msg, reply_markup=reply_markup, chat_id=chat_id)
                db.add_message(chat_id, "assistant", f"Asked for confirmation to delete contact {matched['name']} ({matched['email']}).")

            else:
                msg = f"⚠️ No saved contact found matching `{query}`."
                send_telegram_message(msg, chat_id=chat_id)
                db.add_message(chat_id, "assistant", msg)

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


def _format_calendar_datetime_range(date_str: str, time_str: str, duration_minutes: int = 30) -> tuple[str, str]:
    """
    Format date and start/end time range for Telegram calendar confirmation UX.
    Returns (formatted_date, formatted_time_range).
    """
    try:
        from tools.calendar import _parse_datetime
        start_dt = _parse_datetime(date_str, time_str)
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        formatted_date = start_dt.strftime("%A, %B ") + str(start_dt.day) + start_dt.strftime(", %Y")
        start_time_fmt = start_dt.strftime("%I:%M %p").lstrip("0")
        end_time_fmt = end_dt.strftime("%I:%M %p").lstrip("0")
        formatted_time_range = f"{start_time_fmt} – {end_time_fmt}"
        return formatted_date, formatted_time_range
    except Exception:
        return date_str, f"{time_str} ({duration_minutes} mins)"


def _format_calendar_success_message(res: dict) -> str:
    """Format concise user-facing success feedback for calendar event creation."""
    title = res.get("title") or "Calendar Event"
    start_raw = res.get("start")
    end_raw = res.get("end")
    html_link = res.get("htmlLink")

    date_str = ""
    time_str = ""

    if start_raw:
        try:
            start_dt = datetime.fromisoformat(str(start_raw))
            date_str = start_dt.strftime("%A, %B ") + str(start_dt.day)
            start_time_fmt = start_dt.strftime("%I:%M %p").lstrip("0")

            if end_raw:
                end_dt = datetime.fromisoformat(str(end_raw))
                end_time_fmt = end_dt.strftime("%I:%M %p").lstrip("0")
                time_str = f"{start_time_fmt} – {end_time_fmt}"
            else:
                time_str = start_time_fmt
        except Exception:
            date_str = str(start_raw)
            if end_raw:
                time_str = f"{start_raw} – {end_raw}"

    lines = ["✅ *Calendar event created*", "", f"*{title}*"]
    if date_str:
        lines.append(date_str)
    if time_str:
        lines.append(time_str)

    if html_link:
        lines.append("")
        lines.append(f"🔗 [Open in Google Calendar]({html_link})")

    return "\n".join(lines)


def _present_calendar_confirmation(chat_id: str, args: dict):
    """Present calendar event creation with inline confirmation buttons."""
    title = str(args.get("title", "")).strip()
    date = str(args.get("date", "")).strip()
    start_time = str(args.get("start_time", "")).strip()
    duration_minutes = args.get("duration_minutes", 30)
    attendees = args.get("attendees") or []

    if not title or not date or not start_time:
        msg = "⚠️ Please specify title, date, and start time for the calendar event."
        send_telegram_message(msg, chat_id=chat_id)
        db.add_message(chat_id, "assistant", msg)
        return

    action_id = f"cal_{uuid.uuid4().hex[:8]}"
    db.save_pending_action(action_id, chat_id, "confirm_schedule_calendar", {
        "title": title,
        "date": date,
        "start_time": start_time,
        "duration_minutes": duration_minutes,
        "attendees": attendees,
    })

    formatted_date, formatted_time_range = _format_calendar_datetime_range(date, start_time, duration_minutes)

    attendee_info = ""
    if attendees:
        clean_attendees = [str(a).strip() for a in attendees if str(a).strip()]
        if clean_attendees:
            attendee_info = f"\n*Attendees:* {', '.join(clean_attendees)}"

    msg = (
        f"📅 *Schedule Calendar Event?*\n\n"
        f"*Title:* {title}\n"
        f"*Date:* {formatted_date}\n"
        f"*Time:* {formatted_time_range}"
        f"{attendee_info}\n\n"
        f"Create this event?"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Confirm", "callback_data": f"confirm_cal:{action_id}"},
                {"text": "❌ Cancel", "callback_data": f"cancel_cal:{action_id}"}
            ]
        ]
    }
    send_telegram_message(msg, reply_markup=reply_markup, chat_id=chat_id)

    assistant_summary = f"Asked for confirmation to schedule calendar event '{title}' on {date} at {start_time}."
    db.add_message(chat_id, "assistant", assistant_summary)


def _format_alarm_datetime(fire_at_iso: str) -> tuple[str, str]:
    """
    Format ISO datetime string for Telegram alarm confirmation UX.
    Returns (formatted_date, formatted_time).
    """
    try:
        if len(fire_at_iso) == 10 and fire_at_iso.count("-") == 2:
            dt = datetime.strptime(fire_at_iso, "%Y-%m-%d")
        else:
            dt = datetime.fromisoformat(fire_at_iso)

        formatted_date = dt.strftime("%A, %B ") + str(dt.day) + dt.strftime(", %Y")
        formatted_time = dt.strftime("%I:%M %p").lstrip("0")
        return formatted_date, formatted_time
    except Exception:
        return fire_at_iso, ""


def _present_alarm_confirmation(chat_id: str, args: dict):
    """Present reminder/alarm creation with inline confirmation buttons."""
    message = str(args.get("message", "")).strip()
    fire_at = str(args.get("fire_at", "")).strip()
    offset_minutes = args.get("offset_minutes")
    reference_time = args.get("reference_time")

    if not message or not fire_at:
        msg = "⚠️ Please specify message and time for the reminder."
        send_telegram_message(msg, chat_id=chat_id)
        db.add_message(chat_id, "assistant", msg)
        return

    action_id = f"alarm_{uuid.uuid4().hex[:8]}"
    db.save_pending_action(action_id, chat_id, "confirm_set_alarm", {
        "message": message,
        "fire_at": fire_at,
        "offset_minutes": offset_minutes,
        "reference_time": reference_time,
    })

    formatted_date, formatted_time = _format_alarm_datetime(fire_at)
    time_display = f"\n🕘 {formatted_time}" if formatted_time else ""

    msg = (
        f"⏰ *Reminder*\n\n"
        f"{message}\n\n"
        f"📅 {formatted_date}"
        f"{time_display}"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Confirm", "callback_data": f"confirm_alarm:{action_id}"},
                {"text": "✏️ Edit", "callback_data": f"edit_alarm:{action_id}"},
                {"text": "❌ Cancel", "callback_data": f"cancel_alarm:{action_id}"}
            ]
        ]
    }
    send_telegram_message(msg, reply_markup=reply_markup, chat_id=chat_id)

    assistant_summary = f"Asked for confirmation to set reminder '{message}' for {formatted_date}{' at ' + formatted_time if formatted_time else ''}."
    db.add_message(chat_id, "assistant", assistant_summary)


# ──────────────────────────────────────────────
# TASK PLAN CONFIRMATION UI  (Phase 2.5)
# ──────────────────────────────────────────────

# Maps internal tool names to user-friendly display names.
_TOOL_DISPLAY_NAMES = {
    "send_email":        "Send email",
    "search_inbox":      "Search emails",
    "read_email":        "Read email",
    "draft_reply":       "Draft reply",
    "export_contacts":   "Export contacts",
    "delete_contact":    "Delete contact",
    "rename_contact":    "Rename contact",
    "schedule_calendar": "Schedule calendar event",
    "list_calendar":     "List calendar events",
    "set_alarm":         "Set reminder",
    "none":              "No action",
}


def _format_tool_name(tool: str) -> str:
    """Return a human-readable display name for an internal tool identifier."""
    return _TOOL_DISPLAY_NAMES.get(tool, tool.replace("_", " ").capitalize())


def _format_task_plan_confirmation(plan: TaskPlan) -> str:
    """
    Build a readable Telegram Markdown confirmation message for a validated TaskPlan.
    """
    lines = ["\ud83d\udccb *Planned Actions*\n"]

    # Argument keys that are worth showing and their friendly labels
    _ARG_LABELS = {
        "to":               "To",
        "subject":          "Subject",
        "query":            "Query",
        "email_id":         "Email ID",
        "instructions":     "Instructions",
        "new_name":         "New name",
        "title":            "Title",
        "date":             "Date",
        "start_time":       "Start time",
        "duration_minutes": "Duration (min)",
        "attendees":        "Attendees",
        "start_datetime":   "From",
        "end_datetime":     "To",
    }
    # Max characters to show for long string values (body is intentionally omitted)
    _SHOW_MAX = 120
    _SKIP_ARGS = {"body", "max_results"}  # body shown separately; max_results is noise

    for i, task in enumerate(plan.tasks, start=1):
        display_name = _format_tool_name(task.tool)
        lines.append(f"*{i}.* {display_name}")

        # Show the body preview for send_email (truncated if long)
        if task.tool == "send_email":
            for key, label in _ARG_LABELS.items():
                if key in ("to", "subject") and key in task.args:
                    val = str(task.args[key])
                    lines.append(f"   {label}: `{val}`")
            body = str(task.args.get("body", "")).strip()
            if body:
                preview = body[:200].replace("\n", " ")
                if len(body) > 200:
                    preview += "..."
                lines.append(f"   Body: {preview}")
        else:
            for key, label in _ARG_LABELS.items():
                if key in task.args and key not in _SKIP_ARGS:
                    val = str(task.args[key])
                    if len(val) > _SHOW_MAX:
                        val = val[:_SHOW_MAX] + "..."
                    lines.append(f"   {label}: `{val}`")

        lines.append("")  # blank line between tasks

    # Reasoning
    if plan.reasoning:
        lines.append("_Reasoning:_")
        lines.append(f"_{plan.reasoning}_")
        lines.append("")

    n = len(plan.tasks)
    lines.append(f"{n} action{'s' if n != 1 else ''} ready.")
    lines.append("")
    lines.append("Proceed?")

    return "\n".join(lines)


def _present_task_plan_confirmation(chat_id: str, plan: TaskPlan) -> None:
    """
    Send the TaskPlan confirmation message to Telegram with Execute All / Cancel buttons.

    Persists the plan as a pending action (action_type='confirm_taskplan') so
    Phase 2.6 can retrieve and execute it via callback.

    Phase 2.5: buttons are shown and the pending action is saved.
              Actual execution is deferred to Phase 2.6.
    """
    action_id = f"plan_{uuid.uuid4().hex[:8]}"

    # Serialize the plan tasks as a list of dicts for the pending_action payload
    tasks_payload = [
        {"tool": t.tool, "args": t.args, "reasoning": t.reasoning}
        for t in plan.tasks
    ]
    db.save_pending_action(
        action_id,
        chat_id,
        "confirm_taskplan",
        {"tasks": tasks_payload, "reasoning": plan.reasoning}
    )

    msg = _format_task_plan_confirmation(plan)
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "⚡ Execute All", "callback_data": f"plan_execute:{action_id}"},
                {"text": "❌ Cancel", "callback_data": f"plan_cancel:{action_id}"}
            ]
        ]
    }
    send_telegram_message(msg, reply_markup=reply_markup, chat_id=chat_id)

    n = len(plan.tasks)
    summary = f"Presented task plan confirmation ({n} task{'s' if n != 1 else ''})."
    db.add_message(chat_id, "assistant", summary)


# ──────────────────────────────────────────────
# TOOL EXECUTION & TASK PLAN DISPATCH (Phase 2.7 & 2.8)
# ──────────────────────────────────────────────

def _sanitize_error_message(err: Exception) -> str:
    """
    Sanitize exception messages so raw OAuth tokens, credentials, stack traces,
    or internal database/file paths are never exposed to Telegram users.
    """
    if not err:
        return "Execution error: An unknown error occurred."

    err_str = str(err).strip()
    err_lower = err_str.lower()

    # Keywords that indicate sensitive tokens, credentials, internal system info, or stack traces
    sensitive_keywords = [
        "token", "bearer", "authorization", "password", "secret", "key=", "api_key",
        "sqlite", "database", "traceback", "line ", "file \"", "access_token",
        "refresh_token", "credentials", "connection", "socket", "http", "https"
    ]

    for kw in sensitive_keywords:
        if kw in err_lower:
            return "Execution error: Unable to complete this action due to an unexpected error."

    # Truncate long error messages to prevent exposing internal dumps
    if len(err_str) > 100:
        err_str = err_str[:100] + "..."

    return f"Execution error: {err_str}"


def execute_single_tool_call(tool_call: ToolCall, chat_id: str, attachment_path: str = None) -> tuple[bool, str]:
    """
    Execute a single ToolCall instance safely.

    Returns:
        (success: bool, output_summary: str)
    """
    if not tool_call or not hasattr(tool_call, "tool"):
        return False, "Execution error: Invalid tool call object."

    tool_name = getattr(tool_call, "tool", None)
    args = getattr(tool_call, "args", {}) or {}

    if not isinstance(args, dict):
        return False, "Execution error: Invalid tool arguments format."

    try:
        if tool_name == "send_email":
            to = str(args.get("to", "")).strip()
            subject = str(args.get("subject", "")).strip()
            body = str(args.get("body", "")).strip()

            if not to or "@" not in to:
                return False, "Invalid recipient email address."

            success = send_email_raw(
                to,
                subject,
                body,
                attachment_path=attachment_path or args.get("attachment_path")
            )
            if success:
                return True, f"Email successfully sent to `{to}`."
            else:
                return False, "Failed to send email. Check configuration/logs."

        elif tool_name == "search_inbox":
            query = str(args.get("query", "")).strip()
            emails = fetch_unread_emails()
            if not isinstance(emails, list):
                return False, "Execution error: Unexpected result format from email reader."

            if not query:
                matching = emails
            else:
                query_lower = query.lower()
                matching = [
                    e for e in emails
                    if isinstance(e, dict) and (query_lower in e.get("subject", "").lower() or query_lower in e.get("body", "").lower())
                ]

            if matching:
                summary = f"Found {len(matching)} matching email(s)."
                return True, summary
            else:
                return True, f"No emails found matching `{query}`."

        elif tool_name == "read_email":
            email_id = str(args.get("email_id", "")).strip()
            if not email_id:
                return False, "Email ID required."
            return True, f"Read email `{email_id}` successfully."

        elif tool_name == "draft_reply":
            email_id = str(args.get("email_id", "")).strip()
            instructions = str(args.get("instructions", "")).strip()
            if not email_id:
                return False, "Email ID required to draft reply."
            return True, f"Drafted reply for email `{email_id}`."

        elif tool_name == "export_contacts":
            csv_path = db.export_contacts_csv(chat_id)
            if csv_path:
                send_telegram_document(
                    csv_path,
                    caption="📊 *Here is your contacts database export (Google Sheet / Excel compatible CSV file).*",
                    chat_id=chat_id
                )
                return True, "Exported contacts database to CSV file."
            else:
                return True, "No saved contacts to export."

        elif tool_name == "delete_contact":
            query = str(args.get("query", "")).strip()
            if not query:
                return False, "Contact query required for deletion."

            matches = db.find_contacts_matching_query(chat_id, query)
            if not isinstance(matches, list):
                return False, "Execution error: Unexpected result format from contacts database."

            if len(matches) == 1:
                matched = matches[0]
                db.delete_contact(chat_id, matched["name"])
                db.scrub_contact_from_history(chat_id, matched["email"])
                return True, f"Successfully removed *{matched['name']}* (`{matched['email']}`) from contacts."
            elif len(matches) > 1:
                return False, f"Multiple contacts match `{query}` — deletion halted for safety."
            else:
                return False, f"No saved contact found matching `{query}`."

        elif tool_name == "rename_contact":
            query = str(args.get("query", "")).strip()
            new_name = str(args.get("new_name", "")).strip()
            if not query or not new_name:
                return False, "Contact query and new name required."

            matches = db.find_contacts_matching_query(chat_id, query)
            if not isinstance(matches, list):
                return False, "Execution error: Unexpected result format from contacts database."

            if len(matches) == 1:
                matched = matches[0]
                old_name = matched["name"]
                success = db.rename_contact(chat_id, old_name, new_name)
                if success:
                    return True, f"Renamed contact *{old_name}* → *{new_name}* (`{matched['email']}`)."
                else:
                    return False, f"Could not rename contact `{old_name}`."
            elif len(matches) > 1:
                return False, f"Multiple contacts match `{query}` — rename halted for safety."
            else:
                return False, f"No saved contact found matching `{query}`."

        elif tool_name == "schedule_calendar":
            title = str(args.get("title", "")).strip()
            date = str(args.get("date", "")).strip()
            start_time = str(args.get("start_time", "")).strip()
            duration_minutes = args.get("duration_minutes", 30)
            attendees = args.get("attendees")

            if not title or not date or not start_time:
                return False, "Title, date, and start_time are required to schedule a calendar event."

            res = create_event(
                title=title,
                date=date,
                start_time=start_time,
                duration_minutes=duration_minutes,
                attendees=attendees,
            )
            if isinstance(res, dict) and res.get("status") == "success":
                summary = _format_calendar_success_message(res)
                return True, summary
            else:
                err_msg = res.get("message", "Failed to create calendar event.") if isinstance(res, dict) else "Failed to create calendar event."
                return False, err_msg

        elif tool_name == "list_calendar":
            start_dt = args.get("start_datetime")
            end_dt = args.get("end_datetime")

            events = list_upcoming_events(start_datetime=start_dt, end_datetime=end_dt)
            if not isinstance(events, list):
                return False, "Execution error: Unexpected result format from calendar reader."

            if not events:
                return True, "📅 You have no calendar events during that period."

            lines = ["📅 *Upcoming Calendar Events:*\n"]
            for ev in events:
                t = ev.get("title", "(No Title)")
                s = ev.get("start", "")
                e = ev.get("end", "")
                atts = ev.get("attendees", [])
                att_str = f"\n   👥 Attendees: {', '.join(atts)}" if atts else ""
                lines.append(f"• *{t}*\n   ⏰ `{s}` to `{e}`{att_str}")

            return True, "\n\n".join(lines)

        elif tool_name == "set_alarm":
            message = str(args.get("message", "")).strip()
            fire_at_iso = str(args.get("fire_at", "")).strip()
            if not message or not fire_at_iso:
                return False, "Message and fire_at required for set_alarm."

            try:
                if len(fire_at_iso) == 10 and fire_at_iso.count("-") == 2:
                    dt_obj = datetime.strptime(fire_at_iso, "%Y-%m-%d")
                else:
                    dt_obj = datetime.fromisoformat(fire_at_iso)
                fire_at_ts = dt_obj.timestamp()

                alarm_id = db.save_alarm(chat_id=chat_id, message=message, fire_at=fire_at_ts)
                formatted_date, formatted_time = _format_alarm_datetime(fire_at_iso)
                time_display = f"\n🕘 {formatted_time}" if formatted_time else ""
                succ_msg = (
                    f"✅ *Reminder set!*\n\n"
                    f"⏰ {message}\n"
                    f"📅 {formatted_date}"
                    f"{time_display}"
                )
                return True, succ_msg
            except Exception as e:
                return False, _sanitize_error_message(e)

        elif tool_name == "none":
            msg = str(args.get("message", "No action executed.")).strip()
            return True, msg

        else:
            return False, f"Unknown tool `{tool_name}`."

    except Exception as e:
        return False, _sanitize_error_message(e)


def execute_task_plan(plan: TaskPlan, chat_id: str) -> str:
    """
    Execute all tasks in a TaskPlan sequentially.

    Stops immediately on the first task failure.
    Returns a formatted Telegram Markdown summary of all task results.
    """
    results = []
    stopped = False

    for i, task in enumerate(plan.tasks, start=1):
        if stopped:
            results.append({
                "index": i,
                "task": task,
                "status": "skipped",
                "message": "Not executed (cancelled due to previous failure)."
            })
            continue

        success, output_msg = execute_single_tool_call(task, chat_id)
        if success:
            results.append({
                "index": i,
                "task": task,
                "status": "success",
                "message": output_msg
            })
        else:
            results.append({
                "index": i,
                "task": task,
                "status": "failed",
                "message": output_msg
            })
            stopped = True

    completed_count = sum(1 for r in results if r["status"] == "success")
    total_count = len(plan.tasks)

    lines = ["📋 *Task Plan Execution Result*\n"]
    for r in results:
        task_name = _format_tool_name(r["task"].tool)
        idx = r["index"]
        if r["status"] == "success":
            status_icon = "✅"
        elif r["status"] == "failed":
            status_icon = "❌"
        else:
            status_icon = "⏭️"

        lines.append(f"{status_icon} *{idx}. {task_name}*")
        lines.append(f"   {r['message']}")
        lines.append("")

    lines.append(f"*{completed_count}/{total_count} actions completed.*")
    summary_text = "\n".join(lines)

    # Log summary in conversation history
    db.add_message(chat_id, "assistant", f"Executed TaskPlan ({completed_count}/{total_count} completed):\n{summary_text}")

    return summary_text


def handle_callback_query(cq: dict):
    """Handle callback button clicks from inline keyboards safely."""
    cq_id = cq.get("id", "") if isinstance(cq, dict) else ""
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id", config.TELEGRAM_CHAT_ID)) if isinstance(cq, dict) else str(config.TELEGRAM_CHAT_ID)

    try:
        data = cq.get("data", "")

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

        elif cmd == "confirm_del":
            payload = action["payload"]
            answer_callback_query(cq_id, "Deleting contact...")
            db.delete_contact(chat_id, payload["name"])
            db.scrub_contact_from_history(chat_id, payload["email"])
            send_telegram_message(f"✅ Successfully removed *{payload['name']}* (`{payload['email']}`) from your contacts!", chat_id=chat_id)
            db.delete_pending_action(action_id)

        elif cmd == "cancel_del":
            answer_callback_query(cq_id, "Cancelled.")
            send_telegram_message("❌ Contact removal cancelled.", chat_id=chat_id)
            db.delete_pending_action(action_id)

        elif cmd == "confirm_cal":
            if action.get("action_type") != "confirm_schedule_calendar":
                answer_callback_query(cq_id, "Invalid action type.")
                send_telegram_message("⚠️ Invalid action type.", chat_id=chat_id)
                return

            if str(action.get("chat_id")) != str(chat_id):
                answer_callback_query(cq_id, "Unauthorized action.")
                send_telegram_message("⚠️ You do not have permission to confirm this event.", chat_id=chat_id)
                return

            payload = action.get("payload", {})
            # Consume pending action BEFORE execution to prevent duplicate execution on rapid double-click
            db.delete_pending_action(action_id)

            answer_callback_query(cq_id, "Scheduling event...")
            send_telegram_message("⏳ Creating calendar event...", chat_id=chat_id)

            title = payload.get("title", "")
            date = payload.get("date", "")
            start_time = payload.get("start_time", "")
            duration_minutes = payload.get("duration_minutes", 30)
            attendees = payload.get("attendees")

            res = create_event(
                title=title,
                date=date,
                start_time=start_time,
                duration_minutes=duration_minutes,
                attendees=attendees,
            )
            if isinstance(res, dict) and res.get("status") == "success":
                succ_msg = _format_calendar_success_message(res)
                send_telegram_message(succ_msg, chat_id=chat_id)
                db.add_message(chat_id, "assistant", f"Created calendar event '{res.get('title', title)}'.")
            else:
                err_msg = res.get("message", "Failed to create calendar event.") if isinstance(res, dict) else "Failed to create calendar event."
                send_telegram_message(err_msg if err_msg.startswith("⚠️") or err_msg.startswith("Calendar API error") else f"❌ {err_msg}", chat_id=chat_id)

        elif cmd == "cancel_cal":
            if action.get("action_type") != "confirm_schedule_calendar":
                answer_callback_query(cq_id, "Invalid action type.")
                send_telegram_message("⚠️ Invalid action type.", chat_id=chat_id)
                return

            if str(action.get("chat_id")) != str(chat_id):
                answer_callback_query(cq_id, "Unauthorized action.")
                send_telegram_message("⚠️ You do not have permission to cancel this event.", chat_id=chat_id)
                return

            db.delete_pending_action(action_id)
            answer_callback_query(cq_id, "Cancelled.")
            send_telegram_message("❌ *Calendar event cancelled*", chat_id=chat_id)

        elif cmd == "confirm_alarm":
            if not action:
                answer_callback_query(cq_id, "Action expired or unavailable.")
                send_telegram_message("⚠️ This action is no longer available or has already been processed.", chat_id=chat_id)
                return

            if action.get("action_type") != "confirm_set_alarm":
                answer_callback_query(cq_id, "Invalid action type.")
                send_telegram_message("⚠️ Invalid action type.", chat_id=chat_id)
                return

            if str(action.get("chat_id")) != str(chat_id):
                answer_callback_query(cq_id, "Unauthorized action.")
                send_telegram_message("⚠️ You do not have permission to confirm this reminder.", chat_id=chat_id)
                return

            payload = action.get("payload", {})
            db.delete_pending_action(action_id)

            message = payload.get("message", "")
            fire_at_iso = payload.get("fire_at", "")

            try:
                if len(fire_at_iso) == 10 and fire_at_iso.count("-") == 2:
                    dt_obj = datetime.strptime(fire_at_iso, "%Y-%m-%d")
                else:
                    dt_obj = datetime.fromisoformat(fire_at_iso)
                fire_at_ts = dt_obj.timestamp()

                alarm_id = db.save_alarm(chat_id=chat_id, message=message, fire_at=fire_at_ts)

                answer_callback_query(cq_id, "Reminder set!")
                formatted_date, formatted_time = _format_alarm_datetime(fire_at_iso)
                time_display = f"\n🕘 {formatted_time}" if formatted_time else ""
                succ_msg = (
                    f"✅ *Reminder set!*\n\n"
                    f"⏰ {message}\n"
                    f"📅 {formatted_date}"
                    f"{time_display}"
                )
                send_telegram_message(succ_msg, chat_id=chat_id)
                db.add_message(chat_id, "assistant", f"Set reminder '{message}'.")

            except Exception as e:
                print(f"[Alarm] Error saving alarm: {e}")
                answer_callback_query(cq_id, "Failed to set reminder.")
                send_telegram_message("⚠️ Sorry, something went wrong setting the reminder.", chat_id=chat_id)

        elif cmd == "edit_alarm":
            if not action:
                answer_callback_query(cq_id, "Action expired or unavailable.")
                send_telegram_message("⚠️ This action is no longer available or has already been processed.", chat_id=chat_id)
                return

            if action.get("action_type") != "confirm_set_alarm":
                answer_callback_query(cq_id, "Invalid action type.")
                send_telegram_message("⚠️ Invalid action type.", chat_id=chat_id)
                return

            if str(action.get("chat_id")) != str(chat_id):
                answer_callback_query(cq_id, "Unauthorized action.")
                send_telegram_message("⚠️ You do not have permission to edit this reminder.", chat_id=chat_id)
                return

            payload = action.get("payload", {})
            answer_callback_query(cq_id, "Editing reminder...")
            db.save_pending_action(f"editing_alarm_{chat_id}", chat_id, "editing_alarm", payload, ttl_seconds=300)
            send_telegram_message(
                "✏️ Reply with what you would like to change.\n\n"
                "Examples:\n"
                "• `call Bob` (change task name)\n"
                "• `at 8:30 PM` (change time)\n"
                "• `tomorrow at 9 AM to call Priya` (change time & task)",
                chat_id=chat_id
            )

        elif cmd == "cancel_alarm":
            if not action:
                answer_callback_query(cq_id, "Action expired or unavailable.")
                send_telegram_message("⚠️ This action is no longer available or has already been processed.", chat_id=chat_id)
                return

            if action.get("action_type") != "confirm_set_alarm":
                answer_callback_query(cq_id, "Invalid action type.")
                send_telegram_message("⚠️ Invalid action type.", chat_id=chat_id)
                return

            if str(action.get("chat_id")) != str(chat_id):
                answer_callback_query(cq_id, "Unauthorized action.")
                send_telegram_message("⚠️ You do not have permission to cancel this reminder.", chat_id=chat_id)
                return

            db.delete_pending_action(action_id)
            answer_callback_query(cq_id, "Cancelled.")
            send_telegram_message("❌ *Reminder cancelled.*", chat_id=chat_id)

        # ── Phase 2.7 & 2.8: TaskPlan multi-task execution callbacks ─────────
        elif cmd == "plan_execute":
            # 1. Verify action_type
            if action.get("action_type") != "confirm_taskplan":
                answer_callback_query(cq_id, "Invalid action type.")
                send_telegram_message("⚠️ Invalid action type for task plan.", chat_id=chat_id)
                return

            # 2. Ownership / chat safety check
            if str(action.get("chat_id")) != str(chat_id):
                answer_callback_query(cq_id, "Unauthorized action.")
                send_telegram_message("⚠️ You do not have permission to execute this plan.", chat_id=chat_id)
                return

            # 3. Reconstruct and validate TaskPlan from stored payload
            payload = action.get("payload")
            try:
                if not isinstance(payload, dict):
                    raise ValueError("Stored payload is not a dictionary.")
                plan = TaskPlan.model_validate(payload)
            except Exception:
                answer_callback_query(cq_id, "Invalid task plan data.")
                send_telegram_message("⚠️ The pending task plan is invalid or corrupted.", chat_id=chat_id)
                db.delete_pending_action(action_id)
                return

            # 4. ONE-SHOT CONSUMPTION: Delete pending action BEFORE execution starts
            # This prevents duplicate execution if Execute All is clicked twice rapidly.
            db.delete_pending_action(action_id)

            # 5. Acknowledge callback immediately
            answer_callback_query(cq_id, "Executing tasks...")

            # 6. Execute tasks sequentially and send final summary report
            summary_msg = execute_task_plan(plan, chat_id)
            try:
                send_telegram_message(summary_msg, chat_id=chat_id)
            except Exception as err:
                print(f"[Telegram Error] Failed to send TaskPlan summary report: {err}")

        elif cmd == "plan_cancel":
            # 1. Verify action_type
            if action.get("action_type") != "confirm_taskplan":
                answer_callback_query(cq_id, "Invalid action type.")
                send_telegram_message("⚠️ Invalid action type for task plan.", chat_id=chat_id)
                return

            # 2. Ownership / chat safety check
            if str(action.get("chat_id")) != str(chat_id):
                answer_callback_query(cq_id, "Unauthorized action.")
                send_telegram_message("⚠️ You do not have permission to cancel this plan.", chat_id=chat_id)
                return

            # 3. Delete action & acknowledge
            db.delete_pending_action(action_id)
            answer_callback_query(cq_id, "Plan cancelled.")
            send_telegram_message("❌ Task plan cancelled.", chat_id=chat_id)

    except Exception as e:
        print(f"[Telegram Error] Exception in callback query handler: {_sanitize_error_message(e)}")
        try:
            if cq_id:
                answer_callback_query(cq_id, "Error handling callback.")
            send_telegram_message(_sanitize_error_message(e), chat_id=chat_id)
        except Exception:
            pass



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


# ──────────────────────────────────────────────
# BACKGROUND ALARM CHECKER THREAD
# ──────────────────────────────────────────────

def check_and_fire_due_alarms():
    """Check SQLite for pending alarms whose fire_at timestamp has arrived and deliver them via Telegram."""
    try:
        now_ts = int(time.time())
        pending_alarms = db.get_pending_alarms()
        if not isinstance(pending_alarms, list):
            return

        for alarm in pending_alarms:
            if not isinstance(alarm, dict):
                continue
            alarm_id = alarm.get("id")
            chat_id = alarm.get("chat_id")
            message = alarm.get("message")
            fire_at = alarm.get("fire_at")

            if fire_at is not None and fire_at <= now_ts:
                try:
                    msg = f"⏰ Reminder: {message}"
                    sent_success = send_telegram_message(msg, chat_id=chat_id)
                    if sent_success is not False:
                        db.mark_alarm_fired(alarm_id)
                    else:
                        print(f"[Alarm Checker Error] Telegram send returned False for alarm {alarm_id}. Leaving alarm pending.")
                except Exception as alarm_err:
                    print(f"[Alarm Checker Error] Failed to send/mark alarm {alarm_id}: {_sanitize_error_message(alarm_err)}")
    except Exception as e:
        print(f"[Alarm Checker Error] Exception during alarm check cycle: {_sanitize_error_message(e)}")


def alarm_checker_loop():
    """Daemon loop that periodically polls and fires due alarms every ALARM_CHECK_INTERVAL_SECONDS."""
    while True:
        try:
            check_and_fire_due_alarms()
        except Exception as e:
            print(f"[Alarm Checker Loop Error] Unhandled exception in loop: {_sanitize_error_message(e)}")
        time.sleep(ALARM_CHECK_INTERVAL_SECONDS)


def main():
    print("=" * 50)
    print("  Personal AI Agent Starting (Fast Mode)...")
    print("=" * 50)

    # Start background alarm checker daemon thread
    alarm_thread = threading.Thread(
        target=alarm_checker_loop,
        daemon=True,
        name="AlarmCheckerThread"
    )
    alarm_thread.start()

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

