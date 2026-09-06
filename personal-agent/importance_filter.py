# personal-agent/importance_filter.py
# Uses local LLM via Ollama to classify emails into 4 tiers
import requests
import json
import config

IMPORTANCE_PROMPT = """You are an email triage assistant. Classify each email into one of 4 tiers:
1. "urgent": Requires immediate attention, urgent deadline, payment due, critical issue from real person
2. "needs_reply": Direct email from a real person that expects a response or follow-up soon
3. "fyi": Informational email, status update, receipt, confirmation (important to know, but no reply needed)
4. "skip": Newsletter, marketing, promo, social media alert, automated notification

Emails to review:
{emails_text}

Reply with a JSON array ONLY — one entry per email, in the same order:
[
  {{"id": "1", "tier": "urgent|needs_reply|fyi|skip", "important": true|false, "reason": "one sentence reason"}}
]"""

def is_email_important(email: dict) -> tuple[bool, str, str]:
    """
    Single email check.
    Returns (is_important: bool, reason: str, tier: str)
    """
    sender_lower = email.get("sender", "").lower()
    for vip in config.VIP_SENDERS:
        if vip.lower() in sender_lower:
            return True, f"VIP sender: {vip}", "urgent"

    results = filter_emails_batch([email])
    if results:
        return results[0]
    return False, "Could not determine importance", "skip"


def filter_emails_batch(emails: list[dict]) -> list[tuple[bool, str, str]]:
    """
    Filter a batch of emails in one LLM call with 4-tier categorization.
    Returns list of (is_important, reason, tier) tuples.
    """
    if not emails:
        return []

    emails_text = ""
    for i, e in enumerate(emails, 1):
        emails_text += (
            f"\nEmail {i}:\n"
            f"From: {e.get('sender', '')}\n"
            f"Subject: {e.get('subject', '')}\n"
            f"Preview: {e.get('body', '')[:250]}\n"
        )

    prompt = IMPORTANCE_PROMPT.format(emails_text=emails_text)

    try:
        response = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 600,
                }
            },
            timeout=120,
        )
        response.raise_for_status()
        raw = response.json().get("response", "").strip()

        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start != -1 and end > start:
            parsed = json.loads(raw[start:end])
            results = []
            for item in parsed:
                tier = item.get("tier", "skip").lower()
                if tier not in ["urgent", "needs_reply", "fyi", "skip"]:
                    tier = "fyi" if item.get("important") else "skip"
                important = tier in ["urgent", "needs_reply", "fyi"]
                reason = item.get("reason", f"Classified as {tier}")
                results.append((important, reason, tier))
            
            while len(results) < len(emails):
                results.append((False, "No response from LLM", "skip"))
            return results

    except json.JSONDecodeError:
        print(f"[Filter] Could not parse LLM batch response as JSON")
    except Exception as e:
        print(f"[Filter] LLM error: {e}")

    return [(False, "LLM unavailable", "skip") for _ in emails]
