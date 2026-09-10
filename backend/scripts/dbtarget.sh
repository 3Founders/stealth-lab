# Point this bash/zsh session's DB env at the local or hosted Postgres.
#
# SOURCE this (do not execute) so the vars land in your current shell:
#
#     source scripts/dbtarget.sh local     # everything now uses $DATABASE_URL_LOCAL
#     source scripts/dbtarget.sh hosted     # back to Supabase
#     source scripts/dbtarget.sh show       # what's active + what each resolves to
#     source scripts/dbtarget.sh off        # clear the overrides
#
# After `source scripts/dbtarget.sh local` a bare `python scripts/migrate.py`
# or `python -m pytest tests -q` uses the local DB; the e2e suite picks it up
# via tests/conftest.py's TEST_DATABASE_URL promotion.
#
# Run from backend/ . Needs DATABASE_URL_LOCAL in backend/.env (or the env).

_dbtarget_main() {
    local here dbpy target
    here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
    dbpy="$here/dbtarget.py"
    target="${1:-show}"

    case "$target" in
        off)
            unset DATABASE_URL TEST_DATABASE_URL STEALTHLAB_DB_TARGET
            echo "[dbtarget] cleared DATABASE_URL / TEST_DATABASE_URL / STEALTHLAB_DB_TARGET for this session."
            return 0
            ;;
        show)
            python "$dbpy" show
            return $?
            ;;
        local|hosted)
            local lines
            if ! lines="$(python "$dbpy" "$target" --print-env --shell bash)"; then
                echo "[dbtarget] resolve failed for '$target' (see message above)." >&2
                return 1
            fi
            eval "$lines"
            echo "[dbtarget] session now points at: $target"
            python "$dbpy" show
            ;;
        *)
            echo "[dbtarget] usage: source scripts/dbtarget.sh {local|hosted|show|off}" >&2
            return 2
            ;;
    esac
}

_dbtarget_main "$@"
