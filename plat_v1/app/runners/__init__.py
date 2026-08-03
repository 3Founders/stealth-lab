from app.runners.base import RunContext, Runner, RunnerError, RunnerResult
from app.runners.command import CommandRunner
from app.runners.model import ModelRunner
from app.runners.python_fn import PythonRunner
from app.runners.registry import REGISTRY


def default_runners() -> dict[str, Runner]:
    """The dispatch table the executor uses. Keyed by `implementations.kind`."""
    return {
        "command": CommandRunner(),
        "python": PythonRunner(),
        "model": ModelRunner(),
    }


__all__ = [
    "CommandRunner",
    "ModelRunner",
    "PythonRunner",
    "REGISTRY",
    "RunContext",
    "Runner",
    "RunnerError",
    "RunnerResult",
    "default_runners",
]
