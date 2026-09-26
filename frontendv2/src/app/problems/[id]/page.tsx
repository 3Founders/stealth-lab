import {ProblemDetail} from '@/components/problems';
export const metadata={title:'Problem & evidence'};
export default async function Page({params}:{params:Promise<{id:string}>}){const {id}=await params;return <div className="page"><a className="back-link" href="/problems">← All problems</a><ProblemDetail id={id}/></div>}
