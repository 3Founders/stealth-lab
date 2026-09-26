"use client";
import {useState} from 'react';
export function Copy({text,label='Copy configuration'}:{text?:string;label?:string}){const [status,setStatus]=useState('');return <span className="copy-control"><button className="button secondary small" onClick={async()=>{try{await navigator.clipboard.writeText(text??window.location.href);setStatus('Copied.')}catch{setStatus('Could not copy. Select the text and copy it manually.')}}}>{label}</button><span role="status">{status}</span></span>}
