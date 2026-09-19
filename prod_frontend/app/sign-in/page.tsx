import type { Metadata } from "next";

export const metadata: Metadata = { title: "Sign in" };

export default function SignIn() {
  const url = process.env.NEXT_PUBLIC_KEL_SIGNIN_URL;
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>SIGN IN</b></div>
        <h1 className="display">Pick up where your work left off.</h1>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="signin">
          {url ? (
            <>
              <p className="lead">Sign-in is handled by this deployment’s identity provider.</p>
              <a className="btn-ink" href={url} style={{ alignSelf: "flex-start" }}>Continue to sign in <span className="sq" aria-hidden="true">→</span></a>
            </>
          ) : (
            <>
              <p className="lead">Sign-in isn’t connected in this build.</p>
              <p className="small dim">keळ deployments identify people through an OIDC provider. Set <code>NEXT_PUBLIC_KEL_SIGNIN_URL</code> to your provider’s entry point and this button appears. No password form is offered here, because none exists to back it.</p>
            </>
          )}
          <p className="small dim">Reading public knowledge doesn’t require an account. Your own private library stays on your machine unless you explicitly publish from it.</p>
        </div>
      </section>
    </>
  );
}
