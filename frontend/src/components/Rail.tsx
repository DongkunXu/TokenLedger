import { useEffect, useState } from "react";
import { GlassLayers } from "./Panel";

// Fixed left rail: the logo mark and one index link per section; the section in view is lit.
export function Rail({ sections, version }: { sections: { id: string; index: string; title: string }[]; version: string }) {
  const [active, setActive] = useState(sections[0]?.id);
  useEffect(() => {
    const onScroll = () => {
      let current = sections[0]?.id;
      for (const s of sections) {
        const el = document.getElementById(s.id);
        if (el && el.getBoundingClientRect().top < window.innerHeight * 0.35) current = s.id;
      }
      setActive(current);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [sections]);

  return (
    <aside className="rail glass">
      <Logo />
      <nav>
        {sections.map((s) => (
          <a key={s.id} href={`#${s.id}`} className={`pix ${s.id === active ? "on" : ""}`} title={s.title}>
            {s.index}
          </a>
        ))}
      </nav>
      <Dots pattern="11101101" />
      <span className="grow" />
      <Dots pattern="101111" />
      <span className="ver pix">Ledger v{version}</span>
      <GlassLayers />
    </aside>
  );
}

export function Logo() {
  // 4x4 pixel mark: ink squares with one orange and one blue cell
  const cells = "1101 1020 3101 1010".replace(/ /g, "");
  return (
    <div className="logo" aria-hidden="true">
      {[...cells].map((c, i) => (
        <i key={i} className={["o", "", "a", "b"][Number(c)]} />
      ))}
    </div>
  );
}

function Dots({ pattern }: { pattern: string }) {
  return (
    <div className="dots" aria-hidden="true">
      {[...pattern].map((c, i) => (
        <i key={i} className={c === "0" ? "o" : ""} />
      ))}
    </div>
  );
}
