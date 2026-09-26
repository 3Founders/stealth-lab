import { authHeaders } from '../auth';
import type { BestWay, Problem, Leaderboard, Evaluation } from './models';
import type { SolutionSearchResponse, ProcedureDetail, MeResponse, Evidence } from './types';
export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }
export async function request<T>(path: string, method: 'GET' | 'PUT' = 'GET', body?: unknown): Promise<T> {
 const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 15000);
 try { const headers = await authHeaders();
 const response = await fetch(`${API_URL}${path}`, { method, headers: {...headers, ...(body === undefined ? {} : {'Content-Type':'application/json'})}, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal, cache:'no-store', credentials:'omit' });
 if (!response.ok) throw new ApiError(response.status, 'Request failed'); return await response.json() as T;
 } catch (error) { if (error instanceof ApiError) throw error; throw new ApiError(0, 'Unable to reach the knowledge library.'); } finally { clearTimeout(timer); }
}
const id = encodeURIComponent;
export const listProblems = async (q = '') => (await request<{problems:Problem[]}>(q ? `/v1/problems/find?q=${id(q)}&limit=50` : '/v1/problems?limit=200')).problems;
export const bestWay = (q: string) => request<BestWay>(`/v1/best-way?goal=${id(q)}`);
export const search = (q: string) => request<SolutionSearchResponse>(`/v1/solutions/search?q=${id(q)}&limit=20`);
export const problem = (key: string) => request<Problem>(`/v1/problems/${id(key)}`);
export const leaderboard = (key: string) => request<Leaderboard>(`/v1/problems/${id(key)}/leaderboard`);
export const evaluations = async (key: string) => (await request<{evaluations:Evaluation[]}>(`/v1/problems/${id(key)}/evaluations`)).evaluations;
export const procedure = (key: string) => request<ProcedureDetail>(`/v1/procedures/${id(key)}`);
export const evidence = (key: string) => request<Evidence[]>(`/v1/procedures/${id(key)}/evidence`);
export const me = () => request<MeResponse>('/v1/me');
