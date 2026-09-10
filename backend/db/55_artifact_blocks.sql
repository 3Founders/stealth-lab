-- Migration 55 (V4-hardening Part II-A §3-§4 / Gate G2 / audit B16):
-- artifact_blocks -- the normalized, addressable structure of an ingested
-- artifact, every block carrying the source location it was cut from.
--
-- Next free migration number: 56.
--
-- WHY THIS EXISTS
--   `ingested_artifacts` (migrations 32/39) records an artifact's
--   provenance -- where the bytes came from, which content_hash, which
--   extractor -- but NOT its internal structure. The skill_md parser
--   (`skill_ingestion.py::parse_skill_md` / `_split_sections`) splits a
--   document into sections but keeps ZERO offsets: once parsed, there is
--   no way back to the exact span a heading, paragraph, or step occupied
--   in the raw source. So a Claim or Observation derived from an artifact
--   cannot be cited back to the characters that justify it.
--
--   V4-hardening §4: "Every normalized block MUST retain source location
--   information ... This is what makes later claims auditable back to the
--   source." This table is that retained structure: one row per block,
--   with `source_start` / `source_end` char offsets into the exact bytes
--   named by `artifact_content_hash`.
--
-- WHAT THIS IS NOT
--   Not a re-parse of `parse_skill_md`, and it does not touch it. The
--   normalizer that fills this table (`app/services/artifact_blocks.py`)
--   is a general, deterministic STRUCTURAL splitter that runs alongside
--   the existing section parser -- it classifies SHAPE (heading / list /
--   code / table / paragraph), never meaning. Not a second provenance
--   graph either: `ingested_artifacts` / `ingestion_contexts` stay as
--   they are; a block points AT an artifact by id + content_hash.
--
--   No FK from `artifact_id` to `ingested_artifacts(id)`: same reasoning
--   migration 32 gives for `procedure_id` / `procedure_row_id` -- an
--   artifact row may be tombstoned (t_invalid) while its blocks must stay
--   queryable so an old claim's citation still resolves.
--
-- OFFSET CONVENTION
--   `source_start` / `source_end` are CHARACTER offsets into the decoded
--   text of the artifact (Python `str` indices), half-open, such that
--   `content[source_start:source_end]` round-trips to the block's raw
--   span (trailing newline excluded). They are only valid against the
--   exact bytes whose sha256 is `artifact_content_hash`; a different hash
--   means different offsets and MUST be a different set of block rows.
--
-- Fresh-start rule: additive, nullable back-links, NO backfill. Existing
-- artifacts get blocks only when re-ingested under a normalizer that
-- writes them.
--
-- Idempotent: CREATE ... IF NOT EXISTS / guarded ADD CONSTRAINT, same
-- idiom as every other migration in db/.

-- ============================================================
-- artifact_blocks [H] -- immutable once written (see the freeze section
-- at the bottom). One row per block of one artifact snapshot.
-- ============================================================
CREATE TABLE IF NOT EXISTS artifact_blocks (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- the ingested_artifacts(id) this block belongs to. No FK on purpose
    -- (see header): the artifact row may be tombstoned while blocks stay
    -- queryable for citation resolution.
    artifact_id             UUID NOT NULL,

    -- pins the exact immutable bytes these offsets are valid against.
    -- A changed artifact -> a new content_hash -> a fresh set of block
    -- rows, never an edit to these (freeze section below).
    artifact_content_hash   TEXT NOT NULL,

    block_index             INTEGER NOT NULL CHECK (block_index >= 0),

    -- nesting: a paragraph / list item under a heading points at that
    -- heading's row. NULL = top level. No FK (self-referential, and the
    -- whole table is append-only -- a dangling parent can never be
    -- created because the writer inserts parents first).
    parent_block_id         UUID,

    -- structural SHAPE, not meaning. TEXT + named CHECK (not an enum) so
    -- the vocabulary is widenable in a one-line migration, same call
    -- migration 50 made for source_kind's later additions.
    block_type              TEXT NOT NULL,

    -- heading level (1-6) or nesting depth; 0 for a top-level non-heading.
    depth                   INTEGER NOT NULL DEFAULT 0,

    text                    TEXT NOT NULL,

    -- char offsets into the decoded artifact content (see header). The
    -- CHECKs make an inverted or negative span unwritable from a
    -- direct-SQL path that bypasses the service.
    source_start            INTEGER NOT NULL CHECK (source_start >= 0),
    source_end              INTEGER NOT NULL CHECK (source_end >= source_start),

    -- a stable slug anchor (e.g. 'h2-installation') for projection
    -- references. NULL when the block is not anchorable (a bare
    -- paragraph). Not unique: two artifacts, or two identically-titled
    -- headings, may collide -- consumers disambiguate by block_index.
    anchor                  TEXT,

    -- Birth discipline (V0 gate): scope + provenance on every write. The
    -- service owns the "who / under what scope" error message; the
    -- columns are nullable so a direct-SQL writer still has somewhere to
    -- put the truth (migration 21's split).
    created_by              TEXT,
    -- Ticket 09 pair rule: BOTH columns on every new table
    -- (access.py::visibility_predicate() breaks otherwise).
    visibility              visibility_level NOT NULL DEFAULT 'public',
    owner_id                TEXT,
    scope_type              TEXT,
    scope_entity_id         TEXT,

    -- nullable back-link to the IngestionContext every derived row stamps
    -- (migration 51). NULL for a block written outside a context.
    ingestion_context_id    UUID,

    -- Bi-temporal trio, evidence / sources style: t_valid / t_invalid =
    -- true-in-the-world (t_invalid = a superseded block set, tombstoned
    -- when a better extraction of the SAME bytes lands -- rare, see the
    -- freeze note), t_created = when the system learned it.
    t_valid                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    t_invalid               TIMESTAMPTZ,
    t_created               TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- one block per position within one artifact snapshot. Makes re-ingest
    -- of byte-identical content a cheap ON CONFLICT DO NOTHING.
    UNIQUE (artifact_id, artifact_content_hash, block_index)
);

-- ---- named, greppable CHECKs (migration 22/24's defense-in-depth idiom:
-- the service layer owns error messaging, the engine refuses garbage from
-- direct-SQL paths that bypass it) ----
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'block_type_chk_artifact_blocks') THEN
        ALTER TABLE artifact_blocks ADD CONSTRAINT block_type_chk_artifact_blocks
            CHECK (block_type IN (
                'heading', 'paragraph', 'ordered_list_item',
                'unordered_list_item', 'code_block', 'table',
                'frontmatter', 'other'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'depth_chk_artifact_blocks') THEN
        ALTER TABLE artifact_blocks ADD CONSTRAINT depth_chk_artifact_blocks
            CHECK (depth >= 0);
    END IF;

    -- Scope CHECK, migration 22/23/24's greppable one-block-per-table
    -- shape. NOT VALID: a fresh-start database has no rows to validate,
    -- and the service is the primary gate.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'scope_type_chk_artifact_blocks') THEN
        ALTER TABLE artifact_blocks ADD CONSTRAINT scope_type_chk_artifact_blocks
            CHECK (scope_type IS NULL OR scope_type IN (
                'global','organization','team','project','repository',
                'branch','user','session','task','entity')) NOT VALID;
    END IF;
END $$;

-- "every live block of this artifact, in order" -- the citation read path.
CREATE INDEX IF NOT EXISTS idx_artifact_blocks_artifact
    ON artifact_blocks (artifact_id) WHERE t_invalid IS NULL;
-- "which block set covers these exact bytes"
CREATE INDEX IF NOT EXISTS idx_artifact_blocks_content_hash
    ON artifact_blocks (artifact_content_hash);
-- nesting traversal
CREATE INDEX IF NOT EXISTS idx_artifact_blocks_parent
    ON artifact_blocks (parent_block_id) WHERE parent_block_id IS NOT NULL;

-- ============================================================
-- The freeze. artifact_blocks is [H] in schema.md's mutability classes:
-- a block is a fact about an immutable byte string (the artifact snapshot
-- at `artifact_content_hash`). Its text and its offsets NEVER change --
-- there is nothing to correct, only a different snapshot to describe.
--
--   * Improved extraction  -> the artifact is re-ingested, a NEW
--     ingested_artifacts row / content_hash is produced, and a NEW set of
--     artifact_blocks is written against it. The old blocks stay, live,
--     citable by any claim that already points at them.
--   * Superseding a block set for the SAME hash (a normalizer bug fix
--     re-run over unchanged bytes) is the one rare tombstone case:
--     t_valid -> t_invalid on the old rows, append the new set. The
--     UNIQUE (artifact_id, artifact_content_hash, block_index) is why the
--     re-run cannot collide -- the writer tombstones first, then inserts.
--
-- No append-only trigger in THIS migration: there is no block-retraction
-- writer yet, so a trigger would guard a path nothing exercises. It lands
-- in the SAME change that wires the first real re-normalization sweep --
-- gate and writer together (half-gate rule). Until then the service is
-- the only writer and it only ever INSERTs.
-- ============================================================
