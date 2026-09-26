"use client";
import { useState } from 'react';
import { PageHead } from '@/components/ui';

export default function RunsPage(){
 const [runId,setRunId]=useState('');
 return <div className="page runs-page">
  <PageHead eyebrow="THE LIBRARY / RUNS" title="What happened when a way was tried.">Open a durable Run to see its nodes, attempts, evidence, and verification state. A Run is kept as it happened.</PageHead>
  <section className="run-open-panel" aria-labelledby="run-open-title">
   <div><p className="eyebrow">OPEN A DURABLE RUN</p><h2 id="run-open-title">Bring the run ID.</h2><p>Run IDs come back from <code>find_best_way</code>, <code>reproduce_procedure</code>, or a host-executed <code>plan_only</code> call.</p></div>
   <form onSubmit={e=>e.preventDefault()} className="run-open-form"><label htmlFor="run-id">Execution run ID</label><div className="run-open-row"><input id="run-id" value={runId} onChange={e=>setRunId(e.target.value)} placeholder="87d7142b-7f75-4a74-8553-8e64b1cde055" spellCheck={false} autoComplete="off"/><a className={`button ${runId.trim()?'':'secondary'}`} href={runId.trim()?`/runs/${encodeURIComponent(runId.trim())}`:'/runs'} aria-disabled={!runId.trim()}>Open ↗</a></div><p className="muted">The page reads the connected backend. It never fills in a run the backend cannot return.</p></form>
  </section>
  <section className="run-objects" aria-labelledby="run-objects-title"><div className="section-heading"><p className="eyebrow">THE RUN RECORD</p><h2 id="run-objects-title">One attempt, kept intact.</h2><p>Execution, evidence, and verification remain distinct objects.</p></div><div className="three-columns">{[['01','Run','One concrete attempt against a pinned Procedure version.'],['02','Nodes','Ordered work, dependencies, leases, attempts, and outcomes.'],['03','Verification','Whether the outcome met the Procedure’s explicit criteria.']].map(([n,t,d])=><article className="principle" key={t}><span className="index">{n}</span><h3>{t}</h3><p>{d}</p></article>)}</div></section>
 </div>;
}
