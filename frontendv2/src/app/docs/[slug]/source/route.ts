import {readDocument} from '@/lib/documents';
export async function GET(_request:Request,{params}:{params:Promise<{slug:string}>}){const d=readDocument((await params).slug);return new Response(d?.content || 'Document not found',{status:d?200:404,headers:{'Content-Type':'text/plain; charset=utf-8','X-Content-Type-Options':'nosniff'}})}
