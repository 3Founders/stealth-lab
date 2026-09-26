import { PageHead, SearchForm } from '@/components/ui';
import { SearchResults } from '@/components/search-results';
export const metadata={title:'Search',description:'Find a reusable way to solve your next problem.'};
export default async function Page({searchParams}:{searchParams:Promise<{q?:string}>}){const params=await searchParams;const q=typeof params.q==='string'?params.q.trim().slice(0,1000):'';return <div className="page search-page"><PageHead eyebrow="THE LIBRARY / SEARCH" title="Find a way that works.">A better starting point, with the evidence to back it up.</PageHead><SearchForm value={q}/><noscript>Enable JavaScript to search the live library. Documentation remains available.</noscript><SearchResults key={q} query={q}/></div>}
