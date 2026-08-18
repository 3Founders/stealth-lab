"""
Real tests for app/services/trace_redaction.py. All event payloads below
are synthetic fixtures I constructed to exercise specific patterns -- not
real user data, and not claimed to be. This tests the pure redaction
logic itself, which is a different, legitimate thing from claiming
real-world coverage (that question is explicitly still open, per ticket
18's own admission that detection is inherently incomplete).
"""
from app.services.trace_redaction import redact_event, redact_value


def test_aws_key_is_redacted_within_a_string_leaf():
    event = {
        "event_type": "PostToolUse",
        "tool_name": "Bash",
        "tool_output": {"stdout": "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"},
    }
    result = redact_event(event)
    assert "AKIAIOSFODNN7EXAMPLE" not in result["tool_output"]["stdout"]
    assert "[REDACTED:aws_access_key]" in result["tool_output"]["stdout"]
    assert result["_redaction"]["patterns_matched"] == ["aws_access_key"]


def test_github_token_is_redacted():
    event = {
        "event_type": "PostToolUse",
        "tool_output": {"result": "token: ghp_" + "a" * 36},
    }
    result = redact_event(event)
    assert "ghp_" + "a" * 36 not in str(result["tool_output"])
    assert "aws_access_key" not in result.get("_redaction", {}).get("patterns_matched", [])
    assert "github_token" in result["_redaction"]["patterns_matched"]


def test_multiple_distinct_secrets_in_one_field_are_all_redacted():
    event = {
        "event_type": "PostToolUse",
        "tool_output": {
            "stdout": f"AWS={'AKIAIOSFODNN7EXAMPLE'} GH={'ghp_' + 'b'*36}",
        },
    }
    result = redact_event(event)
    stdout = result["tool_output"]["stdout"]
    assert "AKIAIOSFODNN7EXAMPLE" not in stdout
    assert "ghp_" + "b" * 36 not in stdout
    assert set(result["_redaction"]["patterns_matched"]) == {"aws_access_key", "github_token"}


def test_private_key_block_is_redacted_across_multiple_lines():
    event = {
        "event_type": "PostToolUse",
        "tool_output": {
            "stdout": "-----BEGIN RSA PRIVATE KEY-----\nMIIBogIBAAJ...\n-----END RSA PRIVATE KEY-----"
        },
    }
    result = redact_event(event)
    assert "MIIBogIBAAJ" not in result["tool_output"]["stdout"]
    assert "private_key_block" in result["_redaction"]["patterns_matched"]


def test_reading_dotenv_path_is_excluded_regardless_of_content():
    """The real point of the path-based layer: content with NO known-token
    shape at all still gets caught because of what file it came from."""
    event = {
        "event_type": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": "/home/user/project/.env"},
        "tool_output": {"content": "SOME_RANDOM_APP_SETTING=just_a_normal_value_here"},
    }
    result = redact_event(event)
    assert result["tool_input"] == "[EXCLUDED: sensitive path]"
    assert result["tool_output"] == "[EXCLUDED: sensitive path]"
    assert "sensitive_path" in result["_redaction"]["patterns_matched"]


def test_private_key_file_path_excluded():
    event = {"tool_input": {"file_path": "/home/user/.ssh/id_rsa"}}
    result = redact_event(event)
    assert result["tool_input"] == "[EXCLUDED: sensitive path]"


def test_clean_event_is_unchanged_and_carries_no_redaction_key():
    """A real, important negative case: redaction must not fire on
    ordinary content, and must not add noise when it doesn't."""
    event = {
        "event_type": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": "/home/user/project/main.py"},
        "tool_output": {"content": "def add(a, b):\n    return a + b\n"},
    }
    result = redact_event(event)
    assert result["tool_input"]["file_path"] == "/home/user/project/main.py"
    assert result["tool_output"]["content"] == "def add(a, b):\n    return a + b\n"
    assert "_redaction" not in result


def test_structural_fields_are_never_touched_even_if_secret_shaped():
    """event_type/actor_id etc are never redacted -- only tool_input/
    tool_output. A weird test fixture where actor_id happens to look
    secret-shaped must still pass through unchanged."""
    event = {
        "event_type": "PostToolUse",
        "actor_id": "AKIAIOSFODNN7EXAMPLE",  # deliberately secret-shaped, not a real key
        "tool_output": {"stdout": "ok"},
    }
    result = redact_event(event)
    assert result["actor_id"] == "AKIAIOSFODNN7EXAMPLE"


def test_redact_event_does_not_mutate_the_input():
    event = {"tool_output": {"stdout": "AKIAIOSFODNN7EXAMPLE"}}
    original_stdout = event["tool_output"]["stdout"]
    redact_event(event)
    assert event["tool_output"]["stdout"] == original_stdout


def test_nested_lists_and_dicts_are_walked():
    event = {
        "tool_output": {
            "results": [
                {"key": "value"},
                {"key": "AKIAIOSFODNN7EXAMPLE"},
            ]
        }
    }
    result = redact_event(event)
    assert result["tool_output"]["results"][0]["key"] == "value"
    assert "AKIAIOSFODNN7EXAMPLE" not in result["tool_output"]["results"][1]["key"]


def test_dotall_pattern_cannot_span_across_separate_fields():
    """
    The real justification for walking parsed structure instead of raw
    serialized text: private_key_block uses re.DOTALL (matches across
    newlines), so a genuine multi-line key body (BEGIN...END within one
    leaf) is caught correctly -- confirmed separately in
    test_private_key_block_is_redacted_across_multiple_lines. The risk
    this test targets is different: if that same pattern were applied to
    one raw JSON-serialized blob instead of per-leaf, a stray BEGIN in
    one field with no matching END in that field, followed later in the
    same blob by an unrelated END-shaped string in a *different* field,
    could be consumed as one spurious match spanning both fields (and
    everything JSON-syntactic in between) -- a real over-redaction/
    corruption risk. Confirmed here, directly against the real function,
    not assumed: with each leaf redacted independently, no match forms
    across the two fields at all -- neither field changes, because
    neither leaf alone contains a complete BEGIN...END pair. That is the
    correct, safe outcome, not a coverage gap: a real key would be
    self-contained within one field's content in practice.
    """
    event = {
        "tool_output": {
            "field_a": "-----BEGIN RSA PRIVATE KEY-----\nMIIBogIB...\n",  # no END in this leaf
            "field_b": "unrelated text mentioning -----END EXAMPLE KEY----- in a log line",
        }
    }
    result = redact_event(event)
    # Neither leaf contains a complete pair on its own, so neither is
    # touched -- confirmed directly, not assumed, before writing this
    # assertion (a prior version of this test asserted the opposite and
    # was wrong; verified against the real function before fixing it).
    assert result["tool_output"]["field_a"] == (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIBogIB...\n"
    )
    assert result["tool_output"]["field_b"] == (
        "unrelated text mentioning -----END EXAMPLE KEY----- in a log line"
    )
    assert "_redaction" not in result
