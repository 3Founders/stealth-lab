"use client";
import {useEffect,useState} from 'react';
import {ApiError} from '@/lib/api/client';
import {Skeleton, Empty} from './ui';
export function Resource<T>({load,children}:{load:()=>Promise<T>;children:(value:T)=>React.ReactNode}){
 const [state,setState]=useState<{value?:T;error?:unknown}>({}); const [attempt,setAttempt]=useState(0);
 useEffect(()=>{let active=true;load().then(value=>{if(active)setState({value})}).catch(error=>{if(active)setState({error})});return()=>{active=false}},[load,attempt]);
 function retry(){setState({});setAttempt(n=>n+1)}
 if(state.error){const status=state.error instanceof ApiError?state.error.status:0;return <div role="alert"><Empty title={status===401?'Sign in to keep going.':status===403?'This knowledge isn’t shared with you.':status===404?'We couldn’t find that.':'The library is out of reach.'}><p>{status===401?'Your account keeps your contributions together.':status===403?'Your account does not have permission to view this content.':status===404?'It may have moved, or may not be available to your account.':'Check your connection and try again. Your question is still here.'}</p><div className="actions">{status===401?<a className="button" href="/account">Sign in ↗</a>:<button className="button" onClick={retry}>Try again</button>}<a href="/setup">Connection setup ↗</a></div></Empty></div>}
 if(state.value===undefined)return <Skeleton/>;return <>{children(state.value)}</>;
}
