import {PageHead,SearchForm} from '@/components/ui';
import {Problems,Contributors} from '@/components/problems';
export const metadata={title:'Problems'};
export default async function Page({searchParams}:{searchParams:Promise<{q?:string}>}){const p=await searchParams;const q=typeof p.q==='string'?p.q:'';return <div className="page"><PageHead eyebrow="THE COMMONS / PROBLEMS" title="Shared problems. Better answers.">Explore ways of solving a problem. See what has been tested, what holds up, and what could be better.</PageHead><SearchForm action="/problems" value={q} label="Find a problem" placeholder="Find a problem…"/><Problems key={q} query={q}/><Contributors/></div>}
