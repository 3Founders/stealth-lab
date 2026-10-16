import re

p = r"C:\Users\user\sl-merge\backend\tests\test_routing_plan_offline.py"
s = open(p, encoding="utf-8").read()
new, n = re.subn(r"<<<<<<< HEAD\n(.*?)=======\n(.*?)>>>>>>> feat/routing-priors\n",
                 lambda m: m.group(1) + "\n\n" + m.group(2), s, flags=re.S)
assert n == 1 and "<<<<<<<" not in new
open(p, "w", encoding="utf-8", newline="").write(new)
print("ok")
