"use client";
import { useEffect, useRef, useState } from "react";

// The film, centred under the hero. It plays (muted, as browsers require for autoplay) while at least half of it is on
// screen and pauses when scrolled away. Once the person pauses it themselves, scrolling no longer restarts it.
export default function FilmPlayer() {
  const box = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const userPaused = useRef(false);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(true);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const v = video.current, el = box.current;
    if (!v || !el) return;
    // how much of the film is on screen, checked on scroll and resize (one cheap measurement each time)
    const check = () => {
      const r = el.getBoundingClientRect();
      const seen = Math.max(0, Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0)) / (r.height || 1);
      if (seen >= 0.5) { if (v.paused && !userPaused.current) v.play().catch(() => { /* the play button still works */ }); }
      else if (!v.paused) v.pause();
    };
    check();
    window.addEventListener("scroll", check, { passive: true });
    window.addEventListener("resize", check);
    return () => {
      window.removeEventListener("scroll", check);
      window.removeEventListener("resize", check);
    };
  }, []);

  const toggle = () => {
    const v = video.current;
    if (!v) return;
    if (v.paused) { userPaused.current = false; v.play().catch(() => {}); }
    else { userPaused.current = true; v.pause(); }
  };
  const toggleMute = () => {
    const v = video.current;
    if (!v) return;
    v.muted = !v.muted;
    setMuted(v.muted);
  };
  const fullscreen = () => {
    const v = video.current as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null;
    if (!v) return;
    if (document.fullscreenElement) document.exitFullscreen();
    else if (box.current?.requestFullscreen) box.current.requestFullscreen();
    else v.webkitEnterFullscreen?.();
  };
  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const v = video.current;
    if (!v || !v.duration) return;
    const r = e.currentTarget.getBoundingClientRect();
    v.currentTime = ((e.clientX - r.left) / r.width) * v.duration;
  };

  return (
    <section className="film-wrap frame" aria-label="The keळ film">
      <div className="film-box" ref={box}>
        <video
          ref={video} src="/films/kel-film.mp4" poster="/films/kel-film.jpg" muted playsInline preload="metadata"
          onClick={toggle} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
          onTimeUpdate={(e) => setProgress(e.currentTarget.currentTime / (e.currentTarget.duration || 1))}
        />
        <div className="film-bar">
          <button type="button" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>{playing ? "❚❚" : "▶"}</button>
          <div className="film-progress" onClick={seek} role="presentation"><i style={{ width: `${progress * 100}%` }} /></div>
          <button type="button" onClick={toggleMute} aria-label={muted ? "Unmute" : "Mute"}>{muted ? "Sound off" : "Sound on"}</button>
          <button type="button" onClick={fullscreen} aria-label="Full screen">⛶</button>
        </div>
      </div>
    </section>
  );
}
