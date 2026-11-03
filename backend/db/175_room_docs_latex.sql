-- 175_room_docs_latex.sql
--
-- Next free number: 174 (room_docs) was highest before this file.
--
-- What this adds: a fourth kind of canvas document, 'latex'. It is written and shared exactly like a markdown document (same versions, merge and
-- live editing); the difference is only how the page shows it: the web app renders the LaTeX source (sections, lists, tables, theorems and
-- maths through KaTeX) beside the editor. Nothing about the stored body changes: it is the LaTeX source text.
--
-- Idempotent: the old inline check is dropped by name if present, and the widened one is added once.

ALTER TABLE room_docs DROP CONSTRAINT IF EXISTS room_docs_kind_check;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'room_docs_kind_check_v2' AND conrelid = 'room_docs'::regclass) THEN
        ALTER TABLE room_docs ADD CONSTRAINT room_docs_kind_check_v2 CHECK (kind IN ('document', 'code', 'html', 'latex'));
    END IF;
END $$;
