# agent/schemas.py
from pydantic import BaseModel, Field
from typing import Literal, Union, Dict, Any

class SendEmailArgs(BaseModel):
    to: str = Field(..., description="Recipient email address — must be a valid email")
    subject: str = Field(..., description="Short, natural email subject line")
    body: str = Field(..., description="Full polished email body in first person")

class SearchInboxArgs(BaseModel):
    query: str = Field(..., description="Gmail search query (e.g. 'from:boss invoice')")
    max_results: int = Field(default=5, le=20)

class ReadEmailArgs(BaseModel):
    email_id: str = Field(..., description="ID of the email to read")

class DraftReplyArgs(BaseModel):
    email_id: str = Field(..., description="ID of the email to reply to")
    instructions: str = Field(..., description="What to say in the reply")

class ExportContactsArgs(BaseModel):
    pass

class DeleteContactArgs(BaseModel):
    query: str = Field(..., description="Name, nickname, or role of the contact to delete (e.g. 'hr', 'John')")

class RenameContactArgs(BaseModel):
    query: str = Field(..., description="Current name, nickname, or role identifying the contact to rename (e.g. 'hr', 'Shylaja')")
    new_name: str = Field(..., description="The new display name to assign to the contact")

class NoneArgs(BaseModel):
    message: str = Field(..., description="Response/question to show to the user")

ToolName = Literal["send_email", "search_inbox", "read_email", "draft_reply", "export_contacts", "delete_contact", "rename_contact", "none"]

class ToolCall(BaseModel):
    tool: ToolName
    args: Dict[str, Any] = Field(default_factory=dict)
    reasoning: str = Field(default="No reasoning provided.", description="One sentence explaining why this tool was chosen")

