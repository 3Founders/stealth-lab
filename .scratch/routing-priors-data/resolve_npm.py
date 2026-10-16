import re

ROOT = r"C:\Users\user\sl-merge"


def resolve(path, chooser):
    p = f"{ROOT}\\{path}"
    s = open(p, encoding="utf-8").read()
    pat = re.compile(r"<<<<<<< HEAD\n(.*?)=======\n(.*?)>>>>>>> feat/library\n", re.S)
    blocks = pat.findall(s)
    out, i = s, 0
    for ours, theirs in blocks:
        out = out.replace(f"<<<<<<< HEAD\n{ours}=======\n{theirs}>>>>>>> feat/library\n", chooser(i, ours, theirs), 1)
        i += 1
    assert "<<<<<<<" not in out and ">>>>>>>" not in out, path
    open(p, "w", encoding="utf-8", newline="").write(out)
    print(path, len(blocks), "resolved")


def bin_choose(i, ours, theirs):
    if i == 0:                                  # help text: both commands
        return ours + theirs
    # main(): library dispatch first, then survey's positional handling
    lib = theirs.split("  // `hook <event>` takes one positional")[0]
    return lib + ours


def hook_choose(i, ours, theirs):
    # survey's readClaims(cwd, prompt) AND library's `extra`
    return "      repoClaims: readClaims(payload.cwd, payload.prompt), timeoutMs: policy.timeoutMs, extra, fetchImpl,\n"


resolve(r"packaging\npm\bin\stealthlab-mcp.mjs", bin_choose)
resolve(r"packaging\npm\lib\hook.mjs", hook_choose)
