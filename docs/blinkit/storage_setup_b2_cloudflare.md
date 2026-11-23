# Setting up Backblaze B2 + Cloudflare

Step by step, in the order that leaves nothing broken between steps. Why this provider rather than R2, with the
numbers: `storage_choice.md` §2–§4. What the code expects: `storage_choice.md` §5.

**Time:** about 25 minutes, most of it DNS propagation.
**You need:** a domain you control, and the Railway project (`kell-mcp`, service `kell-mcp`).

Two things to know before you start:

- **The bucket must stay private.** Every paid output is served through a short-lived signed URL. A public bucket
  would make the licence check decorative. Only thumbnails are public, and they go through the CDN's cache rule.
- **The CDN is not optional for B2.** Without it, a buyer outside Virginia downloads at ~810 ms/MB where R2 gives
  ~280 ms (`storage_choice.md` §3). Do steps 1–7 in one sitting.

---

## 1. Backblaze: the bucket

1. Sign up at [backblaze.com](https://www.backblaze.com/sign-up/cloud-storage) — no card required to start; the first
   10 GB is free, and there is no minimum.
2. In the web console, left sidebar: **B2 Cloud Storage → Buckets → Create a Bucket**.
   - **Bucket Unique Name:** `kell-artifacts` (it must be globally unique — if taken, use `kell-artifacts-prod` and
     follow that name through every step below).
   - **Files in Bucket are:** **Private**. ← this is the one that matters.
   - **Default Encryption:** enabled.
   - **Object Lock:** disabled.
3. Open the bucket and note its **Endpoint**. It looks like `s3.us-west-004.backblazeb2.com`. That host and the
   `us-west-004` part are two of your environment variables. Pick the region nearest your Railway deployment
   (Ashburn → `us-west-004` is the usual pairing).

## 2. Backblaze: an application key

1. Left sidebar: **Application Keys → Add a New Application Key**.
2. **Name of Key:** `kell-mcp-objects`.
3. **Allow access to Bucket(s):** `kell-artifacts` only — **not** "All".
4. **Type of Access:** **Read and Write**.
5. Leave the file-name prefix empty; kell writes under `outputs/` itself.
6. **Create New Key.** The next screen shows `keyID` and `applicationKey` **once** — the secret is not shown again.

   | Shown as | Your variable |
   |---|---|
   | keyID | `OBJECT_STORAGE_ACCESS_KEY_ID` |
   | applicationKey | `OBJECT_STORAGE_SECRET_ACCESS_KEY` |
   | Endpoint host | `OBJECT_STORAGE_ENDPOINT_URL` (with `https://`) |
   | Region (`us-west-004`) | `OBJECT_STORAGE_REGION` |

   If you lose the `applicationKey`, make a new key — there is nothing to look up.

## 3. Cloudflare: the DNS record

The CDN is a Cloudflare zone in front of the bucket. Cloudflare's free plan is enough.

1. [dash.cloudflare.com](https://dash.cloudflare.com) → pick the domain kell serves from. If the domain's nameservers
   are not already Cloudflare's, **add the site** first and change them at your registrar — everything below happens
   only after the zone shows **Active**.
2. **DNS → Records → Add record**:
   - **Type:** `CNAME`
   - **Name:** `files` (gives you `files.yourdomain.com`)
   - **Target:** `<the endpoint host from step 1>`, e.g. `s3.us-west-004.backblazeb2.com`
   - **Proxy status:** **Proxied (orange cloud)**. ← required. Grey cloud serves B2 directly: no CDN, no caching,
     and no free egress.
   - **TTL:** Auto.

The record will show an error like *"CNAME target not allowed"* if the zone has a conflicting record on the same name —
delete that first. It is safe to save before the next step; the request just 404s until step 4 lands.

## 4. Cloudflare: cache rules for the public thumbnails

Only two kinds of object are ever public: thumbnails (`thumbs/…`). Everything else needs a signature. Set the zone
to respect that:

1. **Caching → Cache Rules → Create rule**, name it `thumbs`.
   - **When incoming requests match:** *Custom filter expression* —
     `(starts_with(http.request.uri.path, "/thumbs/"))`
   - **Then**, under *Cache eligibility*: **Eligible for cache**.
   - **Edge TTL:** *Ignore cache-control header and use this TTL* → **1 year**.
   - **Browser TTL:** *Respect origin TTL* (the thumbnail keys are content-addressed, so a year is safe and correct —
     a changed artefact gets a new key).
   - Save. Thumbnails are small (measured ~2.4 KB) and now cached at the edge for free.
2. **Caching → Cache Rules → Create rule**, name it `never-cache-signed`.
   - **When:** `(starts_with(http.request.uri.path, "/outputs/"))`
   - **Then:** **Bypass cache**.
   - This is not strictly needed for correctness — a signed URL is scoped to one key and expires — but it stops a
     paid file from ever sitting in an edge cache, and makes that decision explicit rather than accidental.

## 5. Backblaze: the CORS rule (for browser uploads/downloads)

If any browser ever puts an object directly (avatars do this from the browser, artefacts do not), B2 needs CORS:

**B2 console → your bucket → Bucket Settings → CORS Rules → Add**:

```json
[
  {
    "corsRuleName": "kell-origin",
    "allowedOrigins": ["https://yourdomain.com"],
    "allowedOperations": ["b2_download_file_by_name", "b2_upload_file", "b2_upload_part"],
    "allowedHeaders": ["content-type", "content-disposition"],
    "exposeHeaders": ["content-length", "content-type"],
    "maxAgeSeconds": 3600
  }
]
```

Replace `https://yourdomain.com` with the site's real origin. **Not `*`** — same rule as `allowed_origins()` on the
API side: an explicit list, never a wildcard.

## 6. Railway: the environment variables

Railway dashboard → project **kell-mcp** → service **kell-mcp** → **Variables → Raw editor**, and paste:

```env
OBJECT_STORAGE_URL=s3://kell-artifacts/outputs
OBJECT_STORAGE_ENDPOINT_URL=https://s3.us-west-004.backblazeb2.com
OBJECT_STORAGE_REGION=us-west-004
OBJECT_STORAGE_ACCESS_KEY_ID=<keyID from step 2>
OBJECT_STORAGE_SECRET_ACCESS_KEY=<applicationKey from step 2>
OBJECT_STORAGE_PUBLIC_BASE=https://files.yourdomain.com
```

`OBJECT_STORAGE_URL` is `bucket/prefix` — the `outputs` prefix is where kell's objects live and it is part of the
key, not the bucket. Keep the `/outputs` part; the signed URLs and the `never-cache-signed` rule above both assume it.

**Do not paste these into the repo or into `backend/.env` on a shared machine.** Railway's variables are the
deployment; that is where they belong.

## 7. Redeploy — both changes are required

Two things must ship together, and neither is automatic:

```powershell
cd backend
git pull                      # railway.json now installs boto3 (see "Why the first attempt would have failed")
railway up --detach
railway deployment list       # wait for SUCCESS
```

**Why the first attempt would have failed.** `railway.json`'s build command installed only `requirements.txt`, and
`boto3` lives in `requirements-objectstore.txt`. The store imports boto3 lazily, so the backend would have booted
fine, accepted your keys, and then failed at the *first write* with *"boto3 is not installed"* — a message that reads
like a bad key. Both the Nixpacks `buildCommand` and `Dockerfile.mcp-server` now install it.

---

## Verifying it actually works

Work down this list; each step fails loudly and distinctly, so the first failure tells you which box is unchecked.

**1. boto3 is in the image.** In the Railway deploy log, the build shows two `pip install` lines, the second
resolving `boto3`. If not, the `railway.json` change did not deploy.

**2. The backend can reach the bucket.** Railway → service → **Shell** (or locally with the same variables in
`backend/.env`):

```powershell
cd backend
python -c "from app.services.object_storage import get_store; s=get_store(); print(type(s).__name__, s.bucket, s.prefix)"
# expect: S3Store kell-artifacts outputs
```

`None` means `OBJECT_STORAGE_URL` is not set on that process. An exception means the URL scheme or boto3 is wrong.

**3. A write lands.** Upload the sample `literaturereview.md` Way's single step, or any Way that declares
`produces`. Then in the B2 console, bucket **Browse Files → `outputs/`** should show `ab/cd/<sha>` shards. Nothing
there = no step declared `produces` (a known gap — `docs/procedure_outputs.md` §1), not a storage fault.

**4. A download is a redirect, not a stream.** This is the behaviour change from this session:

```powershell
# signed in, with a paid run id:
curl -sI "https://api.yourdomain.com/v1/goals/runs/<run-id>/artifacts/report.md" -H "Authorization: Bearer <token>"
# expect: 302, with a Location: https://s3.us-west-004.backblazeb2.com/kell-artifacts/outputs/...?X-Amz-Signature=...
```

- **302 with a signature** → correct: the licence check passed and the browser fetches from B2 directly.
- **402** → the licence check refused. Correct behaviour, wrong account or unpaid Way.
- **200 with bytes** → the store could not sign, so it fell back to proxying. In production that means boto3 or the
  keys are wrong; check steps 1–2.
- **500** → something else; the Railway log has the traceback.

**5. The signature expires.** The same URL, pasted in a browser after five minutes, should return
`Request has expired`. That proves the presign is a time-boxed capability, not an open door.

**6. Thumbnails are public and cached.**

```powershell
curl -sI "https://files.yourdomain.com/thumbs/<sha>.webp"
# expect: 200, content-type: image/webp, cf-cache-status: HIT on the second request
```

`cf-cache-status: MISS` on the first request and `HIT` on the second is the cache rule working. A `404` means the
bucket or the `thumbs/` prefix is wrong; a `403` means the CNAME is not proxied (grey cloud) or the bucket is private
and the rule was never applied.

**7. Egress is free.** Cloudflare → your zone → **Analytics & Logs → Traffic** should count the thumbnail requests.
In the B2 console, **Caps & Alerts → Bandwidth** should stay flat as thumbnails are served — traffic through
Cloudflare does not bill, which is the whole reason for this configuration.

---

## First-run cost sanity check

Run one Way, then in the B2 console check **Buckets → kell-artifacts → Files** for the size, and under **Reports**
for transaction counts. At kell's size a run is a handful of objects of tens of kilobytes: B2 bills storage at
$6.95/TB-month and **nothing** for transactions, so the expected line for weeks of light use is under a dollar. If
you see a transaction charge, the key is not scoped to the bucket or a retry loop is re-uploading — objects are
content-addressed, so a retry of identical bytes is a no-op `head_object`, not a write.

## If you would rather stay on R2

Everything above except steps 1–2 and the DNS target applies unchanged; the only differences are the endpoint, the
region (`auto`), and that the custom domain is connected under **R2 → your bucket → Settings → Public access → Connect
Domain** rather than as a CNAME. The code, the presigning, the `OBJECT_STORAGE_PUBLIC_BASE` and the verification list
are identical — that is the point of `storage_choice.md` §5.
