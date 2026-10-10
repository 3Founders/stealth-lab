import re

p = r"C:\Users\user\sl-merge\.gitignore"
s = open(p, encoding="utf-8", newline="").read()
new, n = re.subn(r"<<<<<<< HEAD\r?\n(.*?)=======\r?\n(.*?)>>>>>>> feat/local-eval\r?\n",
                 lambda m: m.group(2) + m.group(1), s, flags=re.S)
assert n == 1 and "<<<<<<<" not in new and ">>>>>>>" not in new
open(p, "w", encoding="utf-8", newline="").write(new)
print("ok")
