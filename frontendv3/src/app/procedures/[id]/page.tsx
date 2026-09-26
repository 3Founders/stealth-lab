import {Procedure} from '@/components/procedure';
export const metadata={title:'Procedure'};
export default async function Page({params}:{params:Promise<{id:string}>}){const {id}=await params;return <div className="page"><a href="/search" className="back-link">← Search the library</a><Procedure id={id}/></div>}
