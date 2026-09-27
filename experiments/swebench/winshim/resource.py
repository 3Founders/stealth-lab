"""Windows stand-in for the POSIX `resource` module, on the path ONLY on Windows (swe_env.py, grade.py).

swebench 4.x imports `resource` at module level just to raise the local open-file limit before running
Docker containers. With grading.backend = "modal" nothing runs locally, so a no-op is exact. The
harness itself is not modified.
"""
RLIMIT_NOFILE = 7


def getrlimit(_which):
    return (8192, 8192)


def setrlimit(_which, _limits):
    return None
