# Storage: which provider, and what it costs

**Status: decided, not yet switched.** The code is provider-agnostic as of 2026-11-23 — presigning, public URLs, key
prefixes and the thumbnail base all read from configuration. Choosing the provider is one block of environment
variables; no code changes with it.

Numbers here were read from vendor pricing pages and one independent benchmark on 2026-11-23. Where a figure is a
vendor claim rather than a measurement, it says so.

---

## 1. What kell actually stores

Before comparing providers, the shape of the workload, because it decides which line of the bill matters:

| What | Size | Written | Read |
|---|---|---|---|
| A Way run's output files (`.md`, `.tex`, `.pdf`) | 1–50 KB typical, 8 MB cap | once, at the end of a step | by the buyer, rarely, and by a ZIP download |
| Output thumbnails (first page as WebP) | ~2.4 KB (measured) | once per artefact | **often, by anyone, on a listing page** |
| Contributor avatars | < 100 KB | rarely | often |
| Large raw ingestion payloads | up to 1 MB inline cap | once | by workers, during ingestion |

So: **many small objects, written once, re-read a modest number of times, with one read-heavy public surface
(thumbnails).** Three consequences:

- **Request cost matters more than storage cost.** At tens of kilobytes per object, a terabyte is ~20 million
  objects. Storage at $0.015/GB is a few dollars; writes at $4.50 per million are not.
- **Egress is dominated by thumbnails**, which are public and cached. A CDN collapses that to almost nothing.
- **Only the backend's own path is latency-critical today** (a run writing files), because buyer-facing reads are
  already meant to sit behind Cloudflare (`docs/procedure_outputs.md` §2).

---

## 2. Cost

Vendor pricing pages, 2026-11-23. No annual-commitment or volume discounts assumed.

| | Storage / GB-month | Class A (writes, lists) | Class B (reads) | Egress |
|---|---|---|---|---|
| **Cloudflare R2 Standard** | $0.015 | $4.50 / M | $0.36 / M | **free** |
| Cloudflare R2 Infrequent Access | $0.010 | $9.00 / M | $0.90 / M | free (retrieval $0.01/GB) |
| **Backblaze B2** | **$0.00695** (= $6.95/TB) | **free** | **free** | free to 3× stored, then $0.01/GB; **unlimited free through Cloudflare, Fastly, bunny.net, CacheFly, CoreWeave, Equinix Metal, Vultr, phoenixNAP** |
| AWS S3 Standard | $0.023 | $5.00 / M | $0.40 / M | ~$0.09/GB — dominating |
| Wasabi | $0.00799 | free | free | free — **but 1 TB minimum, 90-day minimum object duration** |
| Tigris | $0.020 | $5.00 / M | $0.50 / M | free |

Both R2 and B2 include free tiers; R2's is 10 GB Standard-only. Neither matters at kell's size yet.

**A reference workload — 1 TB stored, 5 TB served per month** (from a 2026 comparison article, cross-checked against
both vendor pages):

| Setup | Monthly |
|---|---|
| S3 Standard | $464 |
| Backblaze B2 alone | $27 |
| **Backblaze B2 behind Cloudflare** | **$7** |
| Cloudflare R2 | $15 |
| Tigris | $20 |

**Ruled out, with the reason:**

- **Wasabi** — the 1 TB minimum is fatal at kell's size (you would pay for a terabyte to store 50 GB), and a 90-day
  minimum object duration is exactly wrong for artefacts that get regenerated.
- **Tigris** — pricier than R2 on every line; only wins if multi-region locality is the requirement.
- **B2 Overdrive** ($15/TB, unlimited egress anywhere) — for throughput-heavy workloads. kell is not one.
- **Data Moose / HiPPIUS / Elestio** — single-digit-person vendors. A run's deliverables living there is an
  availability risk with no support story.
- **S3** — only the egress line, and it is ten times everyone else's.

---

## 3. Latency — the part that reverses the cost story

Independent benchmark (storage-bench.vercel.app, 2026-11-23): 10 providers measured from 6 locations, 1 MB object,
every bucket in the eastern US except where noted. These are measurements, not vendor claims.

**Time to first byte, ms (lower is better):**

| Provider | Ashburn (Railway) | Florida home | San Francisco | Seattle | Amsterdam (Railway) | Lille |
|---|---|---|---|---|---|---|
| Tigris | **20** | **114** | **110** | **139** | **26** | **40** |
| **Backblaze B2** | **21** | 116 | 220 | 209 | 294 | 277 |
| Railway Buckets | 26 | 120 | 190 | 224 | 220 | 216 |
| AWS S3 | 66 | 159 | 231 | 227 | 321 | 311 |
| **Cloudflare R2** | **101** | 155 | 208 | 190 | **200** | 219 |

**Download 1 MB, ms:**

| Provider | Ashburn | Florida | San Francisco | Seattle | Amsterdam | Lille |
|---|---|---|---|---|---|---|
| **Backblaze B2** | **38** | 318 | 622 | 600 | 863 | **810** |
| **Cloudflare R2** | 169 | **220** | **273** | **239** | **275** | **280** |

Two facts, and they point in opposite directions:

1. **From the backend's own region, B2 is ~5× faster.** 21 ms vs 101 ms TTFB, 38 ms vs 169 ms for a megabyte.
   Railway runs in Ashburn; B2 sits there. A run that writes five files saves ~400 ms of TTFB per run on B2.
2. **To a distant client with no CDN, B2 is ~3× worse.** 810 ms vs 280 ms for a megabyte to Lille, 863 vs 275 to
   Amsterdam. R2 is *inside* Cloudflare's edge network, so a buyer pulls from a nearby PoP by default. B2 pulls from
   one bucket in Virginia.

**Why R2's TTFB is 101 ms from Ashburn** is not explained by the benchmark; it is consistently ~2× every other
provider from its own region. What follows from it does not depend on the explanation: the backend path is
measurably slower, and the edge path is measurably better.

**What kell's size does to these numbers.** Output files are 1–50 KB, not 1 MB. At that size **TTFB dominates and the
1 MB column overstates the gap** — the honest per-file figure is the TTFB table, roughly 80 ms difference from the
backend, and single-digit milliseconds from an edge once a CDN is in front.

---

## 4. The decision

| | R2 (current) | B2 + Cloudflare CDN |
|---|---|---|
| Backend write/read TTFB (Ashburn) | 101 ms | **21 ms** |
| Buyer download, far from origin | 280 ms (native edge) | CDN-cached, ~edge |
| Buyer download, **no CDN configured** | fine | **810 ms — unacceptable** |
| Storage, 1 TB | $15 | **$6.95** |
| Writes, 1 M | $4.50 | **free** |
| Egress | free | free to 3× stored; unlimited **only** via a partner CDN |
| Moving parts | CDN optional | **CDN required** |

**Recommendation: switch to B2 only in the same change that puts Cloudflare in front of the bucket.** Half of B2's
cost advantage (`$7` instead of `$27` in §2) *is* the CDN, and its latency to a distant buyer *requires* the CDN.
Switching the bucket without the CDN buys $4/TB and 80 ms of backend latency while making every download three times
slower for anyone outside Virginia — a bad trade at any volume.

If the CDN is not going to be set up now, **stay on R2.** It is edge-adjacent out of the box, its egress is already
free, and its 101 ms TTFB costs a few hundred milliseconds inside a run that takes seconds.

Either way, **the Cloudflare custom domain is the piece worth doing first**, because `docs/procedure_outputs.md` §2
already requires it for thumbnails, and it is the same few clicks in front of either bucket.

---

## 5. What is now provider-agnostic

Everything except the choice. Nothing in the code names R2, B2 or S3.

**`services/object_storage.py`**

| | Purpose |
|---|---|
| `ObjectStore.presign_get(locator, ttl_seconds=300)` | A short-lived GET URL, so a paid file is not proxied through the API (`procedure_outputs.md` §4). `S3Store` uses `generate_presigned_url`; `LocalFileStore` and `MemoryStore` return `None`, meaning "this backend cannot presign — proxy it". |
| `ObjectStore.public_url(locator, prefix="")` | The CDN or public-bucket URL for an object, composed from config at read time. `None` when no public base is configured. |
| `OBJECT_STORAGE_PUBLIC_BASE` | The base URL the public side of the store is served from: an R2 custom domain, an R2 dev URL, a Cloudflare-fronted B2 domain, or a bucket's own public host. |

**`submissions/api.py`** — downloads and the ZIP redirect to a presigned URL when the store can sign, and stream
through the API only when it cannot (`file://` in dev). The licence check happens *before* the signature is issued;
a signed URL is a capability with a 300-second life, not an ACL.

**`services/output_thumbnails.py`** — `thumbnail_url(sha)` composes `OBJECT_STORAGE_PUBLIC_BASE` + `thumbs/<sha>.webp`.
The key is content-addressed, so the URL is cacheable forever and never regenerates.

**Nothing stores a URL.** Key prefixes (`thumbs/`, `avatars/`, the content-addressed `ab/cd/<sha>`) are constants, so
a provider change rewrites no rows.

---

## 6. Switching on — the one block left

R2 and B2 differ in three values. Everything else is identical.

### Cloudflare R2 (current default)

```env
OBJECT_STORAGE_URL=s3://kell-artifacts/outputs
OBJECT_STORAGE_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
OBJECT_STORAGE_REGION=auto
OBJECT_STORAGE_ACCESS_KEY_ID=...
OBJECT_STORAGE_SECRET_ACCESS_KEY=...
# public side: a custom domain in front of the bucket (Cloudflare dashboard -> R2 -> Connect domain), or the
# r2.dev development URL. Custom domain is required for caching that survives.
OBJECT_STORAGE_PUBLIC_BASE=https://files.kell.example
```

### Backblaze B2 + Cloudflare CDN

```env
OBJECT_STORAGE_URL=s3://kell-artifacts/outputs
OBJECT_STORAGE_ENDPOINT_URL=https://s3.us-west-004.backblazeb2.com
OBJECT_STORAGE_REGION=us-west-004
OBJECT_STORAGE_ACCESS_KEY_ID=<keyID>
OBJECT_STORAGE_SECRET_ACCESS_KEY=<applicationKey>
# public side: a Cloudflare domain CNAME'd to f004.backblazeb2.com, proxied (orange cloud). Caching on,
# and a cache rule that honours the immutable Cache-Control on thumbs/.
OBJECT_STORAGE_PUBLIC_BASE=https://files.kell.example
```

The bucket must be **private** for presigning to be meaningful; `thumbs/` is exposed through the CDN's cache rule,
not through a public bucket. If the bucket itself is public, every paid output is fetchable without a licence.

### Choosing, in one line each

- **CDN now?** → B2. Cheaper, free writes, and the CDN supplies the edge R2 would have given you.
- **Not yet?** → R2. Edge-adjacent already, free egress already, and 101 ms costs less than an uncached B2 download.

**Step-by-step setup: `launch_b2_cloudflare_india_us.md`** (B2 US East + Cloudflare, India and the US).

> **Decision updated 2026-11-23 (India + US at launch, single region).** The tables above were measured from US and
> EU vantage points and do not include India. Two facts overrode them:
> - B2 has **no region east of Amsterdam** — US East, US West, EU Central, Canada East only — so B2 cannot lower
>   India latency, and the region is **permanent at signup**.
> - The backend runs in **Singapore** (best single-region worst case: 88 ms India / 209 ms US). To it, every B2 region
>   is transcontinental.
>
> The shape that follows: **B2 stores bytes in US East; Cloudflare is what makes them fast.** Cloudflare's 9 Indian and
> 54 North American PoPs serve every cacheable byte; paid downloads (signed, uncacheable by design) are the one slow
> path and are mitigated by Argo and by keeping artefacts small. The provider choice below is superseded for this
> launch by that runbook; the provider-agnostic code (§5) is unchanged and still the reason either works.
