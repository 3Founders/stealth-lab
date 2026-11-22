# Back control on a marketing-site subpage (/contact, "Talk to a founder"): WCAG 2.2, behaviour, patterns

## 1. Which WCAG 2.2 criteria bear on a back control, and what do they mean concretely?

### Takeaway
A Back control touches AA criteria 2.5.8 (24px target), 3.2.3 and 3.2.4 (consistency), 2.4.7 and 2.4.11 (focus), 1.4.11 (3:1 contrast) and 2.4.3 (focus order). 2.5.5 (44px) and 2.4.13 (focus appearance) are AAA, so they are optional targets. 2.4.5 (multiple ways) is mostly not triggered by the Back control.

### Cited Findings
- 2.5.8 Target Size (Minimum), AA: pointer targets are at least 24x24 CSS px. Exceptions are spacing (a 24px circle on each undersized target doesn't overlap another), equivalent control, inline (within a sentence or limited by line height), user-agent control and essential. — [W3C Understanding 2.5.8](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)
- 2.5.5 Target Size (Enhanced), AAA: targets are at least 44x44 CSS px. Exceptions are equivalent, inline, user agent and essential. A link that is a whole sentence and not in a block of text must still meet the size. — [W3C Understanding 2.5.5](https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced.html)
- 3.2.4 Consistent Identification, AA: components with the same functionality within a set of pages are identified consistently. The label need not be identical, but users must be able to tell the components do the same thing. — [W3C Understanding 3.2.4](https://www.w3.org/WAI/WCAG22/Understanding/consistent-identification.html)
- 3.2.3 Consistent Navigation, AA: navigation repeated across pages appears in the same relative order each time. — [W3C Understanding 3.2.3](https://www.w3.org/WAI/WCAG22/Understanding/consistent-navigation.html)
- 2.4.3 Focus Order, Level A: when focus order affects meaning or operation, focusable components receive focus in an order that preserves meaning and operability. — [W3C Understanding 2.4.3](https://www.w3.org/WAI/WCAG22/Understanding/focus-order.html)
- 2.4.7 Focus Visible, AA: any keyboard-operable UI has a mode where the focus indicator is visible. — [W3C Understanding 2.4.7](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html)
- 2.4.11 Focus Not Obscured (Minimum), AA: a focused component must not be completely covered by author-created content. Partial overlap is allowed. This matters for sticky headers over a top-of-page Back link. — [W3C Understanding 2.4.11](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html)
- 2.4.13 Focus Appearance, AAA: the indicator's area is at least a 2 CSS px perimeter around the unfocused component, with at least 3:1 change of contrast between the focused and unfocused states. It is exempt if the user agent determines the indicator and the author doesn't modify it. — [W3C Understanding 2.4.13](https://www.w3.org/WAI/WCAG22/Understanding/focus-appearance.html)
- 1.4.11 Non-text Contrast, AA: visual information needed to identify UI components and their states needs 3:1 against adjacent colours. This includes the focus indicator against the background it appears on. — [W3C Understanding 1.4.11](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)
- 2.4.5 Multiple Ways, AA: more than one way to locate a page in a set. It does not apply to pages that are the result of, or a step in, a process. — [W3C Understanding 2.4.5](https://www.w3.org/WAI/WCAG22/Understanding/multiple-ways.html)
- GOV.UK's back link doc cites 1.4.3 (Contrast Minimum, AA) for text: on dark backgrounds the inverse link needs 4.5:1 with white. — [GOV.UK Back link](https://design-system.service.gov.uk/components/back-link/)

### Inferences
- Size: a standalone Back control is not "inline", so the inline exception doesn't apply. Give it a hit area of at least 24x24 for AA. Aim for min-height and min-width of 44px (padding, not necessarily visible chrome) to meet AAA 2.5.5.
- Label: use the same word ("Back", or the same "Back to X" pattern) wherever a back control appears, so 3.2.4 is met. Place it in the same spot on every subpage (3.2.3 concerns repeated navigation; a consistent position is the safe reading).
- Contrast: use 4.5:1 for the text label and 3:1 for any icon or border that conveys the control (an arrow alone must be 3:1). The focus ring needs 3:1 against the page background.
- Focus ring: a 2px solid ring with offset, 3:1 against the background, also gets close to the AAA 2.4.13. Don't remove the outline.
- Focus order: put the Back link early in DOM order, after skip link/header, before main content. Visual order should match DOM order.
- 2.4.11: if the header is sticky, add scroll-padding or scroll-margin so a focused Back link isn't hidden under it.
- 2.4.5 is satisfied by header nav plus the home route, not by the Back control. Don't count Back as a "way".

### Gaps
- I did not verify "Understanding" text on the sufficient techniques for back links. I did not find a WCAG criterion that requires a back control at all (none is required).

## 2. How should a visible "Back" behave for arrivals from another site, a new tab or a shared link?

### Takeaway
`history.back()` is unreliable as the only behaviour: `history.length` can't tell you whether the previous entry is on your site, and a new tab has length 1. Render a real link to a parent route (progressive enhancement) and only intercept to go back when you can establish that the prior entry is your own site.

### Cited Findings
- `history.length` counts session-history entries including the current one; a page opened in a new tab returns 1. — [MDN History.length](https://developer.mozilla.org/en-US/docs/Web/API/History/length)
- MDN's page does not discuss the caveats below. Those caveats (count includes forward entries and cross-site entries, no way to see the previous entry's origin, so `length > 1` does not mean `back()` stays on your site) came from the fetch tool's general knowledge, not from MDN text. Treat them as unverified-by-source. They are consistent with how the History API is specified (entries are not exposed).
- `Navigation.canGoBack` is true if the current entry isn't the first in the history entry list. MDN lists it as Baseline "newly available" since January 2026. MDN's page doesn't state same-origin scoping. — [MDN Navigation.canGoBack](https://developer.mozilla.org/en-US/docs/Web/API/Navigation/canGoBack)
- Next.js App Router: `router.back()` navigates to the previous route in the browser history stack; `router.push` adds an entry, `router.replace` does not. The docs recommend `<Link>` over `useRouter` for navigation unless there is a specific requirement. The docs show no no-history fallback for `back()`. — [Next.js useRouter](https://nextjs.org/docs/app/api-reference/functions/use-router)
- Android's navigation guidance is a useful analogue: after a deep link from another app, Up goes through a synthetic back stack to the app's own parent; Back returns to the other app. It says the synthetic stack should match one achievable by organic navigation. — [Android navigation principles](https://developer.android.com/guide/navigation/principles)
- Ionic's `ion-back-button` takes a `defaultHref` used when there is no history (reported in forum threads, not the official docs I fetched). — [Ionic forum thread](https://forum.ionicframework.com/t/how-to-have-a-custom-history-and-display-the-back-buttons-on-a-deep-link/158086)
- GOV.UK: the link returns the user to the previous page "in the state they last saw it" and should work without JavaScript where possible; if it can't, hide it when JavaScript is unavailable. — [GOV.UK Back link](https://design-system.service.gov.uk/components/back-link/)

### Inferences
- Recommended design for /contact: render `<Link href="/">Back</Link>` (or the real referring parent) so it works with no JS, on a shared link or in a new tab, and never exits the site. On click, optionally enhance: if the previous entry is known to be same-origin (for example, the app sets a sessionStorage or history.state flag on in-app navigation, or `navigation.canGoBack` plus a same-origin `document.referrer` check), call `preventDefault()` and `router.back()`; otherwise let the link navigate to `/`.
- Using `router.push('/')` for the fallback adds a history entry; use `router.replace` only if you intend to avoid a loop. A plain `<Link>` push is acceptable and keeps browser Back sensible (browser Back returns to /contact).
- Don't trap the user: never `replaceState` or push dummy entries to intercept the browser Back button.
- Because the hero button, header link and a direct link can all lead here, "go to previous page" yields three different results. A deterministic parent ("Home") is more predictable than history. If using history, label it "Back" and accept it returns to wherever they were.
- Hash and search-param changes on the page (if any) can add entries and make `back()` stay on /contact; avoid pushing entries for in-page UI state.

### Gaps
- Could not verify via source whether `document.referrer` is reliable across Next client navigations (it reflects the initial document load, not client-side transitions), or its cross-site truncation under Referrer-Policy. This is from general knowledge only.
- No official Next.js doc found that addresses a no-history fallback for `router.back()`. My search for a canonical web pattern returned only mobile and Ionic discussions.
- Cross-browser behaviour of `canGoBack` for entries from other origins: not verified.

## 3. Breadcrumbs or back link: when is each recommended?

### Takeaway
Breadcrumbs are for deep hierarchies; a site with one or two levels (home to /contact) doesn't need them, so a simple Back/parent link suits. GOV.UK says never combine its back link and breadcrumbs.

### Cited Findings
- NN/g: breadcrumbs are most valuable on deep sites with hierarchies and when users arrive from search or external links; they are not needed on flat sites (1-2 levels) or linear sites; they supplement rather than replace global navigation; on mobile consider shortening to the last level or two. — [NN/g Breadcrumbs](https://www.nngroup.com/articles/breadcrumbs/)
- NN/g: "Breadcrumbs are not intended to show the history of pages traversed during a session on the site." The article does not discuss back links. — [NN/g Breadcrumbs](https://www.nngroup.com/articles/breadcrumbs/)
- GOV.UK: always include the back link on question pages in a service; never combine it with breadcrumbs; default text "Back" suits simple journeys; for complex services consider "Go back to [page]". — [GOV.UK Back link](https://design-system.service.gov.uk/components/back-link/)
- Apple HIG, Material, USWDS: fetches returned only page titles or nothing useful. Not verified from the pages. The Apple fetch explanation (back button shows the previous screen's title by default in UIKit) was general knowledge only. — [Apple navigation bars/toolbars](https://developer.apple.com/design/human-interface-guidelines/navigation-bars)

### Inferences
- /contact is one level below home, so breadcrumbs add little; a "Back" or "Home" link is enough. NN/g's point that external arrivals benefit from orientation argues for a deterministic parent target, not history.
- A GOV.UK-style back link is designed for multi-step transactions; on a marketing page it is borrowed, not prescribed. The "never combine with breadcrumbs" rule is a reasonable one to follow.

### Gaps
- No NN/g article specifically on back buttons was fetched. USWDS has no verified back-link guidance from me. Apple HIG and Material text were not retrievable.

## 4. Placement and wording norms

### Takeaway
GOV.UK: top of the page, before `<main>`, default text "Back" (or "Go back to X" in complex services). A link, not a button.

### Cited Findings
- Put the back link at the top of the page, before the `<main>` element, so the skip link can bypass navigation including the back link. Examples use `href` values (placeholder `#`). — [GOV.UK Back link](https://design-system.service.gov.uk/components/back-link/)
- Automated checkers may warn that the link sits outside landmarks; GOV.UK says this does not affect WCAG compliance. — [GOV.UK Back link](https://design-system.service.gov.uk/components/back-link/)
- Inverse (white) variant for dark backgrounds with a 4.5:1 background requirement. — [GOV.UK Back link](https://design-system.service.gov.uk/components/back-link/)

### Inferences
- Link vs button: a back control that changes location is semantically a link (`<a href>` via `next/link`), which gives middle-click, open in new tab, and a no-JS fallback. A `<button>` fits only if it performs a non-navigational action. Using a link with an onClick enhancement keeps both.
- Placement: above the page heading, left-aligned, in the same place as the visible mobile tap area; on mobile ensure the 44px hit area and enough spacing from the header links.
- Label: visible text "Back" (a left-chevron icon marked `aria-hidden`, text as the accessible name). Where the target is fixed, "Back to home" is clearer than "Back" for shared-link arrivals; if the label is "Back" but the target is Home, that is mildly misleading.

### Gaps
- Mobile-specific guidance (thumb reach, top-left vs bottom): no authoritative source retrieved.

## 5. Focus management after navigating back and screen-reader announcements

### Takeaway
I did not retrieve authoritative sources for this. Only inferences are offered.

### Cited Findings
- Next.js App Router scrolls to the top by default on `router.push`/`replace`; `scroll: false` disables it. — [Next.js useRouter](https://nextjs.org/docs/app/api-reference/functions/use-router)
- Next.js `router.bfcacheId` stays the same on back/forward navigations, so state is restored on back/forward when Activity-based preservation (`cacheComponents`) is enabled. — [Next.js useRouter](https://nextjs.org/docs/app/api-reference/functions/use-router)

### Inferences
- Because the Back link is a normal link, the browser/Next handle the destination. Next's App Router has route-announcer behaviour for client navigations (from general knowledge, not fetched here). Give the /contact page a unique `<title>` and a single `<h1>` ("Talk to a founder") so the announcement is meaningful.
- On arrival at /contact, do not move focus programmatically unless necessary; if the page does, move it to the `<h1>` (tabindex="-1"), not to the Back link.
- Contact-form state: if the visitor goes Back and returns, browser history restore may keep field values; avoid clearing silently.

### Gaps
- No source fetched on focus restoration after back navigation or on Next.js route announcer details. Needs a follow-up (Next.js docs for the route announcer; Sara Soueidan / Marcy Sutton-style SPA focus guidance; W3C ARIA APG) before treating as fact.

## Recommended implementation (synthesis of above; items marked as inference where applicable)
- `<Link href="/" className="back">` containing chevron (`aria-hidden`) + visible text "Back", placed first in the page content above the h1; real href, no JS needed.
- Optional enhancement: if same-origin prior entry is known, `preventDefault()` and `router.back()`; else navigate to `/`.
- Hit area at least 44x44 (AAA 2.5.5), never below 24x24 (AA 2.5.8); text contrast 4.5:1; icon and focus ring 3:1; 2px ring with offset; scroll-margin for sticky header.
- Same label and position on any other subpage that gets one (3.2.3, 3.2.4). No breadcrumbs alongside.
