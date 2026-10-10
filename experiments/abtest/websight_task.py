"""WebSight page recreation (see page_task.py)."""
from page_task import CHECK, files, grade, prompt, solution_file, sample_suite  # noqa: F401


def sample(n: int, seed: int) -> list[dict]:
    return sample_suite("websight", n, seed)
