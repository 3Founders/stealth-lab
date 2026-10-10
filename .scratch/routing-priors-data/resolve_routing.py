import re

ROOT = r"C:\Users\user\sl-merge"
PAT = re.compile(r"<<<<<<< HEAD\n(.*?)=======\n(.*?)>>>>>>> feat/routing-priors\n", re.S)


def resolve(path, chooser):
    p = f"{ROOT}\\{path}"
    s = open(p, encoding="utf-8").read()
    i = [0]

    def sub(m):
        out = chooser(i[0], m.group(1), m.group(2))
        i[0] += 1
        return out
    new = PAT.sub(sub, s)
    assert "<<<<<<<" not in new and ">>>>>>>" not in new, path
    open(p, "w", encoding="utf-8", newline="").write(new)
    print(path, i[0], "resolved")


def server(i, ours, theirs):
    if i == 0:          # signature: both
        return ours + theirs
    if i == 1:          # docstring: both
        return ours + "\n" + theirs
    # body: the library version, with the caller's own model added to the candidates before the plan
    marker = "    final = await _attach_model_plan("
    assert marker in ours
    add = ("    own = _routing_plan.caller_unit(my_model, (_find_ways_client(ctx) or {}).get(\"name\"))\n"
           "    if own is not None:\n"
           "        candidates = [*(candidates or []), own]\n")
    return ours.replace(marker, add + marker, 1)


def service(i, ours, theirs):
    if i == 0:          # evidence: the caller's local counts AND where each model's estimate comes from
        return ("                     \"models\": {unit_id(m, s): _model_basis(g, m, registry, cards) for m, s in usable},\n"
                "                     **({\"list_priced\": list_priced} if list_priced else {}),\n" + ours)
    return ours + "\n\n" + theirs     # helper functions: both


resolve(r"backend\app\mcp_server\server.py", server)
resolve(r"backend\app\routing\service.py", service)
