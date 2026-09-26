import {PageHead} from '@/components/ui';
import {Account} from '@/components/account';
export const metadata={title:'Account',robots:{index:false,follow:false}};
export default function Page(){return <div className="page"><PageHead eyebrow="YOUR ACCOUNT" title="Knowledge grows with you.">Your identity, contributions, and the things you helped make better.</PageHead><Account/></div>}
