import { useEffect, useState } from "react";

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Digits settle left to right from random digits whenever `text` changes.
export function useScramble(text: string, duration = 700): string {
  const [shown, setShown] = useState(text);
  useEffect(() => {
    if (reducedMotion()) {
      setShown(text);
      return;
    }
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const k = Math.min(1, (now - start) / duration);
      const fixed = Math.floor(k * text.length);
      setShown([...text].map((c, i) => (i < fixed || !/\d/.test(c) ? c : String(Math.floor(Math.random() * 10)))).join(""));
      if (k < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [text, duration]);
  return shown;
}

// Glass panels light up the part of their edge nearest the pointer. One listener for the
// page writes the pointer position, relative to each panel, into --mx / --my.
export function usePointerLight(selector = ".glass") {
  useEffect(() => {
    let frame = 0;
    let x = 0;
    let y = 0;
    const paint = () => {
      frame = 0;
      document.querySelectorAll<HTMLElement>(selector).forEach((el) => {
        const r = el.getBoundingClientRect();
        el.style.setProperty("--mx", `${x - r.left}px`);
        el.style.setProperty("--my", `${y - r.top}px`);
      });
    };
    const onMove = (e: PointerEvent) => {
      x = e.clientX;
      y = e.clientY;
      if (!frame) frame = requestAnimationFrame(paint);
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      window.removeEventListener("pointermove", onMove);
      cancelAnimationFrame(frame);
    };
  }, [selector]);
}
