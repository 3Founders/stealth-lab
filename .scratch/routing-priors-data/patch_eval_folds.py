p = r'C:\Users\user\stealth-lab\backend\scripts\routing_priors_eval.py'
s = open(p, encoding='utf-8').read()


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


start = s.index('def cold_start(data: Data, held_sets')
end = s.index('# ---------------------------------------------------------------- leave one benchmark out')
s = s[:start] + '''def cold_start_fold(data: Data, held: Sequence[str], conf: RoutingDefaults, k: int, label: str) -> Optional[dict]:
    """One fold: fit without the held-out models (with and without cards), predict them. Returns the
    per-observation predictions so folds can run as separate processes and be merged later."""
    held = set(held)
    train = [o for o in data.obs if o["model_key"] not in held]
    test = [o for o in data.obs if o["model_key"] in held]
    if not test or not train:
        return None
    items = data.items
    fits = {"cards": fit(train, items, cards=data.cards, features=True, conf=conf, seed=k),
            "no_cards": fit(train, items, cards=None, features=True, conf=conf, seed=k)}
    pairs = sorted({(o["model_key"], o["scaffold"]) for o in test})
    test_goals = {o["goal_id"] for o in test}
    test_items = [it for it in items.values() if it["goal_id"] in test_goals]
    preds = {
        "cards": predict(fits["cards"], pairs, test_items, data.cards),
        "no_cards (old prior)": predict(fits["no_cards"], pairs, test_items, None),
        "family_average": predict(fits["no_cards"], pairs, test_items, None,
                                  override=family_override(fits["no_cards"], sorted(held), data)),
    }
    col = {p: j for j, p in enumerate(pairs)}
    row = {it["goal_id"]: i for i, it in enumerate(test_items)}
    rows = {name: [[float(pm[row[o["goal_id"]], col[(o["model_key"], o["scaffold"])]]), float(o["accepted"]),
                    o["model_key"]] for o in test] for name, pm in preds.items()}
    print(f"[{label}] fold {k}: {len(held)} models held out, {len(test)} test obs", flush=True)
    return {"fold": k, "held_out": sorted(held), "train_obs": len(train), "test_obs": len(test),
            "fit_seconds": {n: round(f["seconds"]) for n, f in fits.items()},
            "diagnostics": {n: f["diag"] for n, f in fits.items()}, "rows": rows}


def summarize_cold_start(folds: Sequence[dict]) -> dict:
    per_obs: dict[str, list] = defaultdict(list)
    for f in folds:
        for name, rows in f["rows"].items():
            per_obs[name].extend(rows)
    result = {"folds": [{k: v for k, v in f.items() if k != "rows"} for f in folds], "metrics": {},
              "rate_error": {}}
    arrays = {}
    for name, rows in per_obs.items():
        p = np.array([r[0] for r in rows])
        y = np.array([r[1] for r in rows])
        m = np.array([r[2] for r in rows])
        arrays[name] = (p, y, m)
        result["metrics"][name] = scores(p, y)
        errs = [abs(p[m == mk].mean() - y[m == mk].mean()) for mk in np.unique(m)]
        result["rate_error"][name] = {"mean_abs_error_of_model_resolve_rate": float(np.mean(errs)),
                                      "models": int(len(errs))}
    if "cards" in arrays:
        p_c, y, m = arrays["cards"]
        ll_c = y * np.log(p_c) + (1 - y) * np.log(1 - p_c)
        result["cards_vs"] = {}
        for other in ("no_cards (old prior)", "family_average"):
            p_o = arrays[other][0]
            ll_o = y * np.log(p_o) + (1 - y) * np.log(1 - p_o)
            result["cards_vs"][other] = {"loglik_gain_per_obs": bootstrap_diff(ll_c, ll_o, m)}
    return result


def cold_start(data: Data, held_sets: Sequence[Sequence[str]], conf: RoutingDefaults, label: str) -> dict:
    folds = [f for k, held in enumerate(held_sets) if (f := cold_start_fold(data, held, conf, k, label))]
    return summarize_cold_start(folds)


''' + s[end:]

# eval config: fewer quadrature nodes (the fit is dominated by them; 12 is exact to ~1e-4 here)
rep('''                           nightly_samples=150 if fast else 250, latent_dims=3, embedding_dims=0,
                           nuts_max_latents=10 ** 9)''', '''                           nightly_samples=150 if fast else 250, latent_dims=3, embedding_dims=0,
                           nuts_max_latents=10 ** 9, gh_eps_nodes=12)''')

rep('''    ap.add_argument("--temporal-after", default="2025-10-01")''', '''    ap.add_argument("--temporal-after", default="2025-10-01")
    ap.add_argument("--fold", type=int, help="lomo: run only this fold, write lomo_fold<K>.json (resumable)")
    ap.add_argument("--merge", action="store_true", help="lomo: merge the lomo_fold*.json files into lomo.json")''')

rep('''        if exp == "lomo":
            verified_obs = [o for o in data.obs if o["split"] == "verified"]
            models = sorted({o["model_key"] for o in verified_obs})
            rng = np.random.default_rng(0)
            rng.shuffle(models)
            held = [models[i::args.folds] for i in range(args.folds)]
            sub = _restrict(data, "verified")
            result = cold_start(sub, held, conf, "lomo")''', '''        if exp == "lomo":
            verified_obs = [o for o in data.obs if o["split"] == "verified"]
            models = sorted({o["model_key"] for o in verified_obs})
            rng = np.random.default_rng(0)
            rng.shuffle(models)
            held = [models[i::args.folds] for i in range(args.folds)]
            sub = _restrict(data, "verified")
            if args.fold is not None:
                one = cold_start_fold(sub, held[args.fold], conf, args.fold, "lomo")
                with open(os.path.join(args.out, f"lomo_fold{args.fold}.json"), "w", encoding="utf-8") as fh:
                    json.dump(one, fh, default=str)
                print(f"wrote lomo_fold{args.fold}.json ({round(time.time() - t0)} s)", flush=True)
                continue
            result = cold_start(sub, held, conf, "lomo")''')

rep('''    data = None
    for exp in exps:''', '''    data = None
    if args.merge:
        import glob as _glob

        folds = []
        for path in sorted(_glob.glob(os.path.join(args.out, "lomo_fold*.json"))):
            with open(path, encoding="utf-8") as fh:
                got = json.load(fh)
            if got:
                folds.append(got)
        result = {"experiment": "lomo", "config": conf.__dict__, **summarize_cold_start(folds)}
        with open(os.path.join(args.out, "lomo.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, default=str)
        print(f"merged {len(folds)} folds into lomo.json", flush=True)
        return 0
    for exp in exps:''')
open(p, 'w', encoding='utf-8').write(s)
print('ok')
