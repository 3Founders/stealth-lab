"use client";
import { useCallback, useRef, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import Turnstile, { TURNSTILE_SITE_KEY, type TurnstileHandle } from "@/components/Turnstile";
import { TOPICS, bookingUrl, contactEmail, submitContact, type Topic } from "@/lib/contact";

type Status = "idle" | "sending" | "sent" | "error";

export default function ContactPage() {
  const booking = bookingUrl();
  const email = contactEmail();
  const [form, setForm] = useState({ name: "", email: "", company: "", topic: "general" as Topic, message: "", website: "" });
  const [token, setToken] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");
  const captcha = useRef<TurnstileHandle>(null);
  const onToken = useCallback((t: string | null) => setToken(t), []);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (status === "sending") return;
    setStatus("sending");
    setMessage("");
    const r = await submitContact({ ...form, turnstileToken: token });
    captcha.current?.reset(); // a Turnstile token is single use, pass or fail
    if (r.ok) { setStatus("sent"); return; }
    setStatus("error");
    setMessage(r.message);
  }

  const needsCaptcha = Boolean(TURNSTILE_SITE_KEY);
  const canSend = status !== "sending" && (!needsCaptcha || Boolean(token));

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTACT</b></div>
        <h1 className="display"><AnimatedHeading>Talk to a founder</AnimatedHeading></h1>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="contact-wrap">
          {/* The booking slot is always here. Set NEXT_PUBLIC_BOOKING_URL to a Calendly (or any https booking) link and it becomes live;
              until then it says so, rather than linking nowhere. */}
          <div className="contact-book">
            <h2 className="h3">Prefer to talk?</h2>
            {booking ? (
              <>
                <p className="dim">Pick a time that suits you.</p>
                <a className="btn-ink" href={booking} target="_blank" rel="noopener noreferrer">
                  <span>Book a call</span><span className="sq" aria-hidden="true">↗</span>
                </a>
              </>
            ) : (
              <>
                <p className="dim">Booking a call opens soon. For now, write to us below.</p>
                <button type="button" className="btn-ink" disabled aria-disabled="true"><span>Book a call</span></button>
              </>
            )}
          </div>

          {status === "sent" ? (
            <div className="signin" role="status">
              <p className="lead">Thanks. We have your message.</p>
              <p className="small dim">We&rsquo;ll write back to {form.email}.</p>
            </div>
          ) : (
            <form className="cform contact-form" onSubmit={submit} noValidate={false}>
              <h2 className="h3">Or write to us</h2>
              <p className="small dim">Tell us what you&rsquo;re trying to do. We&rsquo;ll answer by email.</p>
              <div className="cfield"><label htmlFor="c-name">Your name</label>
                <input id="c-name" name="name" type="text" autoComplete="name" required maxLength={120} value={form.name} onChange={set("name")} /></div>
              <div className="cfield"><label htmlFor="c-email">Work email</label>
                <input id="c-email" name="email" type="email" autoComplete="email" required maxLength={254} value={form.email} onChange={set("email")} /></div>
              <div className="cfield"><label htmlFor="c-company">Company <span className="dim">(optional)</span></label>
                <input id="c-company" name="company" type="text" autoComplete="organization" maxLength={160} value={form.company} onChange={set("company")} /></div>
              <div className="cfield"><label htmlFor="c-topic">What is this about?</label>
                <select id="c-topic" name="topic" value={form.topic} onChange={set("topic")}>
                  {TOPICS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                </select></div>
              <div className="cfield"><label htmlFor="c-message">Message</label>
                <textarea id="c-message" name="message" required rows={6} maxLength={4000} value={form.message} onChange={set("message")} /></div>

              {/* The honeypot. Hidden from people and from assistive technology; a bot that fills every field fills this too. */}
              <div aria-hidden="true" style={{ position: "absolute", left: "-10000px", width: 1, height: 1, overflow: "hidden" }}>
                <label htmlFor="c-website">Leave this empty</label>
                <input id="c-website" name="website" tabIndex={-1} autoComplete="off" value={form.website} onChange={set("website")} />
              </div>

              <Turnstile ref={captcha} onToken={onToken} />
              <div className="cform-submit">
                <button className="btn-ink" type="submit" disabled={!canSend}>
                  <span>{status === "sending" ? "Sending…" : "Send message"}</span>
                  <span className="sq" aria-hidden="true">→</span>
                </button>
                {status === "error" && <span className="small" role="alert">{message}</span>}
              </div>
              <p className="small dim">
                We use what you send only to reply to you. See our <a href="/docs#legal" style={{ textDecoration: "underline" }}>legal documents</a>.
                {email && <> Prefer email? <a href={`mailto:${email}`} style={{ textDecoration: "underline" }}>{email}</a>.</>}
              </p>
            </form>
          )}
        </div>
      </section>
    </>
  );
}
