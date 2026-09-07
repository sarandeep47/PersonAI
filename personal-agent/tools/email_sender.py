# personal-agent/tools/email_sender.py
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from langchain.tools import tool
import config


import os
from email.mime.base import MIMEBase
from email import encoders

def send_email_raw(to: str, subject: str, body: str, attachment_path: str = None) -> bool:
    """
    Send an email via Gmail SMTP with optional file attachment.
    Returns True on success, False on failure.
    """
    try:
        msg = MIMEMultipart()
        msg["From"] = config.EMAIL_ADDRESS
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        if attachment_path and os.path.exists(attachment_path):
            filename = os.path.basename(attachment_path)
            with open(attachment_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename= {filename}",
            )
            msg.attach(part)
            print(f"[EmailSender] Attached file: {filename}")

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
