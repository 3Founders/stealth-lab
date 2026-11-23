# Launch runbook: B2 (US East) + Cloudflare, for India and the US

Follow top to bottom. Every row of the latency table has a step here; nothing is left as "consider".

**The shape of the decision**, so the instructions make sense:

- Your backend is in **Singapore** (Railway, already deployed) — best worst-case for both markets (88 ms India / 209 ms US).
- **B2 has no region near India or Singapore.** Nothing east of Amsterdam. So B2 cannot fix India latency.
- Therefore: **B2 stores the bytes; Cloudflare is what makes them fast.** Every instruction below serves that split.

**Region: US East (Reston, Virginia) — `us-east-005`.** ⚠️ **You cannot change your B2 region after signup.** The
account is locked to it forever; changing means a new account. Decide before you create it.

| Path | India | US | What makes it fast | Step |
|---|---|---|---|---|
| Website (Vercel) | edge | edge | already correct | §6 |
| Thumbnails | ~10–40 ms | ~5–20 ms | Cloudflare cache | §3, §4 |
| API (Railway Singapore) | 88 ms | 209 ms | inherent to one region | §7 |
| Paid download | ~250–400 ms TTFB | ~100–150 ms | Argo + small files | §5, §8 |

---

## §1. Backblaze account

1. [backblaze.com](https://www.backblaze.com/sign-up/cloud-storage) → sign up.
2. **When asked for a region, choose US East.** This is permanent.
3. No card needed; 10 GB free; no minimum storage duration.

## §2. Bucket + key

**Bucket:** B2 console → **Buckets → Create a Bucket**

| Field | Value |
|---|---|
| Bucket Unique Name | `kell-artifacts` (if taken: `kell-artifacts-prod`) |
| Files in Bucket are | **Private** ← paid outputs are signed, never public |
| Default Encryption | Enabled |
| Object Lock | Disabled |

Open the bucket and copy the **Endpoint** — expect `s3.us-east-005.backblazeb2.com`.

**Key:** **Application Keys → Add a New Application Key**

| Field | Value |
|---|---|
| Name | `kell-mcp-objects` |
| Allow access to Bucket(s) | `kell-artifacts` only — **not "All"** |
| Type of Access | **Read and Write** |
| File name prefix | leave empty |

Copy both values now — the `applicationKey` is shown **once**:

| Shown as | Goes to |
|---|---|
| keyID | `OBJECT_STORAGE_ACCESS_KEY_ID` |
| applicationKey | `OBJECT_STORAGE_SECRET_ACCESS_KEY` |

## §3. Cloudflare: the public domain for artefacts

1. [dash.cloudflare.com](https://dash.cloudflare.com) → your domain → confirm **Active**.
2. **DNS → Records → Add record**:

| Field | Value |
|---|---|
| Type | `CNAME` |
| Name | `files` |
| Target | `s3.us-east-005.backblazeb2.com` |
| Proxy status | **Proxied (orange cloud)** ← required |
| TTL | Auto |

Result: `https://files.yourdomain.com` serves the bucket. Thumbnails become public through this; paid files stay gated by signature.

## §4. Cloudflare: cache rules (this is where India gets fast)

**Caching → Cache Rules → Create rule — rule 1, "thumbs"**

| Field | Value |
|---|---|
| When | `starts_with(http.request.uri.path, "/thumbs/")` |
| Cache eligibility | **Eligible for cache** |
| Edge TTL | **Ignore cache-control, use 1 year** |
| Browser TTL | Respect origin |

Thumbnail keys are content-addressed, so a year is correct — a changed image gets a new key.
This is what serves India from ~9 Indian PoPs (Mumbai, Delhi, Chennai, Bangalore, Hyderabad, Kolkata, Kochi, Kannur…) and the US from ~54 PoPs.

**Caching → Cache Rules → Create rule — rule 2, "never-cache-signed"**

| Field | Value |
|---|---|
| When | `starts_with(http.request.uri.path, "/outputs/")` |
| Then | **Bypass cache** |

A signed URL is unique per buyer and lives 300 s. Caching it would leak a paid file. Bypass makes that explicit.

## §5. Cloudflare: Argo Smart Routing (the paid one)

**Network → Argo Smart Routing → Enable.** ~$5/month + $0.10/GB.

This is the only Cloudflare product that helps the **uncacheable** path — the paid download. It moves India→Virginia traffic onto Cloudflare's private backbone instead of the public internet through the Middle East. Expect a meaningful reduction in TTFB; it cannot beat physics, only routing.

**If you skip this**, paid downloads in India are ~250–400 ms before the first byte. Acceptable, not good. Enable it if India matters at launch; skip it if you'd rather revisit in a month.

## §6. Cloudflare: proxy the API host too

Your site calls `https://mcp.kell.org.in`. Putting Cloudflare in front of that host gives you connection reuse (TLS + TCP established at the nearest edge) for every API call from both markets — which matters more than it sounds when the API itself is 88/209 ms away.

**DNS → Records → Add record:**

| Field | Value |
|---|---|
| Type | `CNAME` |
| Name | `mcp` |
| Target | the Railway hostname for `kell-mcp` |
| Proxy status | **Proxied (orange cloud)** |

Then **SSL/TLS → Overview → Full (strict)**. Railway terminates TLS; Full (strict) works; Flexible would loop.

Keep the Next.js site on Vercel — it is already edge-served in both markets. Don't proxy the site through Cloudflare as well; you'd gain nothing and lose Vercel's own optimisations.

## §7. Railway: the environment variables

Project **kell-mcp** → service **kell-mcp** → **Variables → Raw editor**:

```env
OBJECT_STORAGE_URL=s3://kell-artifacts/outputs
OBJECT_STORAGE_ENDPOINT_URL=https://s3.us-east-005.backblazeb2.com
OBJECT_STORAGE_REGION=us-east-005
OBJECT_STORAGE_ACCESS_KEY_ID=<keyID>
OBJECT_STORAGE_SECRET_ACCESS_KEY=<applicationKey>
OBJECT_STORAGE_PUBLIC_BASE=https://files.yourdomain.com
```

Saving restarts the service automatically. The `/outputs` prefix is part of the key and the cache rules above assume it.

**Do not change the Railway region.** Singapore is deliberate: 88 ms to India, 209 ms to the US — the best worst case of any single region. Mumbai would be 2 ms India / 242 ms US; US East the mirror image.

## §8. Cap the artefact size — the highest-leverage step

A 50 KB file is pure TTFB. An 8 MB file is pure bandwidth, and bandwidth across ~12,000 km is where India feels genuinely slow.

`backend/app/submissions/artifacts.py`:

```python
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024          # now
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024          # suggested for a two-market launch
```

**This will do more for perceived speed in India than any provider or region choice.** Tell me if you want it changed and deployed; it's a one-line edit plus the suite.

## §9. A Way must declare `produces` — or nothing is stored

Storage can be perfect and still store nothing: no published step declares what file it makes. Add `produces` to one step (e.g. `literaturereview.md`) in the ₹1 Way. Without this, §1–§8 are invisible to the user.

---

## Verify — each failure names its own cause

1. **boto3 is in the image.** Railway build log shows two `pip install` lines, the second resolving `boto3`. (Fixed this session: it was missing, and the failure would have looked like a bad key.)
2. **The store is configured.** Railway → Shell:
   `python -c "from app.services.object_storage import get_store; s=get_store(); print(type(s).__name__, s.bucket, s.prefix)"`
   → `S3Store kell-artifacts outputs`. `None` = `OBJECT_STORAGE_URL` unset on that process.
3. **A write lands.** Run a Way that declares `produces`, then B2 console → **Browse Files → `outputs/`** → `ab/cd/<sha>` shards appear.
4. **A download is a redirect, not a stream:**
   `curl -sI ".../v1/goals/runs/<run>/artifacts/report.md" -H "Authorization: Bearer <token>"`
   - **302** with `X-Amz-Signature` → correct
   - **402** → not bought on this account (correct refusal)
   - **200 with bytes** → store could not sign → boto3 or keys wrong (§1–2, §7)
   - **500** → traceback in the Railway log
5. **The signature expires.** Open the same URL after 5 minutes → `Request has expired`.
6. **Thumbnails cache.** `curl -sI https://files.yourdomain.com/thumbs/<sha>.webp` twice → `cf-cache-status: MISS` then `HIT`, `content-type: image/webp`.
7. **Argo is on.** Cloudflare → the zone → **Analytics & Logs** shows Argo traffic.

## What to expect afterwards

| | India | US |
|---|---|---|
| Thumbnails | 10–40 ms | 5–20 ms |
| API | 88 ms | 209 ms |
| Paid download | 250–400 ms TTFB (less with Argo; less again with 2 MB cap) | 100–150 ms |

Signed-URL downloads are the slowest thing in India **by design** — a capability that must not be cacheable cannot be
served from an edge. Everything cacheable is fast in both markets.
