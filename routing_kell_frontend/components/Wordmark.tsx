import Image from "next/image";

/** The keळ wordmark in both colourings: dark letters for light mode, light letters for dark mode. CSS shows one (globals.css, .wm-*). */
export default function Wordmark({ style, priority }: { style?: React.CSSProperties; priority?: boolean }) {
  return (
    <>
      <Image className="wm-light" src="/kel-wordmark.png" alt="keळ" width={800} height={440} priority={priority} style={style} />
      <Image className="wm-dark" src="/kel-wordmark-dark.png" alt="" aria-hidden="true" width={800} height={440} style={style} />
    </>
  );
}
