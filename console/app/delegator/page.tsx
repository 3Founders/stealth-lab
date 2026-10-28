import RoomView from "@/components/delegator/RoomView";
import "./delegator.css";

export const metadata = { title: "Delegator (dev sandbox)" };

export default function Page() {
  return (
    <div className="dg-page">
      <div className="dg-top">
        <div className="marker"><b>DELEGATOR</b>dev sandbox</div>
        <p>Runs in this browser tab only. The relay keeps no history, and nothing leaves this machine.</p>
      </div>
      <RoomView />
    </div>
  );
}
