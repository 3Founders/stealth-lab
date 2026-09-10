-- Migration 59: drops `procedure_implementation_bindings` (migrations
-- 53/54), the table this session's B23/B24 gate built BEFORE
-- discovering `procedure_implementations` already existed (in
-- production, 482 real rows, richer, bi-temporally versioned -- see
-- migration 58's own comment for the full story). CLAUDE.md rule 2
-- forbids leaving two tables for the same relation once the duplication
-- is known; `app/services/procedure_implementation_bindings.py` has
-- already been rewritten to operate against `procedure_implementations`
-- instead, so nothing reads or writes this table any more.
--
-- Safe: confirmed zero real rows immediately before this migration was
-- written (only this session's own already-cleaned test debris ever
-- existed in it).

DROP TABLE IF EXISTS procedure_implementation_bindings;
