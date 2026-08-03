"""Runner dispatch, with fakes for all three kinds."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.runners import default_runners
from app.runners.base import RunContext, RunnerError
from app.runners.command import CommandRunner, substitute
from app.runners.model import ModelRunner, strictify
from app.runners.python_fn import PythonRunner
from tests.helpers import FakeAnthropic, FakeUsage


@pytest.fixture
def ctx(tmp_path) -> RunContext:
    return RunContext(workdir=tmp_path, node_ref="n1", task_name="stage")


def test_dispatch_table_covers_every_implementation_kind():
    assert set(default_runners()) == {"command", "python", "model"}


# --- command ---------------------------------------------------------------


async def test_command_runner_executes_and_captures_stdout(ctx):
    runner = CommandRunner()
    result = await runner.run(
        {"template": f'"{sys.executable}" -c "print(41+1)"'}, {}, ctx
    )
    assert result.output["stdout"].strip() == "42"
    assert result.output["returncode"] == 0


async def test_command_runner_substitutes_inputs(ctx):
    result = await CommandRunner().run(
        {"template": f'"{sys.executable}" -c "import sys;print(sys.argv[1])" {{name}}'},
        {"name": "hello"},
        ctx,
    )
    assert result.output["stdout"].strip() == "hello"


async def test_a_value_with_spaces_stays_one_argument(ctx):
    """
    Split-then-substitute is what makes this safe. Substituting first would
    turn one value into three argv entries.
    """
    result = await CommandRunner().run(
        {"template": f'"{sys.executable}" -c "import sys;print(len(sys.argv)-1)" {{value}}'},
        {"value": "one two three"},
        ctx,
    )
    assert result.output["stdout"].strip() == "1"


async def test_nonzero_exit_is_a_runner_error(ctx):
    with pytest.raises(RunnerError, match="exited 3"):
        await CommandRunner().run(
            {"template": f'"{sys.executable}" -c "import sys;sys.exit(3)"'}, {}, ctx
        )


async def test_missing_binary_is_a_runner_error_not_a_crash(ctx):
    with pytest.raises(RunnerError, match="command not found"):
        await CommandRunner().run({"template": "definitely-not-a-real-binary"}, {}, ctx)


async def test_timeout_is_reported(ctx):
    with pytest.raises(RunnerError, match="timed out"):
        await CommandRunner().run(
            {"template": f'"{sys.executable}" -c "import time;time.sleep(5)"', "timeout_s": 0.3},
            {},
            ctx,
        )


def test_unknown_placeholder_is_refused():
    with pytest.raises(RunnerError, match="neither an input"):
        substitute("{nope}", {"known": 1})


async def test_declared_outputs_resolve_against_the_workdir(ctx):
    result = await CommandRunner().run(
        {
            "template": f'"{sys.executable}" -c "open(\'out.txt\',\'w\').write(\'x\')"',
            "outputs": {"text_path": "out.txt"},
        },
        {},
        ctx,
    )
    assert Path(result.output["text_path"]).read_text() == "x"


# --- python ----------------------------------------------------------------


async def test_python_runner_resolves_against_the_registry(ctx):
    def double(inputs, context):
        return {"value": inputs["value"] * 2}

    runner = PythonRunner(registry={"fake:double": double})
    result = await runner.run({"ref": "fake:double"}, {"value": 21}, ctx)
    assert result.output == {"value": 42}


async def test_python_runner_supports_async_implementations(ctx):
    async def slow(inputs, context):
        return {"ok": True}

    result = await PythonRunner(registry={"fake:slow": slow}).run({"ref": "fake:slow"}, {}, ctx)
    assert result.output == {"ok": True}


async def test_unknown_ref_lists_what_is_registered(ctx):
    runner = PythonRunner(registry={"fake:known": lambda i, c: {}})
    with pytest.raises(RunnerError, match="fake:known"):
        await runner.run({"ref": "fake:typo"}, {}, ctx)


async def test_registry_is_the_only_resolution_path(ctx):
    """
    A dotted module path is not resolvable. importlib on a database value is
    arbitrary code execution with extra steps.
    """
    with pytest.raises(RunnerError, match="unknown python ref"):
        await PythonRunner(registry={}).run({"ref": "os:system"}, {}, ctx)


async def test_implementation_exception_becomes_a_runner_error(ctx):
    def explode(inputs, context):
        raise ZeroDivisionError("nope")

    runner = PythonRunner(registry={"fake:explode": explode})
    with pytest.raises(RunnerError, match="ZeroDivisionError"):
        await runner.run({"ref": "fake:explode"}, {}, ctx)


async def test_non_dict_return_is_refused(ctx):
    runner = PythonRunner(registry={"fake:bad": lambda i, c: [1, 2, 3]})
    with pytest.raises(RunnerError, match="must return a dict"):
        await runner.run({"ref": "fake:bad"}, {}, ctx)


# --- model -----------------------------------------------------------------


async def test_model_runner_parses_structured_output(ctx):
    client = FakeAnthropic(text=json.dumps({"doc_type": "digital_table", "page_count": 2}))
    runner = ModelRunner(client_factory=lambda: client)

    schema = {
        "type": "object",
        "properties": {"doc_type": {"type": "string"}, "page_count": {"type": "integer"}},
    }
    result = await runner.run({"output_schema": schema}, {"pdf_path": "x"}, ctx)

    assert result.output == {"doc_type": "digital_table", "page_count": 2}
    sent = client.messages.calls[0]
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"]["format"]["type"] == "json_schema"
    # strictify closed the schema so structured outputs will accept it.
    assert sent["output_config"]["format"]["schema"]["additionalProperties"] is False


async def test_model_runner_costs_the_call_from_reported_usage(ctx):
    from app.config import settings

    client = FakeAnthropic(
        text="{}", usage=FakeUsage(input_tokens=1000, output_tokens=500)
    )
    result = await ModelRunner(client_factory=lambda: client).run(
        {"output_schema": {"type": "object", "properties": {}}}, {}, ctx
    )
    expected = (
        1000 * settings.model_input_cost_per_token + 500 * settings.model_output_cost_per_token
    )
    assert result.cost == pytest.approx(expected)


async def test_model_refusal_is_reported_before_content_is_read(ctx):
    client = FakeAnthropic(text="{}", stop_reason="refusal")
    with pytest.raises(RunnerError, match="declined"):
        await ModelRunner(client_factory=lambda: client).run(
            {"output_schema": {"type": "object", "properties": {}}}, {}, ctx
        )


async def test_model_runner_needs_a_schema(ctx):
    client = FakeAnthropic()
    with pytest.raises(RunnerError, match="output_schema"):
        await ModelRunner(client_factory=lambda: client).run({}, {}, ctx)


async def test_model_runner_applies_a_postprocess_hook(ctx):
    client = FakeAnthropic(text=json.dumps({"mapping": [{"target_field": "sku",
                                                         "source_column": 0}]}))
    runner = ModelRunner(client_factory=lambda: client)
    result = await runner.run(
        {
            "output_schema": {"type": "object", "properties": {"mapping": {"type": "array"}}},
            "postprocess": "tables:apply_column_mapping",
        },
        {
            "typed_grid": [["A-1"], ["A-2"]],
            "columns": [{"name": "SKU", "type": "string"}],
            "target_schema": {"properties": {"sku": {"type": "string"}}},
        },
        ctx,
    )
    assert result.output == {"rows": [{"sku": "A-1"}, {"sku": "A-2"}]}


async def test_unknown_postprocess_ref_is_refused(ctx):
    client = FakeAnthropic(text="{}")
    with pytest.raises(RunnerError, match="unknown postprocess ref"):
        await ModelRunner(client_factory=lambda: client).run(
            {"output_schema": {"type": "object", "properties": {}},
             "postprocess": "nope:missing"},
            {},
            ctx,
        )


async def test_documents_are_attached_rather_than_passed_as_paths(ctx, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    client = FakeAnthropic(text="{}")
    await ModelRunner(client_factory=lambda: client).run(
        {"output_schema": {"type": "object", "properties": {}},
         "attach_documents": ["pdf_path"]},
        {"pdf_path": str(pdf), "hint": "invoice"},
        ctx,
    )

    content = client.messages.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"]["media_type"] == "application/pdf"
    # The path itself is gone from the text payload; the hint remains.
    assert str(pdf) not in content[1]["text"]
    assert "invoice" in content[1]["text"]


def test_strictify_closes_objects_and_fills_required():
    strict = strictify(
        {
            "type": "object",
            "properties": {
                "rows": {"type": "array", "items": {"type": "object",
                                                    "properties": {"a": {"type": "string"}}}}
            },
        }
    )
    assert strict["additionalProperties"] is False
    assert strict["required"] == ["rows"]
    assert strict["properties"]["rows"]["items"]["additionalProperties"] is False
    assert strict["properties"]["rows"]["items"]["required"] == ["a"]
