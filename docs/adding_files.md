# Adding files to chat, not just pictures: UI and backend (research, 2026-11-02)

For the founder. The ask: people should attach any file type in /chat and have it **managed by the backend**, the way
WhatsApp does it - a chat-native experience - not the way Gmail does it (an attachment-management experience). Sources:
product documentation read directly (V), vendor docs and practitioner guides (S), the OpenAlex literature at the bottom
(thin). Not legal or security advice.

## What exists in kell today, and why it cannot carry files

Pictures today are attached **client-side as data URLs inside the message payload** (lib/chat/images.ts): re-drawn on a
canvas to a 1568px edge, at most 4 per message, 40 MB raw / ~6 M chars of data URL. There is no server-side upload
endpoint at all. For pictures that is survivable; for files it breaks down on every axis:

- sync and history store the bytes in every message row, on every device, forever - storage bloat and slow first syncs
  (a documented failure mode: base64 payloads in message rows bloating the database and full-text indexes);
- a 2 GB document cannot live in a JSON payload at all;
- nothing validates what is inside a file, nothing scans it, nothing thumbnails it;
- agents and the backend can never fetch the file themselves (no URL to fetch).

So the direction is fixed before any research: **bytes move to server-side storage; the message carries metadata.**

## What WhatsApp actually does (V - the reference product)

From WhatsApp's own help and API documentation:

- One paperclip opens a **typed sheet**: Photos & Videos, Camera, Document, Contact - the type is chosen first, then
  the picker opens. On desktop, drag-and-drop into the text field works for any type.
- Limits are generous and stated: documents up to **2 GB**; up to 100 photos/videos at once; videos compressed to a
  size tier matched to connection speed (100 MB/720p default, 64 MB/480p on slow links); a per-photo
  **Standard/HD quality toggle**.
- Media lives **on the device**; the service keeps it for a bounded window (WhatsApp's own API: media is stored ~30
  days, then deleted) and the chat shows "media expired - tap to download again" if it was not fetched.
- In the chat, a document renders as a **card** (file icon, name, size, pages where known); pictures and videos render
  inline with a tap-to-open full view. Captions ride on the media, not the text field.

The takeaway: WhatsApp hides the machinery - no filename pickers mid-flow, no upload managers, no "attachments" tab.
The chat bubble is the unit.

## The UI, chat-native (S)

1. **One paperclip, typed choices.** A small attach menu (Photo or video / Document / anything else via the system
   picker) mirrors WhatsApp; the system file input carries `accept` hints per choice but every choice also allows
   "all files", because the phone knows better than we do what the person wants to send.
2. **Drag-and-drop and paste are first-class.** Drop on the composer; paste a screenshot or a copied file. Both feed
   the same attachment tray.
3. **The attachment tray shows tiles before sending**: picture/video tiles for media, a document tile (icon, name,
   size) for everything else, each with a remove cross; captions per attachment for media, like WhatsApp.
4. **Send before upload finishes, with the upload shown in the message bubble.** Stream's chat SDK documents this
   exact pattern: the message renders from the local file immediately, progress replaces the size label in the
   bubble, and the same widget is reused in the composer and the list so the attachment looks identical on both sides
   of pressing send. The trade-off is stated plainly: the message is not on the server until the upload completes -
   the bubble must say so ("sending... 60%"), and **one failed upload fails the message visibly, with a retry** -
   never a silent half-message.
5. **Previews respect the click-to-load rule kell already has.** Inline and free: images, short videos (muted loop),
   audio (a play bar), PDFs (page count + a render of page 1; PDF.js can draw it). Rendered only after a tap for:
   anything heavy (video >~20 MB), and anything executable-shaped. Office documents (docx, xlsx, pptx) do NOT render
   inline in any serious chat product - they show as download cards; do not build an office viewer.
6. **Downloads are explicit and labelled.** Tap a document card -> download (or open in a new tab for PDFs). Every
   download link is a signed, expiring URL; never a guessable path.

## The backend, file-managed (S, V)

**Ingest - the OWASP file-upload rules, unchanged since forever (S):**

- allowlist extensions AND validate the true type (magic bytes via a sniffer); the Content-Type header and the
  filename are user input, never trusted;
- rename on store (application-generated key), cap filename length and characters (double extensions, null bytes,
  case tricks are the documented bypasses); cap size per type;
- authenticated uploads only; store outside the webroot - in practice, in object storage behind our API.

**Ingest - who talks to storage (S, V):**

- **Presigned direct upload** is the standard shape: our API authorizes (who, room, size, type) and hands the browser
  a short-lived PUT/POST URL for object storage; the bytes never pass through our app server. AWS documents this as
  the ordinary case, and a 2026 security analysis of direct-to-cloud uploads (OpenAlex) is the caution: the presigned
  URL itself must be tightly scoped (single object key, size cap, short expiry) - it is a capability.
- **Proxy through our API** only as fallback (tiny files, or when a presign is awkward): simple, but every byte
  transits the app server.
- **Resumable uploads (tus)** are the protocol answer when files get large or networks flaky - the same protocol
  Vimeo uses. For a 2 GB cap this belongs in the plan; for a first cut, plain presigned PUT with a retry is honest.

**Ingest - dedup and integrity (S):**

- Hash the bytes (SHA-256) at upload; store content-addressed so the same file sent twice costs once (Hugging Face's
  Xet protocol is the current large-scale example of chunk-level content-addressed storage; whole-file hashing is the
  cheap version and enough at our size). The message row references the object; deleting a message decrements, an
  orphan sweep reclaims.

**Safety - scan before the file is served (S, V):**

- The Google Cloud reference pipeline generalizes to any stack: uploads land in an **unscanned** bucket; a ClamAV
  container (with a mirrored signature DB, refreshed) scans on arrival; clean files move to the **clean** bucket;
  infected ones to **quarantine** - never served, logged, and reported to the sender in chat ("the file you sent was
  removed: it contains malware"). Message payloads only ever reference clean objects, and only after the scan
  returns; until then the bubble shows "processing" - the same semantics as the upload progress.

**Serving - never let a stored file run as our origin (S):**

- Serve from a separate origin (object-storage URL or CDN domain with no session cookies) - a stored XSS payload in
  an SVG or crafted HTML inherits the app's session if served same-origin;
- `Content-Disposition: attachment` for everything except images/audio/video/PDF; `X-Content-Type-Options: nosniff`;
  serve previews (PDF viewer, media) from sandboxed frames;
- thumbnails and transcoded previews are generated server-side at ingest (pdf.js server-side or at render; video
  posters from a frame grab), so the chat list never downloads a whole file to show a tile.

**Retention - the WhatsApp shape (S, V):**

- Devices (IndexedDB) keep what they fetched; the server keeps the object for a bounded window (WhatsApp's API keeps
  ~30 days); the bubble degrades to "file expired - ask the sender again" rather than pretending it is there. A
  retention number is a product decision (decisions below), and rooms that go E2EE later will need the object
  encrypted per room key at ingest for that promise to survive - so the upload path should store an IV/algorithm
  field per attachment from day one even while it stores plaintext (rooms E2EE is in design, not shipped).

**Where the bytes live (S):**

| Option | ~$ / 100 GB-mo | Egress | Fits kell because |
|---|---|---|---|
| Railway Buckets (S3-compatible, where kell already runs) | mid | standard | one vendor for app + files; no new account |
| Cloudflare R2 | ~$15 | **$0** | previews and downloads are egress-heavy; zero egress kills the scariest bill |
| Backblaze B2 | ~$7 | free up to 3x storage/mo | cheapest at rest; fine behind a CDN |
| MinIO on a Railway volume | hardware | $0 | full control, E2EE-at-rest later; we run it |

R2 or Railway Buckets are the default answers for a small team (Valters/c17 comparisons); MinIO becomes right when
E2EE-at-rest wants a box we fully control.

## The data model this implies (I, mapped to what exists)

```
files table:  id, sha256, size, mime (sniffed), name (original), width/height (media), pages (pdf),
              state (scanning/clean/quarantine/expired), storage_key, uploader, room, created_at
message payload attachment: { file_id, name, mime, size, w?, h?, iv? }   # metadata only - no bytes
```

- The sync engine keeps working unchanged: attachments are small JSON like today, the payload no longer carries the
  image bytes, and old picture messages stay as they are (data URLs) until a migration wants them.
- Search never indexes bytes, only names and captions - the documented bloat lesson (the Odysseus base64-in-rows
  issue).
- Agents get files the same way people do: a room tool (room_files or an extension of room_read) that returns
  metadata plus a short-lived fetch URL for clean files, respecting the data-class rules.

## Build order (I)

1. Files table + presigned upload + OWASP validation + serving rules; pictures move to it first (they already have
   all the UX).
2. Attachment tray in the composer for any type + document cards in the bubble + pending-upload progress/retry.
3. ClamAV scan pipeline (quarantine semantics in the bubble: "processing" -> sent/removed).
4. Previews: PDF page-1 render, audio/video players, thumbnails at ingest.
5. Retention window + expired-bubble UI; tus resumable upload when the 2 GB tier matters.
6. Agent file access via a room tool; then per-room key wrapping for the E2EE future.

## OpenAlex (S; thin - this is mostly engineering practice)

- (In)Security of File Uploads in Node.js (2024) and URadar: Discovering Unrestricted File Upload Vulnerabilities via
  Adaptive Dynamic Testing (2023) - the failure modes are still the OWASP list.
- A Security Analysis of Website-Enabled Direct File Uploads to Cloud Storage Services (2026) - the presigned URL is
  a capability; scope it.
- Understanding user acceptance of multimedia messaging services: an empirical study (2007, 118 citations) - people
  accept media messaging when effort and perceived cost stay low; the WhatsApp lesson predates WhatsApp.
- A Comprehensive Review on Malware Detection Approaches (2020, 654) and Malware Detection in Cloud Computing (2014)
  - scanning-at-ingest is the settled shape.

## Decisions this doc asks for

1. Confirm the split: bytes to object storage behind the API; message payloads carry metadata only.
2. Pick the storage vendor (Railway Buckets vs R2 vs B2 vs MinIO) - default recommendation: R2 (zero egress) or
   Railway Buckets (one vendor).
3. Pick the per-type size caps (WhatsApp-shaped: 2 GB documents is the ceiling; suggest 100 MB at launch) and the
   server retention window (suggest 30 days, WhatsApp's own API number).
4. Confirm the scan gate (nothing served before the ClamAV scan returns) and the quarantine-notice copy.
