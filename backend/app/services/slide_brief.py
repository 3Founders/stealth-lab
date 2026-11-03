"""A design brief and a working skeleton for slide decks, handed to the cheap model that writes them (room_agent.call_model, kind=html).

A small model asked for "aesthetic slides" writes plain boxes and placeholder pictures. Given a finished skeleton (type scale, palette, layouts, keyboard
and touch navigation, a progress bar) it only has to fill in words, which is the part it is good at. The skeleton uses system fonts and inline SVG only:
the deck is one self-contained file that runs in the canvas's sandbox with no network.
"""
from __future__ import annotations

import re

_SKELETON = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DECK TITLE</title>
<style>
:root{--bg:#0f1115;--ink:#f4f1ea;--mute:#a8a49a;--accent:#f5c542;--card:#181b22;--rule:#2a2e37;
  --font:ui-sans-serif,system-ui,-apple-system,"Segoe UI","Helvetica Neue",Roboto,sans-serif}
*{box-sizing:border-box;margin:0}
html,body{height:100%;background:var(--bg);color:var(--ink);font-family:var(--font)}
.deck{height:100vh;overflow-y:auto;scroll-snap-type:y mandatory;scroll-behavior:smooth}
.slide{min-height:100vh;scroll-snap-align:start;display:flex;flex-direction:column;justify-content:center;padding:7vh 9vw;position:relative;
  background:radial-gradient(1200px 600px at 85% -10%,rgba(245,197,66,.10),transparent 60%),var(--bg)}
.slide:nth-child(even){background:radial-gradient(900px 500px at 10% 110%,rgba(245,197,66,.08),transparent 60%),#12151b}
.kicker{font-size:clamp(12px,1.1vw,16px);letter-spacing:.18em;text-transform:uppercase;color:var(--accent);margin-bottom:2.2vh}
h1{font-size:clamp(40px,7vw,104px);line-height:1.02;letter-spacing:-.03em;font-weight:760;max-width:14ch}
h2{font-size:clamp(30px,4.4vw,64px);line-height:1.08;letter-spacing:-.02em;font-weight:720;max-width:20ch;margin-bottom:3vh}
p,li{font-size:clamp(17px,1.7vw,26px);line-height:1.5;color:var(--mute);max-width:46ch}
ul{list-style:none;display:grid;gap:1.6vh;padding:0}
li{padding-left:1.4em;position:relative}li::before{content:"";position:absolute;left:0;top:.62em;width:.55em;height:.55em;border-radius:50%;background:var(--accent)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:5vw;align-items:center}
.card{background:var(--card);border:1px solid var(--rule);border-radius:20px;padding:3vh 2.4vw}
.card b{display:block;color:var(--ink);font-size:clamp(18px,1.9vw,28px);margin-bottom:.8vh}
.big{font-size:clamp(64px,13vw,200px);font-weight:780;letter-spacing:-.04em;color:var(--accent);line-height:.95}
blockquote{font-size:clamp(26px,3.6vw,54px);line-height:1.2;letter-spacing:-.015em;color:var(--ink);max-width:24ch;border-left:4px solid var(--accent);padding-left:2vw}
.num{position:absolute;right:4vw;bottom:4vh;color:var(--mute);font-size:14px;letter-spacing:.1em}
.bar{position:fixed;left:0;top:0;height:3px;background:var(--accent);width:0;z-index:9;transition:width .2s}
svg{max-width:100%;max-height:44vh;height:auto}
h1,h2{margin-bottom:2.6vh}.cols svg{width:100%}
@media(max-width:800px){.cols{grid-template-columns:1fr}.slide{padding:6vh 7vw}}
</style></head>
<body><div class="bar" id="bar"></div>
<main class="deck" id="deck">
<!-- 1: title -->
<section class="slide"><div class="kicker">TOPIC</div><h1>Title of the deck</h1><p>One line that says what this is about.</p></section>
<!-- 2: bullets -->
<section class="slide"><div class="kicker">SECTION</div><h2>A claim, not a label</h2><ul><li>Short point</li><li>Short point</li><li>Short point</li></ul></section>
<!-- 3: two columns, text and an inline SVG diagram -->
<section class="slide"><h2>Heading</h2><div class="cols"><p>Explanation in two sentences.</p><svg viewBox="0 0 400 240" role="img" aria-label="diagram"><rect x="20" y="40" width="130" height="70" rx="14" fill="#181b22" stroke="#f5c542"/><rect x="250" y="130" width="130" height="70" rx="14" fill="#181b22" stroke="#a8a49a"/><path d="M150 75 C 210 75 190 165 250 165" fill="none" stroke="#f5c542" stroke-width="3"/></svg></div></section>
<!-- 4: three cards -->
<section class="slide"><h2>Three things</h2><div class="cols" style="grid-template-columns:repeat(3,1fr)"><div class="card"><b>One</b><p>Short.</p></div><div class="card"><b>Two</b><p>Short.</p></div><div class="card"><b>Three</b><p>Short.</p></div></div></section>
<!-- 5: one big number -->
<section class="slide"><div class="big">92%</div><p>What the number means, in one sentence.</p></section>
<!-- 6: a quote or the takeaway -->
<section class="slide"><blockquote>The one sentence to remember.</blockquote></section>
</main>
<script>
const deck=document.getElementById('deck'),bar=document.getElementById('bar'),slides=[...deck.children];
slides.forEach((s,i)=>{const n=document.createElement('div');n.className='num';n.textContent=(i+1)+' / '+slides.length;s.appendChild(n)});
const at=()=>Math.round(deck.scrollTop/deck.clientHeight);
const go=d=>deck.scrollTo({top:Math.max(0,Math.min(slides.length-1,at()+d))*deck.clientHeight});
deck.addEventListener('scroll',()=>{bar.style.width=(deck.scrollTop/(deck.scrollHeight-deck.clientHeight||1)*100)+'%'});
addEventListener('keydown',e=>{if(['ArrowDown','ArrowRight','PageDown',' '].includes(e.key)){e.preventDefault();go(1)}if(['ArrowUp','ArrowLeft','PageUp'].includes(e.key)){e.preventDefault();go(-1)}});
</script></body></html>"""

BRIEF = (
    "You are making a slide deck as ONE complete, self-contained HTML file. Start from the skeleton below and keep its CSS, layout classes and script "
    "exactly; replace the sample slides with 8 to 10 slides of real, specific content about the topic (title; a claim per slide, not a label; at most "
    "four short points; a diagram, a big number, a comparison, a quote, a closing takeaway). Vary the layouts (bullets, two columns, three cards, big "
    "number, quote, SVG diagram). Replace TOPIC and SECTION with real words. Draw any picture as inline SVG (always with a viewBox, inside a .cols or a .card, never as a direct child of .slide and never larger than the text beside it) that illustrates the idea (shapes, arrows, simple icons in the palette) - never a grey "
    "placeholder box and never an external image. Keep the dark palette with its one yellow accent. No external fonts, scripts or files. Output only the "
    "HTML document, starting with <!doctype html>, nothing before or after it.\n\nSKELETON:\n"
)

_SLIDES = re.compile(r"\b(slides?|decks?|presentation|pitch)\b", re.I)


def wants_slides(prompt: str, title: str = "") -> bool:
    return bool(_SLIDES.search(f"{title} {prompt}"))


def slide_prompt(prompt: str) -> str:
    """The brief, the skeleton and then the person's request."""
    return f"{BRIEF}{_SKELETON}\n\nTHE REQUEST:\n{prompt.strip()}"
