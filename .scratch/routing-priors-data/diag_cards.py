import sys
sys.path.insert(0, r"C:\Users\user\stealth-lab\backend")
sys.path.insert(0, r"C:\Users\user\stealth-lab\backend\tests")
import numpy as np
from test_routing_priors_offline import _fit_world, T0
from app.routing import fit, cards as cl
from app.routing.config import RoutingDefaults
from app.routing.predict import Globals, unit_terms

conf = RoutingDefaults(draws=64, nightly_warmup=200, nightly_chains=2, nightly_samples=150, embedding_dims=0,
                       latent_dims=1, nuts_max_latents=10 ** 9, gh_eps_nodes=12)
cards, theta, obs, meta = _fit_world()
held = sorted(cards)[-2:]
train = [o for o in obs if o["model_key"] not in held]
data, info = fit.build_joint_data(train, meta, {}, {}, conf, cards=cards)
samples, _, diag = fit.run_joint(data, conf, seed=1)
print(diag)
names = info["cov_names"]
for n, v, s in zip(names, samples["beta"].mean(0), samples["beta"].std(0)):
    print(f"{n:28s} {v:+.2f} +- {s:.2f}")
print("fam_theta", samples["fam_theta"].mean(0).round(2), "families", info["families"])
g = Globals(1, fit.global_arrays(samples, info), {**fit.public_meta(info), "tokens": {}})
loc = g.arrays["theta_last"].mean(axis=1)
truth_loc = np.mean([theta[m] for m in cards if m not in held])
fitted = dict(zip(g.models, (g.arrays["theta_last"] - loc[:, None]).mean(0)))
for m in g.models:
    print("fit", m, round(fitted[m], 2), "truth", round(theta[m] - truth_loc, 2))
base, _ = unit_terms(g, [(m, "mini") for m in held], {}, T0, np.random.default_rng(0), cards=cards)
for i, m in enumerate(held):
    print("held", m, cards[m].family, "pred", round(float((base[:, i] - loc).mean()), 2),
          "truth", round(theta[m] - truth_loc, 2), "x", np.round(cl.Design.from_meta(info).row(cards[m]), 2))
