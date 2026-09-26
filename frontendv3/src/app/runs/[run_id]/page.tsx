"use client";
import { useParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { executionRun, executionRunNodes, ApiError } from '@/lib/api/client';
import { Badge, Empty, PageHead, Skeleton } from '@/components/ui';

type Row=Record<string,unknown>;
function value(row:Row,key:string,fallback='—'){const v=row[key];return typeof v==='string'||typeof v==='number'?String(v):fallback}
function State({value:status}:{value:unknown}){return <Badge>{typeof status==='string'?status:'unknown'}</Badge>}

export default function RunDetailPage(){
 const {run_id}=useParams<{run_id:string}>();
 const [run,setRun]=useState<Row|null>(null); const [nodes,setNodes]=useState<Row[]>([]); const [loading,setLoading]=useState(true); const [error,setError]=useState('');
 useEffect(()=>{let alive=true;setLoading(true);setError('');Promise.all([executionRun(run_id),executionRunNodes(run_id)]).then(([r,n])=>{if(!alive)return;setRun(r);setNodes(n.nodes??[])}).catch(e=>{if(alive)setError(e instanceof ApiError&&e.status===404?'This Run was not found.':'The connected backend could not return this Run.')}).finally(()=>{if(alive)setLoading(false)});return()=>{alive=false}},[run_id]);
 return <div className="page run-detail-page"><Link className="back-link" href="/runs">← All Runs</Link>{loading?<Skeleton/>:error?<Empty title={error}><p>Check the ID or connect the MCP backend, then try again.</p><Link className="button secondary" href="/runs">Open another Run</Link></Empty>:run&&<><PageHead eyebrow="RUN / DURABLE EXECUTION" title="A Run, kept intact."><span className="run-id-display">{run_id}</span></PageHead><div className="run-overview"><div><span className="eyebrow">STATUS</span><State value={run.status}/></div><div><span className="eyebrow">PROCEDURE</span><strong>{value(run,'procedure_id')}</strong><small>version {value(run,'procedure_version')}</small></div><div><span className="eyebrow">OUTCOME</span><strong>{value(run,'final_outcome')}</strong><small>{value(run,'final_execution_id')==='—'?'no terminal execution yet':value(run,'final_execution_id')}</small></div></div><section className="run-ledger-section"><div className="section-heading"><p className="eyebrow">NODE LEDGER</p><h2>The work, step by step.</h2><p>Each row comes from the durable run record. Pending is a real state, not a missing answer.</p></div>{nodes.length?<ol className="run-node-list">{nodes.map((n,i)=><li key={value(n,'id',String(i))}><span className="index">{String(n.node_order??i).padStart(2,'0')}</span><div><h3>{value(n,'goal',`Node ${value(n,'node_order',String(i))}`)}</h3><p className="muted mono">{value(n,'id')}</p></div><div className="node-state"><State value={n.status}/><small>attempt {value(n,'attempt_count','0')}</small></div></li>)}</ol>:<Empty title="No node rows returned."><p>The backend returned this Run but no node history.</p></Empty>}</section><section className="run-next"><p className="eyebrow">WHAT COMES NEXT</p><h2>Execution and verification stay separate.</h2><p>A successful node is an outcome. Verification is a later, evidence-backed judgment against the Procedure’s criteria.</p><div><Link href="/docs#execution">Read execution ↗</Link><Link href="/docs#verification">Read verification ↗</Link></div></section></>}</div>;
}
