"""A person submits a goal and the Way to do it, step by step (docs/goal_submission.md, db/204).

    intake.parse(payload)            -> Submission (validated; the mode worked out from the steps)
    store.submit(pool, owner, sub)   -> each step stored as its own goal + one-step Way, quarantined and private
    review.review_submission(pool, id)  -> checks + the AI reviewer, per step; activation or a rejection email
    runs.start / runs.finish_human_step -> run an approved Way, pausing at human steps
"""
