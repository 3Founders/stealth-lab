import fs from 'node:fs';
import path from 'node:path';
export interface Document {slug:string;title:string;file:string;category:string;}
export const documents:Document[]=[
{slug:'terms',title:'Terms of Service',file:'docs/legal/TERMS_OF_SERVICE.md',category:'Legal'},
{slug:'privacy',title:'Privacy Policy',file:'docs/legal/PRIVACY_POLICY.md',category:'Privacy'},
{slug:'acceptable-use',title:'Acceptable Use Policy',file:'docs/legal/ACCEPTABLE_USE_POLICY.md',category:'Policies'},
{slug:'global-commons',title:'Global Commons Terms',file:'docs/legal/GLOBAL_COMMONS_TERMS.md',category:'Contributions'},
{slug:'verification',title:'Verification Disclaimer',file:'docs/legal/VERIFICATION_DISCLAIMER.md',category:'Knowledge'},
{slug:'copyright',title:'Copyright & Third-Party Sources',file:'docs/legal/COPYRIGHT_AND_THIRD_PARTY_SOURCES.md',category:'Legal'},
{slug:'cookies',title:'Cookies & Tracking',file:'docs/legal/COOKIES_AND_TRACKING.md',category:'Privacy'},
{slug:'subprocessors',title:'Subprocessors',file:'docs/legal/SUBPROCESSORS.md',category:'Privacy'},
{slug:'security',title:'Security Overview',file:'docs/legal/SECURITY_OVERVIEW.md',category:'Security'},
{slug:'audit',title:'Policy Documentation Audit',file:'docs/legal/AUDIT_REPORT.md',category:'Policies'},
{slug:'data-statement',title:'Data Statement',file:'DATA_STATEMENT.md',category:'Privacy'},
{slug:'security-model',title:'Engineering Security Model',file:'SECURITY.md',category:'Security'},
{slug:'mcp',title:'MCP Connection Guide',file:'backend/README_MCP_SERVER.md',category:'Product'},
];
export function readDocument(slug:string){const doc=documents.find(d=>d.slug===slug);if(!doc)return null;const content=fs.readFileSync(path.resolve(process.cwd(),'..',doc.file),'utf8');const date=content.match(/(?:Generated from repository state on|Last updated:|\*\*Date:\*\*)\s*(\d{4}-\d{2}-\d{2})/i)?.[1] || null;return {...doc,content,date};}
export function headingId(s:string){return s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu,'-').replace(/^-|-$/g,'')}
export function documentLink(target:string){if(/^https?:\/\//.test(target)||target.startsWith('#'))return target;const filename=target.split('/').pop()?.split('#')[0];const found=documents.find(d=>d.file.split('/').pop()===filename);return found?'/docs/'+found.slug:undefined;}
