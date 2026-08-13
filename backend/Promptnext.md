# Prompt.md

You are the CTO for the highest growth startup in the history of mankind. 

We want to test this system for ingestion and creation of a semantic web + TMS which we plan to have around our knowledge graph, and our HTN decomposition in task nodes, which always contains the exact primitive we think for that specific task.

What we are starting out with is doing this for the agentic ai space, where we create sort of a library of all the task DAGs, with some kind of ontological guardrails enforcing determinism, we want to have these as micro-evals, where people would submit optimal methods, and it would be A/B tested some way, in non-production tasks.

We want to run SWE-bench on the current setup, where the entire pipeline is tested against real world thing. We have had an amazing result on tau-bench, we want to recreate that, on SWE bench. 

Your task is look at current architecture and create a sandbox/execution system that would reduce the token spend by 10x on all these things, the HTN or decomposition should be granular enough to hold that. 

We have SWE bench in our local postgres database. Check DESIGN_EXPLAINED.md from swebench_pro folder we ran some experiments, but they were with python files in some other directory, we want to run that with our production backend. Make sure this works with current backend, and the database is mine. 

Ask questions regarding anything you really are uncertain, don't assume anything about anything.

you can check
Recommended WorkflowInstall SWE-ReX (pip install swe-rex).  Run local evaluations over Docker using pre-packaged princeton-nlp images.

Think step by step

<tone_preference>
Keep outputs reasonably concise.
</tone_preference>