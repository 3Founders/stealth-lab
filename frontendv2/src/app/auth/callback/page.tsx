"use client";
import {useEffect,useState} from 'react';
import {getSupabase} from '@/lib/supabase/client';
import {Skeleton} from '@/components/ui';
export default function Callback(){const [error,setError]=useState('');useEffect(()=>{const c=getSupabase();if(!c)return;let active=true;c.auth.getSession().then(({data,error})=>{if(!active)return;if(error||!data.session)setError('Sign-in could not be completed. Please try again.');else window.location.replace('/account')});return()=>{active=false}},[]);return <div className="page"><h1>Completing sign-in.</h1>{error?<p role="alert">{error} <a href="/account">Back to sign-in</a></p>:<Skeleton/>}</div>}
