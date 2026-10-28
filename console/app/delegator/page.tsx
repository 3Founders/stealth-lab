import RoomView from "@/components/delegator/RoomView";
import { springVars } from "@/lib/delegator/spring";
import "./delegator.css";

export const metadata = { title: "Delegator (dev sandbox)" };

export default function Page() {
  return (
    <div className="dg-page" style={springVars() as React.CSSProperties}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img className="dg-logo" src="/kel-wordmark.png" alt="keळ" width={800} height={440} />
      <RoomView />
    </div>
  );
}
