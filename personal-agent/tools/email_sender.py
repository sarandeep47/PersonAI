# personal-agent/tools/email_sender.py
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from langchain.tools import tool
import config


def send_email_raw(to: str, subject: str, body: str) -> bool:
    """
    Send an email via Gmail SMTP.
    Returns True on success, False on failure.
    """
    try:
        msg = MIMEMultipart()
        msg["From"] = config.EMAIL_ADDRESS
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
            server.sendmail(config.EMAIL_ADDRESS, to, msg.as_string())

        print(f"[EmailSender] Email sent to {to}")
        return True
    except Exception as e:
        print(f"[EmailSender] Error sending email: {e}")
        return False


@tool
def send_email(to_subject_body: str) -> str:
    """
    Send an email. Input must be formatted as:
    TO: recipient@example.com | SUBJECT: Your subject here | BODY: Your message here

    Example:
    TO: john@example.com | SUBJECT: Meeting rescheduled | BODY: Hi John, the meeting is moved to Friday at 3pm.
    """
    try:
        parts = {}
        for part in to_subject_body.split("|"):
            part = part.strip()
            if ":" in part:
                key, value = part.split(":", 1)
                parts[key.strip().upper()] = value.strip()

        to = parts.get("TO", "")
        subject = parts.get("SUBJECT", "(No Subject)")
        body = parts.get("BODY", "")

        if not to:
            return "Error: No recipient (TO) specified."
        if not body:
            return "Error: Email body is empty."

        success = send_email_raw(to, subject, body)
        if success:
            return f"Email successfully sent to {to} with subject '{subject}'."
        else:
            return f"Failed to send email to {to}. Check credentials and try again."
    except Exception as e:
        return f"Error parsing email input: {e}"
