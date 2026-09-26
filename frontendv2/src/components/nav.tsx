"use client";
import { usePathname } from 'next/navigation';
export function Navigation(){const path=usePathname();return <nav aria-label="Main navigation">{[['/about','About'],['/problems','Problems'],['/search','Search'],['/docs','Docs']].map(([href,label])=><a key={href} href={href} aria-current={path===href || path.startsWith(href+'/') ? 'page':undefined}>{label}</a>)}</nav>}
