from typing import get_args
import pytest
from pydantic import ValidationError

from agent.schemas import (
    AddTaskArgs,
    ListTasksArgs,
    CompleteTaskArgs,
    DeleteTaskArgs,
    ToolName,
    ToolCall,
    TOOL_ARGS_SCHEMAS,
)


def test_01_add_task_args_valid():
    """1. AddTaskArgs accepts a valid title."""
    args = AddTaskArgs(title="Buy milk and eggs")
    assert args.title == "Buy milk and eggs"

    # Whitespace trimming
    args_space = AddTaskArgs(title="   Submit report   ")
    assert args_space.title == "Submit report"


def test_02_add_task_args_invalid():
    """2. AddTaskArgs rejects empty or missing title."""
    with pytest.raises(ValidationError) as exc_info:
        AddTaskArgs(title="")
    assert "Title must not be empty" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info2:
        AddTaskArgs(title="   ")
    assert "Title must not be empty" in str(exc_info2.value)

    with pytest.raises(ValidationError):
        AddTaskArgs()  # missing required field


def test_03_complete_task_args_valid():
    """3. CompleteTaskArgs accepts a valid task_id."""
    args = CompleteTaskArgs(task_id="task_12345")
    assert args.task_id == "task_12345"

    args_space = CompleteTaskArgs(task_id="  task_abc  ")
    assert args_space.task_id == "task_abc"


def test_04_complete_task_args_invalid():
    """4. CompleteTaskArgs rejects empty or missing task_id."""
    with pytest.raises(ValidationError) as exc_info:
        CompleteTaskArgs(task_id="")
    assert "task_id must not be empty" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info2:
        CompleteTaskArgs(task_id="   ")
    assert "task_id must not be empty" in str(exc_info2.value)

    with pytest.raises(ValidationError):
        CompleteTaskArgs()  # missing required field


def test_05_delete_task_args_valid():
    """5. DeleteTaskArgs accepts a valid task_id."""
    args = DeleteTaskArgs(task_id="task_67890")
    assert args.task_id == "task_67890"

    args_space = DeleteTaskArgs(task_id="  task_xyz  ")
    assert args_space.task_id == "task_xyz"


def test_06_delete_task_args_invalid():
    """6. DeleteTaskArgs rejects empty or missing task_id."""
    with pytest.raises(ValidationError) as exc_info:
        DeleteTaskArgs(task_id="")
    assert "task_id must not be empty" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info2:
        DeleteTaskArgs(task_id="   ")
    assert "task_id must not be empty" in str(exc_info2.value)

    with pytest.raises(ValidationError):
        DeleteTaskArgs()  # missing required field


def test_07_list_tasks_args():
    """7. ListTasksArgs accepts empty arguments."""
    args = ListTasksArgs()
    assert isinstance(args, ListTasksArgs)


def test_08_toolname_enum_contains_task_tools():
    """8. Verify all four task tool names are present in ToolName Literal enum."""
    literals = get_args(ToolName)
    assert "add_task" in literals
    assert "list_tasks" in literals
    assert "complete_task" in literals
    assert "delete_task" in literals


def test_09_tool_args_schemas_mapping():
    """9. Verify TOOL_ARGS_SCHEMAS maps all four task tools correctly."""
    assert TOOL_ARGS_SCHEMAS["add_task"] is AddTaskArgs
    assert TOOL_ARGS_SCHEMAS["list_tasks"] is ListTasksArgs
    assert TOOL_ARGS_SCHEMAS["complete_task"] is CompleteTaskArgs
    assert TOOL_ARGS_SCHEMAS["delete_task"] is DeleteTaskArgs


def test_10_tool_call_with_task_tools():
    """10. Verify ToolCall accepts tool calls for all four task tools."""
    tc1 = ToolCall(tool="add_task", args={"title": "Test task"}, reasoning="Add new task")
    assert tc1.tool == "add_task"
    assert tc1.args["title"] == "Test task"

    tc2 = ToolCall(tool="list_tasks", args={}, reasoning="List all tasks")
    assert tc2.tool == "list_tasks"

    tc3 = ToolCall(tool="complete_task", args={"task_id": "123"}, reasoning="Complete task")
    assert tc3.tool == "complete_task"

    tc4 = ToolCall(tool="delete_task", args={"task_id": "123"}, reasoning="Delete task")
    assert tc4.tool == "delete_task"
