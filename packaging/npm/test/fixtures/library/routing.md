# routing.md fixture: an older and a newer plan for the same route, OBS from two machines
# (a union merge), a rung model with a colon in its name, and lines to skip.
ROUTE|R-b00c1e|goal=L-b00c1e|g=2c1d4a9e-0f3b-4c55-9e1a-7b2f0c6d8e11|fit=3|basis=prior|as_of=2026-09-30|step=*|ladder=gpt-oss-120b::kel > claude-sonnet-5-5::claude-code|whole=p=0.71[0.55,0.84]:$0.0400
OBS|R-b00c1e|gpt-oss-120b|kel|n=3|ok=1|last=2026-10-02
ROUTE|R-b00c1e|goal=L-b00c1e|g=2c1d4a9e-0f3b-4c55-9e1a-7b2f0c6d8e11|fit=4|basis=posterior|as_of=2026-10-06|step=*|ladder=ollama:qwen3-coder::kel:p=0.62[0.48,0.75]:$0.0010 > claude-sonnet-5-5::claude-code:p=0.88[0.80,0.94]:$0.0900|whole=p=0.95[0.90,0.98]:$0.0410
OBS|R-b00c1e|gpt-oss-120b|kel|n=2|ok=2|last=2026-10-06
OBS|R-b00c1e|claude-sonnet-5-5|claude-code|n=1|ok=1|last=2026-10-03
ROUTE|R-a11ce5|goal=-|g=9f9f9f9f-0000-4000-8000-000000000001|fit=-|basis=-|as_of=2026-10-06|step=2|ladder=-|whole=-
OBS|R-a11ce5|gpt-oss-120b|kel|n=4|ok=3|last=2026-10-06
OBS|R-a11ce5|gpt-oss-120b|kel|n=1|ok=2|last=2026-10-06
OBS|R-zzzzzz|gpt-oss-120b|kel|n=1|ok=1|last=2026-10-06
not a routing line
