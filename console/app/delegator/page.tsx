import RoomView from "@/components/delegator/RoomView";

export const metadata = { title: "Delegator (dev sandbox)" };

export default function Page() {
  return (
    <div className="frame grid console-main">
      <header className="console-head">
        <div className="marker caption"><b>DELEGATOR</b><span>/ dev sandbox, this browser only</span></div>
        <h1>Group chat for people and agents.</h1>
      </header>
      <RoomView />
    </div>
  );
}
