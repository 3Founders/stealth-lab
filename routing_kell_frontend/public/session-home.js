/* A signed-in person who opens the home page goes to the rooms (kell.org.in -> /chat) the first time in a tab. The
   sessionStorage flag means a later "Back to home" (or a refresh) shows the landing page instead of bouncing back; a
   new tab starts fresh and redirects once. An explicit home link carries ?home=1, which always shows the page (so the
   in-app Back to home never bounces), and also marks this tab as having seen it. It reads Supabase's own session entry
   (sb-<project-ref>-auth-token, the key its browser client writes); components/OnboardingGate.tsx is the fallback for a
   session that arrives after load (an OAuth return) and for a client-side navigation to "/". Signed-out visitors, and
   every other page, are left alone. */
try {
  if (location.pathname === "/") {
    var stay = /[?&]home=1(?:&|$)/.test(location.search || "");
    var done = false;
    try { done = sessionStorage.getItem("kell:home:redirected") === "1"; } catch (e) { /* storage blocked */ }
    if (stay) {
      try { sessionStorage.setItem("kell:home:redirected", "1"); } catch (e) { /* storage blocked */ }
    } else if (!done) {
      for (var i = 0; i < localStorage.length; i++) {
        var key = localStorage.key(i);
        if (!key || key.indexOf("sb-") !== 0 || key.slice(-11) !== "-auth-token") continue;
        var stored = JSON.parse(localStorage.getItem(key) || "null");
        var session = Array.isArray(stored) ? stored[0] : stored;
        if (session && (session.access_token || (session.currentSession && session.currentSession.access_token))) {
          try { sessionStorage.setItem("kell:home:redirected", "1"); } catch (e) { /* storage blocked */ }
          location.replace("/chat");
          break;
        }
      }
    }
  }
} catch (e) { /* storage blocked: the client fallback still runs */ }
