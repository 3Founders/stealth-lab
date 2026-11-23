# Where output artefacts live
**Reports, decks and thumbnails do not belong in Neon.** 2026-11-22. Companion to `docs/procedure_billing.md` and
`docs/thumbnails.md` (the listing picture, sample runs and PII). Written for the agent wiring the storage; the
frontend half (`Output` and `More like this` on the Way page) is already in place.

> **Pricing and execution decisions now live in `docs/blinkit/`** (2026-11-23). This document remains the authority on
> *where artefacts live*; for what a step may produce, what it reads, and how that is priced, read
> `docs/blinkit/execution.md` and `docs/blinkit/pricing_discussions.md` first.

---

## 0. The rule

**Neon stores the locator, the hash and the size. The bytes live in object storage.**

This is not a preference — it is already the documented design of `backend/app/services/object_storage.py`:

> *"The relational databases (control DB and shards) keep only a small LOCATOR + sha256 + size; the bytes live in
> object storage. This keeps `ingestion_jobs.payload` and every shard small, makes duplicate content free (keys
> are content-addressed), and lets any worker on any provider fetch the same bytes."*

**Do not build a second storage path.** `object_storage.py` already gives you content-addressed keys,
sha256 verification, and a three-way failure model (`ObjectStoreUnavailable` retryable, `ObjectStoreCorrupt`
permanent, `PayloadTooLarge` fail-closed). Reuse it.

---

## 1. The measurement that decides the answer

A 12-page report is ~1–2 MB. A 30-slide deck is ~5–15 MB. A thumbnail is ~40 KB.

Scale that against Neon: it is Postgres, billed on compute time and storage, and **every byte you put in a
`BYTEA` column is read back through the same connection that serves the router's queries**. A 10 MB deck pulled
through the request path is a slow page and a hot database. Fifty of them is a genuinely bad database.

So: **bytes go to a bucket; Neon keeps a pointer.** The pointer is one row of about 300 bytes.

---

## 2. The options, ranked for this repo

### Option A — Cloudflare R2 via the existing `S3Store` ✅ **recommended**

`object_storage.py` already speaks S3-compatible storage with a custom endpoint, and names R2 explicitly
(`OBJECT_STORAGE_ENDPOINT_URL   custom S3 endpoint (R2/MinIO/OCI)`). Switching on is configuration:

```
OBJECT_STORAGE_URL=s3://kell-artifacts/outputs
OBJECT_STORAGE_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
OBJECT_STORAGE_ACCESS_KEY_ID=…
OBJECT_STORAGE_SECRET_ACCESS_KEY=…
OBJECT_STORAGE_REGION=auto
```

Why it wins here:

- **Zero egress fees.** The single number that decides this. Outputs are *made to be viewed* — a public page, a
  thumbnail grid, people opening PDFs repeatedly. On S3 that traffic is billed; on R2 it is not.
- **Public bucket for thumbnails.** A thumbnail on a listing page must be a plain URL any browser can load
  without a token. R2 gives a public bucket URL or a custom domain in front of it.
- **One client.** Same `boto3` S3 path already written and tested.
- **Content-addressed keys mean dedupe is automatic** — two Ways producing the same deck store it once.

### Option B — AWS S3

Workable, same code path, but **egress is billed per GB** and this is a read-heavy public surface. Choose it only
if you already have AWS commitments. The module's own docstring warns about a real trap here: on an AWS machine
`AWS_*` env vars may belong to the machine, not the bucket — which is why the module prefers
`OBJECT_STORAGE_ACCESS_KEY_ID` over `AWS_ACCESS_KEY_ID`. Keep that.

### Option C — Supabase Storage

Tempting because Supabase is already the identity provider (`supabase_project_url` in `config.py`). But storage
would be a **second client and a second auth model** bolted beside `object_storage.py`, and the repo already
documents that the two Supabase token audiences are deliberately split. **Only if you want to consolidate on
Supabase entirely** — then storage is the least of the migration.

### Option D — keep it in Postgres (`BYTEA` / `pg_largeobject`)

**No.** It is the option that looks simplest today and is the reason this document exists. It makes the database
the CDN, makes every backup 10× larger, and makes a 20 MB deck compete with router queries for connections.

### Option E — a CDN in front of whichever bucket (do it regardless)

A bucket serves bytes; it does not serve them *fast from Mumbai to a phone*. Cloudflare (free tier) in front of
the bucket, or any CDN with a custom domain, is a config change and not architecture. **Do this at the same
time as the bucket**, not later.

> **Updated 2026-11-23 — the provider question is open again, with numbers.** R2 vs Backblaze B2 costs and
> latency to six locations, and why the CDN is now a precondition for B2 rather than a nicety:
> `docs/blinkit/storage_choice.md`. The code is provider-agnostic; only configuration changes.

---

## 3. What goes where

| Thing | Stored in | Served as |
|---|---|---|
| The output file (PDF/deck) | object storage, content-addressed | a URL from `outputs` table (below) |
| Its thumbnail (first page/slide) | object storage, same bucket, `thumbs/` prefix | **plain public URL**, CDN-cached |
| Its metadata (title, kind, pages, size, sha256) | **Neon**, `procedure_outputs` | read with the Way page |
| "More like this" tiles | Neon rows + thumbnail URLs | the rail at the foot of the page |

### 3.1 The row Neon holds

```sql
CREATE TABLE IF NOT EXISTS procedure_outputs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_row_id  UUID NOT NULL,       -- THE EXACT VERSION, same discipline as a licence
    procedure_version INTEGER NOT NULL,
    title             TEXT,
    kind              TEXT NOT NULL,       -- 'pdf' | 'pptx' | 'docx' | 'png' | …
    object_key        TEXT NOT NULL,       -- the content-addressed key in the bucket; NOT the bytes
    sha256            TEXT NOT NULL,
    size_bytes        BIGINT NOT NULL,
    page_count        INTEGER,
    thumb_key         TEXT,                -- also in the bucket
    visibility        TEXT NOT NULL DEFAULT 'public'
                      CHECK (visibility IN ('public', 'unlisted', 'private')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (procedure_row_id, sha256)      -- one row per distinct artefact per version
);
```

**`object_key`, not `url`.** URLs change (CDN domain, bucket rename, migration); keys do not. The API composes the
URL at read time from configuration, so moving from R2 to S3 is a config change rather than a data migration.

### 3.2 Why thumbnails are public and outputs need not be

Two different jobs:

- **A thumbnail is a picture of value.** It exists to make a listing scannable. Requiring a token to see it
  defeats the purpose — the rail would be 8 grey boxes. **Public bucket, random-enough key, no token.**
- **The output is the value.** If the Way is paid, the PDF is the thing people pay for. So: **thumbnail public,
  full output gated** (see §4). Publishing the full artefact for free would make the licence pointless.

---

## 4. Access rules

| Artefact | Who can fetch |
|---|---|
| Thumbnail | anyone — public bucket, `Cache-Control` long, key includes sha256 so it never goes stale |
| Output of a **free** Way | anyone |
| Output of a **paid** Way | a holder of a `paid` licence for that exact version (`procedure_billing.md` §3) |

For gated bytes, do **not** proxy the file through FastAPI — that puts a 10 MB stream on the API. Issue a
**short-lived signed URL** from the backend after the licence check.

> **Built 2026-11-23.** `ObjectStore.presign_get(locator, ttl_seconds=300, filename=...)` exists on every backend.
> `S3Store` signs with `generate_presigned_url` (identical on R2, B2, MinIO and AWS); `file://` and `memory://`
> return `None`, and the route then proxies — so a dev environment needs no signer and a broken signer degrades
> instead of failing. `download_artifact` redirects (302) once the licence check passes; the ZIP still streams,
> because signing N parts leaves the browser fetching N objects. Provider choice, with the latency and cost tables:
> `docs/blinkit/storage_choice.md`.

---

## 5. Generating the thumbnail

Do not ask a model. A deterministic renderer is cheap, reproducible, and cannot hallucinate a cover.

### 5.1 What is actually installed (measured 2026-11-22)

| Tool | Status | Covers |
|---|---|---|
| **PyMuPDF (`fitz`)** | ✅ installed | PDF → page image. **This is the primary path.** |
| `pdf2image` + `pdftoppm` | ✅ installed (via MiKTeX) | Alternative PDF rasteriser |
| **Pillow (`PIL`)** | ✅ installed | crop, resize, WebP encode |
| `python-docx` | ✅ installed | reads `.docx` text/structure |
| **LibreOffice (`soffice`)** | ❌ **not installed** | the usual way to render PPTX/DOCX |
| `python-pptx` | ❌ not installed | PPTX structure |

**Measured end-to-end with only what is installed:** a 2-page PDF rendered page 1 at 90 dpi and encoded to
WebP in **2.4 KB**. So the PDF path needs no new dependency.

```python
import fitz, io                    # PyMuPDF
from PIL import Image

def pdf_thumb(pdf: bytes, dpi: int = 90, max_w: int = 640) -> tuple[bytes, int]:
    doc = fitz.open(stream=pdf, filetype="pdf")
    pages = doc.page_count
    pix = doc[0].get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if img.width > max_w:
        img = img.resize((max_w, round(img.height * max_w / img.width)), Image.LANCZOS)
    buf = io.BytesIO(); img.save(buf, "WEBP", quality=82)
    return buf.getvalue(), pages
```

### 5.2 PPTX without LibreOffice — a measured constraint, not a guess

A `.pptx` is a zip of XML: `ppt/slides/slide1.xml` holds the text runs. Reading it with the **standard library
alone** (`zipfile` + `ElementTree`) works — that is verified. What it does **not** give you is *layout*: positions,
theme, fonts, images and shapes live in separate parts.

So there are three honest options, in order of fidelity:

| Option | Fidelity | Cost |
|---|---|---|
| **A. Render HTML ourselves, screenshot with headless Chrome** | good enough | Chrome is already a dependency (used for page screenshots). We build a simple slide → HTML view. |
| **B. Parse `slide1.xml` for text, draw a text-only card** | low, but truthful | stdlib only. Shows the actual title/bullets on a branded tile. |
| **C. Install LibreOffice** in the worker image | highest | one dependency; must exist in prod, not just dev |

**Recommendation: A with B as fallback.** We generate the deck ourselves, so we can render it ourselves — the
same content, the same shapes, in HTML, then screenshot at 1280×720. That avoids a 500 MB office suite and avoids
the "install it in prod and discover it is missing" failure.

**Never show a broken image.** The frontend already falls back to a labelled tile: a tidy grid beats broken icons.

### 5.3 The image itself

- **Format: WebP**, quality ~82. Measured: a page render is ~2–15 KB; an image upload can be ~80 KB.
- **Size:** 640 px wide for the page preview, **1280×720 for a deck tile** (16:9, matching the rail's aspect ratio).
- **Key:** `thumbs/<sha256>.<ext>` — content-addressed, so `Cache-Control: public, max-age=31536000, immutable` is
  safe forever and two Ways producing the same deck share one thumbnail.
- **Fallback order:** already-rendered thumbnail → render from PDF → render from PPTX → labelled tile.


---

## 6. "More like this" — what the rail actually queries

The frontend renders `way.similar[]` with `{id, goal_name, thumb_url, relation, price_paise}`. The backend picks
the set; **the page never guesses why a Way is there.**

```sql
-- Cheapest honest signals first. Each row carries its own reason, which the UI prints as `relation`.
SELECT p.id, p.display_name AS goal_name, o.thumb_key, p.price_paise, p.price_currency, 'same Goal' AS relation
  FROM procedures p JOIN procedure_outputs o ON o.procedure_row_id = p.id
 WHERE p.goal = $1 AND p.id <> $2 AND p.t_invalid IS NULL AND p.verification_state <> 'retired'
UNION ALL
SELECT … 'same publisher' …     -- p.owner_id = the current Way's owner
UNION ALL
SELECT … 'also a PDF' …         -- same output kind, when the others come up short
LIMIT 12;
```

Four rules:

1. **Never rank by popularity.** Same reason the registry does not (`docs/pages.md` #2): a rail sorted by clicks
   is the GPT Store. Rank by relationship, then evidence.
2. **`relation` is displayed.** "Same Goal" is checkable; "recommended" is not.
3. **Only Ways with an output can appear** — a tile with no picture is the thing this rail exists to avoid.
4. **Free and paid appear together, with the price shown.** Don't hide paid items; people are on the page to
   judge value, and a `Free` chip next to `₹150` is information, not pressure.

---

## 7. Pitfalls

1. **Storing bytes in Neon.** The whole point of this document. Locator + sha256 + size only.
2. **Serving the file through the API.** Streams 10 MB through a database-adjacent process. Presign instead.
3. **A URL in the database.** Store `object_key`; compose the URL from config.
4. **Public full outputs for paid Ways.** Publishes the thing people pay for.
5. **Private thumbnails.** Makes every listing a grey box.
6. **Caching a mutable URL.** Keys include sha256, so they are immutable — cache for a year, never `no-cache`.
7. **Thumbnailing by model.** Slow, costly, and it can depict a cover that does not exist.
8. **A missing renderer.** Check LibreOffice/poppler availability before promising deck thumbnails; fall back to a
   labelled tile rather than a broken image.

---

## 8. Acceptance

1. `procedure_outputs` holds **no bytes** — assert the column list contains no `BYTEA`.
2. A 10 MB deck uploads and the Way page loads without the database doing any file I/O (measure: the page query
   returns in single-digit ms regardless of artefact size).
3. A thumbnail URL loads in a fresh browser with **no cookie and no token**.
4. A paid Way's output URL, fetched without a licence, **is refused** — asserted by the refusal, not the success.
5. The same file uploaded twice yields **one** object (content-addressed) and two rows only if two versions
   reference it.

---

## 9. One sentence

**Point `object_storage.py` at an R2 bucket, keep three hundred bytes in Neon and the PDF in the bucket, make
thumbnails public and outputs licence-gated, and let a deterministic renderer draw the picture — because the
database should store what an artefact *is*, never what it *weighs*.**
