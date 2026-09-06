# personal-agent/nlp_email.py
# Compatibility wrapper around agent.core
from agent.core import call_agent

def parse_natural_language_email(message: str) -> dict:
    """Legacy compatibility function mapping to agent.core call_agent."""
    tool_call = call_agent(message)
    if tool_call.tool == "send_email":
        return tool_call.args
    return None
