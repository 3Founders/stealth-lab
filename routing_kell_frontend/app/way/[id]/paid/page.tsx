"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getAccessToken } from "@/lib/session";
import { MCP_ORIGIN } from "@/lib/connector";
import "@/app/way/way.css";

/**
 * After a successful payment: two choices, nothing else.
 *
 *  1. Do it on the website — run the Way here, answering only the inputs and questions its steps
 *     declare (app/submissions/way_inputs.py). That is the whole interaction.
 *  2. Download kell — keep and edit the output on the device, and submit Ways or procedures yourself.
 *
 * The page never trusts the query string alone: it waits until the server reports the purchase as
 * paid (the webhook is the source of truth), polling for a few seconds because the redirect can beat
 * the webhook. If it is still confirming, the Way page is the honest place to wait.
 */
type Confirm = "checking" | "paid" | "slow" | "signin";

export default function PaidPage() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id || "");
  const [confirm, setConfirm] = useState<Confirm>("checking");

  useEffect(() => {
    if (!id) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;

    const check = async () => {
      const token = await getAccessToken();
      if (!token) {
        if (live) setConfirm("signin");
        return;
      }
      try {
        const res = await fetch(`${MCP_ORIGIN}/v1/goals/submissions/${encodeURIComponent(id)}`, {
          headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
          cache: "no-store",
        });
        if (res.ok) {
          const body = await res.json();
          const way = body?.submission ?? body;
          const status = way?.purchase_status
            || (way?.licence_status === "purchased" ? "paid" : "none");
          if (status === "paid" || status === "used") {
            if (live) setConfirm("paid");
            return;
          }
        }
      } catch { /* keep waiting: the webhook may still be in flight */ }
      attempts += 1;
      if (attempts >= 15) {
        if (live) setConfirm("slow");
        return;
      }
      timer = setTimeout(check, 2000);
    };

    void check();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [id]);

  /**
   * Once the payment is confirmed, open the room that runs it.
   *
   * The room is created here and NOT started: a person who lands on a paid page should see what the Way needs
   * before anything spends their run. The run begins when they press Start inside the room.
   */
  const [room, setRoom] = useState<{ room_id: string; href: string } | null>(null);
  const [roomError, setRoomError] = useState<string | null>(null);

  useEffect(() => {
    if (confirm !== "paid" || !id || room) return;
    let live = true;
    void (async () => {
      try {
        const token = await getAccessToken();
        if (!token) return;
        const res = await fetch(`${MCP_ORIGIN}/v1/rooms/from-way`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json",
                     Authorization: `Bearer ${token}` },
          body: JSON.stringify({ submission_id: id }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body?.error?.message || body?.error || `the server answered ${res.status}`);
        if (live) {
          setRoom({
            room_id: body.room_id,
            href: `/chat/present?room=${encodeURIComponent(body.room_id)}&way=${encodeURIComponent(id)}`,
          });
        }
      } catch (e) {
        if (live) setRoomError(String((e as Error)?.message || e));
      }
    })();
    return () => { live = false; };
  }, [confirm, id, room]);

  const wayHref = `/way/${encodeURIComponent(id)}`;
  const runHref = `${wayHref}#kwm-run-h`;
  const signInHref = `/sign-in?redirect=${encodeURIComponent(`/way/${id}/paid`)}`;

  return (
    <section className="kwm frame grid kwm-paid">
      <header className="kwm-id">
        <h1 className="display">Payment received</h1>
        <p className="kwm-muted">One output of this Way is yours. Choose where to produce it.</p>
      </header>

      <div className="kwm-paid-options">
        <article className="kwm-paid-card">
          <h2>Do it on the website</h2>
          <p>
            Run it here, step by step. You answer only the inputs this Way asks for; the run starts when you
            say so, and nothing is spent until then.
          </p>
          {confirm === "paid" ? (
            room ? (
              <Link className="kwm-cta" href={room.href}>Open it in kell</Link>
            ) : roomError ? (
              <p className="kwm-small kwm-muted">
                The room could not be opened: {roomError}{" "}
                <Link href={wayHref}>Open the Way</Link>
              </p>
            ) : (
              <p className="kwm-small kwm-muted" role="status">Opening a room for this Way…</p>
            )
          ) : confirm === "signin" ? (
            <Link className="kwm-cta" href={signInHref}>Sign in to run it</Link>
          ) : confirm === "slow" ? (
            <p className="kwm-small kwm-muted">
              The payment is still confirming.{" "}
              <Link href={wayHref}>Open the Way</Link>; the run appears once it is confirmed.
            </p>
          ) : (
            <p className="kwm-small kwm-muted" role="status">Confirming the payment…</p>
          )}
        </article>

        <article className="kwm-paid-card">
          <h2>Download kell</h2>
          <p>
            Keep the output and edit it on your device. With kell installed you can also submit Ways and
            procedures yourself.
          </p>
          <a className="kwm-cta" href="/download">Download kell</a>
        </article>
      </div>
    </section>
  );
}
