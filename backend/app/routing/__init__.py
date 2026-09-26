"""Per-Goal model recommender (docs/model_routing_plan.md).

Recommends, for a Goal (and the Procedure being followed), the LADDER of model units
("try A; if the check rejects it, B; ...") with the best expected utility

    U = V * P(accepted & correct) - L * P(accepted & wrong) - E[total cost]

subject to a reliability chance constraint on the posterior. It never changes
retrieval: which Procedure is correct is decided elsewhere; this decides which model
to run it with.

    model.py       the hierarchical Bayesian IRT likelihood + priors (NumPyro; worker only)
    fit.py         nightly joint refit and per-Goal local refits (NumPyro; worker only)
    predict.py     numpy: posterior draws -> success probabilities per unit
    ladder.py      numpy: exact ladder utilities, chance constraint, Thompson sampling,
                   belief updates from earlier attempts on the same instance
    costs.py       token model x live price table
    store.py       database access (control DB + project B logs)
    service.py     recommend() / record_observation() -- what the MCP tool and
                   report_execution call
    jobs.py        worker job handlers

Deciding only READS stored draws (milliseconds, numpy); updating happens in the
worker after a run finishes (seconds, NumPyro). The API process never imports JAX.
"""
