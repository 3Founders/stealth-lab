import pytest
from pathlib import Path
import asyncio
from unittest.mock import AsyncMock, MagicMock
from tasknode_driver import mark_step_done, run_step

def test_mark_step_done_success(tmp_path):
    f = tmp_path / "tasknode.md"
    content = "1. [ ] Step one\n2. [ ] Step two\n3. [ ] Step three"
    f.write_text(content)
    
    mark_step_done(f, 2)
    updated = f.read_text()
    assert "1. [ ] Step one" in updated
    assert "2. [x] Step two" in updated
    assert "3. [ ] Step three" in updated

def test_mark_step_done_raises_value_error(tmp_path):
    f = tmp_path / "tasknode.md"
    content = "1. [x] Already done\n2. [ ] Step two"
    f.write_text(content)
    
    # Test already checked
    with pytest.raises(ValueError, match="Unchecked step 1 not found"):
        mark_step_done(f, 1)
        
    # Test non-existent
    with pytest.raises(ValueError, match="Unchecked step 99 not found"):
        mark_step_done(f, 99)

@pytest.mark.asyncio
async def test_run_step_facts_accumulation():
    # Mock OpenAI client
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_msg = MagicMock()
    
    # Simulate a response that says DONE immediately
    mock_msg.tool_calls = []
    mock_msg.content = "DONE"
    mock_resp.choices = [mock_msg]
    mock_resp.usage.prompt_tokens = 10
    mock_resp.usage.completion_tokens = 5
    mock_client.chat.completions.create.return_value = mock_resp

    # Test that facts are passed into the prompt
    facts = ["User ID is 123", "Email is test@example.com"]
    step_text = "Send email"
    
    await run_step(mock_client, "gpt-4", MagicMock(), step_text, facts=facts)
    
    # Verify the prompt sent to the LLM contains the facts
    args, kwargs = mock_client.chat.completions.create.call_args
    messages = kwargs['messages']
    user_msg = messages[1]['content']
    
    assert "Facts discovered so far:" in user_msg
    assert "User ID is 123" in user_msg
    assert "Email is test@example.com" in user_msg
    assert "Current step: Send email" in user_msg

@pytest.mark.asyncio
async def test_run_step_fact_extraction():
    mock_client = AsyncMock()
    
    # Mock a tool call sequence: Tool Call -> Tool Result -> DONE
    mock_tool_msg = MagicMock()
    mock_tool_call = MagicMock()
    mock_tool_call.function.name = "api_fetch"
    mock_tool_call.function.arguments = '{"url": "http://api/user"}'
    mock_tool_call.id = "call_1"
    mock_tool_msg.tool_calls = [mock_tool_call]
    mock_tool_msg.content = None
    
    mock_done_msg = MagicMock()
    mock_done_msg.tool_calls = []
    mock_done_msg.content = "DONE"
    
    resp1 = MagicMock()
    resp1.choices = [mock_tool_msg]
    resp1.usage.prompt_tokens = 10
    resp1.usage.completion_tokens = 10
    
    resp2 = MagicMock()
    resp2.choices = [mock_done_msg]
    resp2.usage.prompt_tokens = 20
    resp2.usage.completion_tokens = 5
    
    mock_client.chat.completions.create.side_effect = [resp1, resp2]

    # We need to mock the actual tool function to avoid real network calls
    import tasknode_driver
    tasknode_driver.api_fetch = MagicMock(return_value="User: Alice")

    log, new_facts = await run_step(mock_client, "gpt-4", MagicMock(), "Get user")
    
    assert len(new_facts) > 0
    assert "User: Alice" in new_facts[0]
    assert len(log) == 2
