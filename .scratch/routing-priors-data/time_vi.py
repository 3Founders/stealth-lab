import os
import sys
import time

sys.path.insert(0, r"C:\Users\user\stealth-lab\backend")
sys.path.insert(0, r"C:\Users\user\stealth-lab\backend\scripts")
import routing_priors_eval as ev  # noqa: E402
from app.routing import fit as fitlib  # noqa: E402

D = r"C:\Users\user\stealth-lab\.scratch\routing-priors-data"
conf = ev.cfg(True)
data = ev._restrict(ev.Data(D, ("verified",)), "verified")
meta, parents = ev.goal_inputs(data.items, True)
jd, info = fitlib.build_joint_data(data.obs, meta, parents, {}, conf, cards=data.cards)
print("method", fitlib.choose_method(jd, conf), "latents", fitlib.latent_count(jd, jd["k"]),
      "work", fitlib.gradient_work(jd, conf), flush=True)
t = time.time()
samples, used, diag = fitlib.run_joint(jd, conf, seed=0)
print("fit", used, round(time.time() - t), "s", diag, flush=True)
