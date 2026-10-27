from app.utils.condense import condense


def test_short_text_is_untouched():
    assert condense("fix the parser", 2000) == "fix the parser"


def test_long_text_keeps_head_and_tail_and_is_exactly_the_limit():
    text = "HEAD" + "x" * 9000 + "THE REAL ASK"
    out = condense(text, 2000)
    assert len(out) == 2000
    assert out.startswith("HEAD") and out.endswith("THE REAL ASK")


def test_tiny_limit_falls_back_to_a_plain_cut():
    assert condense("a" * 50, 8) == "a" * 8


def test_the_mcp_surface_tells_the_model_the_unit_is_a_task():
    import app.mcp_server.server as srv
    from app.mcp_server.resources import FIND_WAYS_GUIDE

    tool = next(t for t in srv.server._tool_manager.list_tools() if t.name == "find_ways")
    for name, text in [("instructions", srv._V1_INSTRUCTIONS), ("find_ways", tool.description), ("guide", FIND_WAYS_GUIDE)]:
        flat = " ".join(text.split())
        assert "TASK, not a message" in flat, name
        assert "goal" in flat and "context" in flat, name
