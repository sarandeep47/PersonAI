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
        return ToolCall.model_validate_json(raw_response)
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
        return ToolCall.model_validate_json(raw_retry)

def revise_draft(original_draft: dict, user_feedback: str) -> ToolCall:
    """Revise an existing draft given feedback."""
    system_prompt = REVISE_DRAFT_SYSTEM_PROMPT.format(
        to=original_draft.get("to", ""),
        subject=original_draft.get("subject", ""),
        body=original_draft.get("body", ""),
        feedback=user_feedback
    )
    messages = [{"role": "user", "content": f"Please update the draft: {user_feedback}"}]
    raw_response = _ollama_chat(messages, system_prompt)
    try:
        return ToolCall.model_validate_json(raw_response)
    except Exception:
        # Fallback to simple edit
        return ToolCall(
            tool="send_email",
            args={
                "to": original_draft.get("to", ""),
                "subject": original_draft.get("subject", ""),
                "body": f"{original_draft.get('body', '')}\n\nNote: {user_feedback}"
            },
            reasoning="Fallback revision due to parsing issue"
        )
