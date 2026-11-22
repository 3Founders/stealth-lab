/**
 * A message that is about work on the person's own computer, not a task the Goal catalog should route.
 *
 * Why this exists: in the rooms' default "Ask" mode, `@kell` is held behind the Goal/Way picker. A desktop
 * command ("continue in my session", "run the tests on my laptop") has no Way, so it stopped at a "no stored
 * procedure" card and never reached the room agent — which is where the desktop hand-off lives. These messages
 * go straight to the agent; the agent still decides whether to hand them to a desktop session.
 *
 * Kept narrow on purpose: a general question that merely contains the word "desktop" must not skip the picker.
 */
const DESKTOP_INTENT: RegExp[] = [
  /\b(?:my|our)\s+(?:desktop|laptop|machine|computer|pc|workstation)\b/i,
  /\b(?:on|from|in)\s+(?:my|our|this|that)\s+(?:desktop|laptop|machine|computer|pc|workstation)\b/i,
  /\b(?:continue|resume|pick\s+up|carry\s+on)\b[^.!?\n]{0,40}\b(?:session|chat|work|task|where)\b/i,
  /\b(?:run|execute)\b[^.!?\n]{0,30}\b(?:tests?|build|command|script)\b[^.!?\n]{0,30}\b(?:desktop|laptop|machine|computer|pc|workstation)\b/i,
];

/** True when the text clearly asks for work on a desktop session. */
export const looksLikeDesktopCommand = (text: string): boolean =>
  DESKTOP_INTENT.some((re) => re.test(text || ""));
