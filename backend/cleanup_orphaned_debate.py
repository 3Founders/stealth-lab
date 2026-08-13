"""
Cleanly closes out a debate that got stuck in IN_DEBATE because the calling
client (e.g. the Inspector) disconnected/timed out mid-debate. Uses the real
DebateStateMachine.transition() -- not a raw UPDATE -- so debate_events
keeps an honest audit trail of what happened and why.

Usage:
    python3 cleanup_orphaned_debate.py <trigger_id>
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
from app.debate.state_machine import DebateStateMachine


async def main(trigger_id: str) -> None:
    pool = await create_pool(os.environ["DATABASE_URL"])

    trigger = await pool.fetchrow(
        "SELECT id, debate_id FROM triggers WHERE id = $1", trigger_id
    )
    if trigger is None:
        print(f"No trigger with id {trigger_id}")
        return
    if trigger["debate_id"] is None:
        print("This trigger has no debate attached yet -- nothing to clean up.")
        return

    debate_id = trigger["debate_id"]
    machine = DebateStateMachine(pool)
    state = await machine.current_state(debate_id)
    print(f"debate {debate_id} is currently: {state}")

    if state in ("APPROVED", "REJECTED"):
        print("Already terminal -- nothing to do.")
        return

    events = await pool.fetch(
        "SELECT from_state, to_state, reason, occurred_at FROM debate_events "
        "WHERE debate_id = $1 ORDER BY occurred_at",
        debate_id,
    )
    print("\nReal transition history for this debate:")
    for e in events:
        print(f"  {e['from_state']} -> {e['to_state']}  ({e['reason']})  at {e['occurred_at']}")

    confirm = input(f"\nTransition {debate_id} from {state} to REJECTED (orphaned)? [y/N] ")
    if confirm.strip().lower() != "y":
        print("Aborted, no change made.")
        return

    new_state = await machine.transition(
        debate_id, "REJECTED",
        reason="orphaned: client disconnected/timed out mid-debate, manually cleaned up",
        actor="manual_cleanup_script",
    )
    print(f"Done. Debate now: {new_state}")
    print("You can now open a fresh trigger for the same task_node and try again.")

    await pool.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 cleanup_orphaned_debate.py <trigger_id>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
