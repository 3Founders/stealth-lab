import { getSupabase } from './supabase/client';
export async function authHeaders(): Promise<Record<string, string>> {
 const client = getSupabase(); if (!client) return {};
 const {data, error} = await client.auth.getSession(); if (error) throw error;
 return data.session ? {Authorization: `Bearer ${data.session.access_token}`} : {};
}
