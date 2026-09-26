"use client";
export default function ErrorPage({reset}:{reset:()=>void}){return <div className="page empty" role="alert"><p className="eyebrow">SOMETHING WENT WRONG</p><h1>Let’s try that again.</h1><p>We couldn’t load this page.</p><button className="button" onClick={reset}>Try again</button><a href="/">Back home</a></div>}
