# Thumbnails and previews for a Way

**The picture on a listing, where it comes from, and why it should be a run rather than an upload. 2026-11-23.**
Companion to `docs/procedure_outputs.md` (where the bytes live) and `docs/blinkit/what_was_done.md` (what is built).

Nothing here is wired beyond the renderer and the artifact path. This is the design record.

---

## 0. The rule

**A thumbnail is a cover, not the product.** The output is the file the buyer receives; the thumbnail is the
picture that helps them decide. kell must never invent a picture of an output it does not have, and must never let
an upload stand in for evidence.

---

## 1. What exists today

| Piece | State |
|---|---|
| `backend/app/services/output_thumbnails.py` | ✅ PDF page 1 via PyMuPDF (90 dpi, WebP ~2.4 KB), images normalised to WebP, Office text runs via stdlib; raises `ThumbnailUnavailable` rather than a placeholder |
| `backend/app/submissions/artifacts.py` + `db/222_step_produces.sql` | ✅ a step declares `produces`/`imports`; bytes go to object storage, Neon keeps a locator + sha256 + size; `view()` exposes artifacts and `download_all` |
| `docs/procedure_outputs.md` | ✅ the storage/access design: `procedure_outputs`, public thumbs, gated outputs, `presign_get` |
| `procedure_outputs` table | ❌ not created |
| `get_public().output` | ❌ not projected; the frontend type `WayOutput` exists and the `Output` card renders it, but no field arrives |
| `similar[].thumb_url` | ❌ not projected |
| Upload / sample-run path | ❌ not built |

So the renderer is real, the files are real, and the page has a slot for them. The missing piece is the row that
connects a version to its artefact, plus the projection.

---

## 2. The options

| Option | Authenticity | Cost | Works with no file output | Moderation |
|---|---|---|---|---|
| Author uploads a cover | claimed | low | yes | images (NSFW, PII, copyright, misleading) |
| Author runs it, submits the run | real | author pays | yes (human steps included) | output scan |
| **kell runs a sample** | **real, independent** | kell pays one run | no | input + output scan |
| Auto-render from an existing run | real | none | no | output scan |

The literature is consistent on the important part: the first image does the selling (Etsy, Gumroad, SlideShare),
but a misleading image is the failure mode every marketplace writes a policy against. kell’s whole argument is
evidence, so a decorative cover is the one thing it cannot afford.

---

## 3. Recommendation

1. **The sample is a real run, not an upload.**
   - The author submits **anonymized inputs and answers** to the questions/human steps.
   - kell runs the Way once (headless) and stores the output as the sample.
   - The cover and the filmstrip are rendered from that output.
2. **Fallback for human steps:** the author runs it in kell desktop, answers the steps, and submits the completed
   run. kell renders from the run record; the page says the sample includes a human step.
3. **Uploads only as a labelled fallback** when there is nothing to render, and never as the only preview.
4. **No sample, no picture.** The listing keeps the text row; it does not get a placeholder.

Why this is better than an upload: there is nothing to compare, because the preview *is* the output of a run. PII
is handled at the input, before anything is rendered.

---

## 4. How the sample run works

```
author fills inputs + answers  ->  PII scan (text)
  ->  way_runs.start(headless, input, answer_script)
       on waiting_human / question: feed the next answer
       on a step that needs a real sign-in: stop, ask for the desktop run
  ->  output stored via artifacts.py (object storage)
  ->  cover + frames rendered (output_thumbnails.py)
  ->  PII scan on output text + frames
  ->  procedure_outputs row (source='sample_run', run_id, keys)
  ->  reviewer sees input, answers, output; approves
```

The sample run is **internal**: it bypasses the purchase gate and never consumes a purchase. It is one run per
version, capped by steps/tokens, re-run only when the Way changes.

What it does **not** prove: that future runs are identical. The page says “a sample from one run with anonymized
inputs”, never “the output you will get”.

---

## 5. Data model

`docs/procedure_outputs.md` §3.1 defines the row. This adds the sample-run fields:

```sql
ALTER TABLE procedure_outputs
  ADD COLUMN IF NOT EXISTS source       TEXT NOT NULL DEFAULT 'sample_run',  -- sample_run | author_run | upload
  ADD COLUMN IF NOT EXISTS run_id       UUID,          -- the run that produced it
  ADD COLUMN IF NOT EXISTS preview_keys TEXT[],        -- cover + filmstrip frames, in order
  ADD COLUMN IF NOT EXISTS verified_at  TIMESTAMPTZ,   -- set when a run produced it
  ADD COLUMN IF NOT EXISTS pii_scan     JSONB;         -- {engine, ran_at, entity_types, count}; never values
```

Rules unchanged: `object_key`, not a URL; bytes in object storage; one row per distinct artefact per version;
`sha256` in the key so the URL is immutable.

---

## 6. Rendering

| Kind | Renderer | Output |
|---|---|---|
| PDF | PyMuPDF page 1 | 640w WebP cover; page count from the same open |
| Image | Pillow, downscaled never cropped | 640w WebP |
| PPTX / DOCX | **no faithful renderer installed** | text-only tile from `slide1.xml` / `document.xml`, or HTML → headless Chrome if we build it |
| Deck filmstrip | first 3–5 pages/slides | 1280×720 WebP each |

- WebP quality 82; cover 640w; tile 1280×720 (16:9).
- Public bucket, `Cache-Control: public, max-age=31536000, immutable`; key `thumbs/<sha256>.webp`.
- The full output is public for a free Way and a short-lived presigned URL for a paid one (`presign_get` is the
  one addition `object_storage.py` needs).
- **Never** publish the PDF’s own `/Thumb`, search index or private app data; render a fresh image.

---

## 7. PII

A preview is a **new raster**, so hidden PDF text, OCR layers, metadata and stale thumbnails cannot ride along.
Only what is visible can leak. That makes the preview a much smaller problem than the file.

**Inputs (before the run).** Scan the submitted inputs and answers with Presidio (regex + checksum + NER). A hit
blocks the run and asks for anonymization. Store only the anonymized input, or its hash — not the raw text.

**Output text and frames (after the run).** Extract text (PyMuPDF spans; the rendered HTML for our own decks) and
scan it. For text inside images, OCR with boxes. Store only `{engine, ran_at, entity_types, count}`.

**The artefact.** For the file the buyer receives: strip PPTX speaker notes, hidden slides, comments and core
properties; strip PDF metadata, `/Thumb`, search index and private app data — **before** hashing and storing, so
the content-addressed key covers the clean bytes.

**Never treat a black rectangle as redaction.** The literature is explicit: text under a box is recoverable in
Word-produced PDFs, and programmatic PDF redaction cannot reliably remove text split across operators or hidden
behind clipping paths. If the file must be clean, use PyMuPDF `apply_redactions` and re-extract text to verify,
then sanitize the metadata.

**What to do on a hit:** v1 withholds the frame (neutral tile, author picks another); v1.1 masks the *preview*
pixels and labels it “redacted preview”; the full file is never half-redacted.

---

## 8. If uploads are allowed

Verify by running and comparing. Exact hashes will not work — LLM output varies — so compare with tolerant
signals:

| Signal | Catches |
|---|---|
| kind | PDF vs PPTX |
| page/slide count | a 3-page report claimed as 12 |
| headings/key phrases | different content, same shape |
| first-page image | pHash (near-duplicate), SSIM (layout), CLIP (crops/restyles) |

Result: **Verified** (badge), **Not verified** (shown as “Author preview”), **Could not verify** (run failed or
needs a person). A stylised cover should fail a strict SSIM and still be allowed as an author image, just not
badged.

---

## 9. The page

```
Output
┌──────────────────┐  Research brief — 6 slides
│   [cover slide]  │  PPTX · 6 slides · 1.2 MB
└──────────────────┘  [ Open the deck ]
[1] [2] [3] [4] [5]   real slides, click to enlarge
Sample from one run with anonymized inputs.
```

- Cover first; it must stand alone (NN/g: people often miss everything after the first frame).
- A **visible filmstrip**, not an auto-rotating carousel. No autoplay; named controls (“Slide 2 of 6”); keyboard
  reachable; a lightbox on click.
- 16:9 for decks; PDF pages letterboxed into the same ratio so the grid stays even.
- Labels: “Sample from one run” / “Author preview” / “Includes a human step”. Never “recommended”.
- Related Ways use the same cover; a Way with no renderable cover keeps the text row.

---

## 10. Cost

- **One sample run per version**, at submission/approval; re-run only when the Way changes.
- A run is model spend: for a long Way it can exceed a ₹1 sale. Treat the sample as a publishing cost, cap it,
  and quota re-runs.
- Cheaper alternatives: the author runs it on their own machine/key and submits the run (kell pays nothing), or
  the author brings their own provider key.
- The first-run spend ceiling (`caps.md` §4.1) applies to the sample too: a runaway sample should fail loudly.

---

## 11. Phasing

1. **v1:** `procedure_outputs` table, sample run with anonymized inputs, PDF cover, `get_public.output`, the
   existing `Output` card. No uploads, no filmstrip.
2. **v1.1:** author picks the cover from rendered frames; 3–5 frame filmstrip; `similar[].thumb_url`.
3. **v2:** optional author-uploaded context images, verified by comparison, never the primary; PPTX layout
   renderer (HTML + Chrome) if the text tile is not enough.

---

## 12. Open questions

1. **Who pays for the sample run** — kell at approval, or the author with a quota?
2. **Human steps** — require a desktop run, or allow a scripted note labelled as the person’s account?
3. **PPTX renderer** — text tile first, or build HTML → headless Chrome now?
4. **PII engine** — local Presidio + Tesseract (private, free, noisier) or Google DLP (accurate, paid, data leaves
   the server)?
5. **Sample visibility** — is the sample file open to anyone as a sales sample, or gated like a paid output?
6. **Uploads** — allowed at all, or only as a labelled fallback?

---

## Sources

- Etsy image rules — https://help.etsy.com/hc/en-us/articles/115015663347
- Etsy AI policy — https://www.etsy.com/seller-handbook/article/1522733284810
- Walmart AI content policy — https://marketplacelearn.walmart.com/guides/Item%20setup/Item%20content,%20imagery,%20and%20media/Product-Detail-Page:-overview
- Airbnb verified images (HBS) — https://www.hbs.edu/faculty/Pages/item.aspx?num=60512
- NN/g carousels — https://www.nngroup.com/articles/designing-effective-carousels/
- Accessible galleries — https://jralo.com/blog/accessible-product-images-and-carousels/
- Gumroad covers — https://gumroad.com/help/article/60-adding-a-cover-image
- FileDeck PDF thumbnails — https://www.getfiledeck.com/docs/pdf-thumbnails/
- pikepdf sanitize / redaction limits — https://pikepdf.readthedocs.io/en/latest/topics/sanitize.html
- Adobe PDF redaction — https://www.adobe.com/acrobat/resources/how-to-redact-a-pdf.html
- Presidio — https://github.com/microsoft/presidio/blob/main/docs/text_anonymization.md
- Google DLP image redaction — https://docs.cloud.google.com/sensitive-data-protection/docs/concepts-image-redaction
- Limina PPTX de-identification — https://docs.getlimina.ai/configuration-and-operations/working-with-files/processing-files/powerpoint
- PowerPoint notes/hidden slides — https://nonimo.ai/resources/guides/how-to-redact-in-powerpoint/
- PyMuPDF redactions — https://pymupdf.readthedocs.io/en/latest/page.html
- OCR + NER redaction example — https://github.com/athal7/image-pii-redactor/

---

## One sentence

**Render the picture from a real run with anonymized inputs, keep the bytes in object storage and the row in
Neon, scan the input and the visible frames rather than trying to redact the PDF, and let an upload be a labelled
fallback rather than the evidence.**
