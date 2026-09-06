# personal-agent/agent.py
# Re-exporting unified agent core for backwards compatibility
from agent.core import call_agent

__all__ = ["call_agent"]
