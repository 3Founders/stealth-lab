import RoomView from "@/components/chat/RoomView";
import { springVars } from "@/lib/chat/spring";
import "./chat.css";
import "./rooms.css";

export const metadata = { title: "Chat (dev sandbox)" };

export default function Page() {
  return (
    <div className="dg-page" style={springVars() as React.CSSProperties}>
      <RoomView brand={/* eslint-disable-next-line @next/next/no-img-element */ <img src="/kel-wordmark.png" alt="keळ" width={800} height={440} />} />
    </div>
  );
}
