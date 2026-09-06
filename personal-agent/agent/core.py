# agent/core.py
import json
import requests
from typing import List, Dict
from pydantic import ValidationError
from tenacity import retry, wait_exponential, stop_after_attempt
import config
from agent.schemas import ToolCall
from agent.prompts import AGENT_SYSTEM_PROMPT, REVISE_DRAFT_SYSTEM_PROMPT

@retry(wait=wait_exponential(min=1, max=8), stop=stop_after_attempt(3), reraise=True)
def _ollama_chat(messages: List[Dict[str, str]], system: str) -> str:
    """Send chat request to Ollama with forced JSON output format."""
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.15}
    }
    response = requests.post(
        f"{config.OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]

def validate_tool_call(tool_call: ToolCall, user_message: str) -> ToolCall:
    """
    Code-level validation to prevent model hallucinations:
    - send_email: 'to' recipient email address must appear literally in user_message.
    - draft_reply: 'email_id' must appear literally in user_message.
    """
    user_msg_lower = user_message.lower()

    if tool_call.tool == "send_email":
        to_address = str(tool_call.args.get("to", "")).strip().lower()
        if not to_address or to_address not in user_msg_lower:
            overridden = ToolCall(
                tool="none",
                args={"message": "Could you please provide the recipient's actual email address?"},
                reasoning="Recipient email address was not explicitly provided in the user's input message (prevented hallucination)."
            )
            if hasattr(tool_call, "_was_retried"):
                overridden._was_retried = tool_call._was_retried
            return overridden

    elif tool_call.tool == "draft_reply":
        email_id = str(tool_call.args.get("email_id", "")).strip().lower()
        if not email_id or email_id not in user_msg_lower:
            overridden = ToolCall(
                tool="none",
                args={"message": "Could you please specify which email ID you want to reply to?"},
                reasoning="Email ID was not explicitly provided in the user's input message (prevented hallucination)."
            )
            if hasattr(tool_call, "_was_retried"):
                overridden._was_retried = tool_call._was_retried
            return overridden

    return tool_call

def call_agent(user_message: str, history: List[Dict[str, str]] = None) -> ToolCall:
    """
    Main entry point for agent tool choice.
    Returns validated ToolCall instance.
    """
    if history is None:
        history = []

    messages = history + [{"role": "user", "content": user_message}]
    raw_response = _ollama_chat(messages, AGENT_SYSTEM_PROMPT)

    try:
        res = ToolCall.model_validate_json(raw_response)
        res._was_retried = False
        return validate_tool_call(res, user_message)
    except (ValidationError, json.JSONDecodeError) as e:
        # Single correction retry loop
        correction_msg = (
            f"Your previous output was invalid or missing required fields. Error: {e}.\n"
            "Respond ONLY with valid JSON matching {"
            '"tool": "<name>", "args": {...}, "reasoning": "..."}'
        )
        messages.append({"role": "assistant", "content": raw_response})
        messages.append({"role": "user", "content": correction_msg})
        raw_retry = _ollama_chat(messages, AGENT_SYSTEM_PROMPT)
        res = ToolCall.model_validate_json(raw_retry)
        res._was_retried = True
        return validate_tool_call(res, user_message)

def _clean_json_str(text: str) -> str:
    """Helper to strip markdown code blocks from model JSON output."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def _normalize_text(text: str) -> str:
    """Normalize text for whitespace and case insensitive comparison."""
    return " ".join(str(text or "").lower().split())

def revise_draft(original_draft: dict, user_feedback: str) -> ToolCall:
    """Revise an existing draft given feedback."""
    system_prompt = REVISE_DRAFT_SYSTEM_PROMPT.format(
        to=original_draft.get("to", ""),
        subject=original_draft.get("subject", ""),
        body=original_draft.get("body", ""),
        feedback=user_feedback
    )
    messages = [{"role": "user", "content": f"Please update the draft according to these instructions: {user_feedback}"}]
    
    print("\n--- [DEBUG REVISE_DRAFT PROMPT] ---")
    print(f"SYSTEM PROMPT:\n{system_prompt}")
    print(f"USER FEEDBACK: {user_feedback}")
    print("-----------------------------------\n")

    raw_response = _ollama_chat(messages, system_prompt)

    print("\n--- [DEBUG RAW OLLAMA RESPONSE] ---")
    print(raw_response)
    print("-----------------------------------\n")

    res = None
    try:
        cleaned = _clean_json_str(raw_response)
        res = ToolCall.model_validate_json(cleaned)
    except (ValidationError, json.JSONDecodeError) as e:
        # Single correction retry loop for JSON format
        correction_msg = (
            f"Your previous output was invalid JSON or missing required fields: {e}.\n"
            "Respond ONLY with valid JSON matching: "
            '{"tool": "send_email", "args": {"to": "...", "subject": "...", "body": "..."}, "reasoning": "..."}'
        )
        messages.append({"role": "assistant", "content": raw_response})
        messages.append({"role": "user", "content": correction_msg})
        raw_retry = _ollama_chat(messages, system_prompt)
        try:
            cleaned_retry = _clean_json_str(raw_retry)
            res = ToolCall.model_validate_json(cleaned_retry)
        except Exception:
            # Safe fallback: preserve existing draft fields without concatenating notes
            res = ToolCall(
                tool="send_email",
                args={
                    "to": original_draft.get("to", ""),
                    "subject": original_draft.get("subject", ""),
                    "body": original_draft.get("body", ""),
                    "is_unchanged": True
                },
                reasoning="I couldn't confidently make that change — could you rephrase what you'd like edited?"
            )

    if res and res.tool == "send_email":
        orig_body = original_draft.get("body", "")
        new_body = res.args.get("body", "")
        orig_subj = original_draft.get("subject", "")
        new_subj = res.args.get("subject", "")

        if _normalize_text(orig_body) == _normalize_text(new_body) and _normalize_text(orig_subj) == _normalize_text(new_subj):
            res.reasoning = "I couldn't confidently make that change — could you rephrase what you'd like edited?"
            res.args["is_unchanged"] = True

    return res
