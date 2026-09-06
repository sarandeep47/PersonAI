# personal-agent/tools/email_reader.py
import imaplib
import email
from email.header import decode_header
from langchain.tools import tool
import config


def decode_mime_words(s):
    """Decode encoded email headers."""
    decoded_fragments = decode_header(s)
    return ''.join(
        fragment.decode(encoding or 'utf-8') if isinstance(fragment, bytes) else fragment
        for fragment, encoding in decoded_fragments
    )


def get_email_body(msg):
    """Extract plain text body from an email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in disposition:
                try:
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    break
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
        except Exception:
            body = ""
    return body.strip()


def fetch_unread_emails(max_count: int = None) -> list[dict]:
    """
    Connect to Gmail via IMAP and fetch unread emails.
    Returns a list of dicts with id, sender, subject, body, date.
    """
    if max_count is None:
        max_count = config.MAX_EMAILS_PER_CHECK

    emails = []
    try:
        mail = imaplib.IMAP4_SSL(config.IMAP_SERVER)
        mail.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
        mail.select("inbox")

        _, message_ids = mail.search(None, "UNSEEN")
        ids = message_ids[0].split()

        # Take the most recent ones up to max_count
        ids = ids[-max_count:] if len(ids) > max_count else ids

        for uid in ids:
            _, msg_data = mail.fetch(uid, "(RFC822)")
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            sender = decode_mime_words(msg.get("From", "Unknown"))
            subject = decode_mime_words(msg.get("Subject", "(No Subject)"))
            date = msg.get("Date", "Unknown Date")
            body = get_email_body(msg)

            emails.append({
                "id": uid.decode(),
                "sender": sender,
                "subject": subject,
                "date": date,
                "body": body[:1000],  # Truncate to 1000 chars to save LLM context
            })

        mail.logout()
    except Exception as e:
        print(f"[EmailReader] Error: {e}")

    return emails


@tool
def check_emails(dummy: str = "") -> str:
    """
    Fetch unread emails from Gmail inbox.
    Returns a summary of unread emails as a formatted string.
    Use this to check what new emails have arrived.
    """
    emails = fetch_unread_emails()
    if not emails:
        return "No unread emails found."

    result = f"Found {len(emails)} unread email(s):\n\n"
    for i, e in enumerate(emails, 1):
        result += f"--- Email {i} ---\n"
        result += f"From: {e['sender']}\n"
        result += f"Subject: {e['subject']}\n"
        result += f"Date: {e['date']}\n"
        result += f"Body preview: {e['body'][:300]}\n\n"

    return result
