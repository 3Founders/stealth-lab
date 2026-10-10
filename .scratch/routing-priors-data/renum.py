import re
edits = {
 "backend/db/147_routing_priors.sql": [("-- Migration 136: model-side", "-- Migration 147: model-side"),
     ("-- Next free number: 138 (137 is its project-B twin).", "-- Next free number: 149 (148 is its project-B twin; 140-146 are taken on other branches).")],
 "backend/db/148_routing_priors_search.sql": [("-- Migration 137: project-B twin of 136", "-- Migration 148: project-B twin of 147"),
     ("Next free number: 138.", "Next free number: 149.")],
 "docs/routing_priors.md": [("`136_routing_priors.sql` (control) and `137_routing_priors_search.sql`", "`147_routing_priors.sql` (control) and `148_routing_priors_search.sql`")],
 "backend/app/routing/store.py": [("# Migration 136/137 columns.", "# Migration 147/148 columns."),
     ("public benchmark items (migration 136).", "public benchmark items (migration 147)."),
     ("model-side priors (migration 136)", "model-side priors (migration 147)"),
     ("(empty before migration 136).", "(empty before migration 147).")],
}
for p, reps in edits.items():
    s = open(p, encoding="utf-8").read()
    for a, b in reps:
        assert s.count(a) == 1, (p, a)
        s = s.replace(a, b)
    open(p, "w", encoding="utf-8").write(s)
print("ok")
