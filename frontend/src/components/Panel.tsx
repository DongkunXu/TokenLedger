import type { CSSProperties, ReactNode } from "react";

// A console "record": index block, code label with its localized description, a ruled
// line, and room for meta text and controls. The glass layers (grain, lit edge) sit on top.
export function Panel({ id, index, code, title, sub, meta, className = "", style, children }: {
  id: string;
  index: string; // "02"
  code: string; // "QT"
  title: string; // English code label
  sub?: string; // localized description
  meta?: ReactNode;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  return (
    <section id={id} className={`panel glass reveal ${className}`} style={style}>
      <header className="ph">
        <div className="ph-id pix">
          <b>{index}</b>
          <i>{code}</i>
        </div>
        <h2 className="ph-title">
          <span className="en">{title}</span>
          {sub && <span className="sub">{sub}</span>}
        </h2>
        <span className="rule" aria-hidden="true" />
        {meta && <div className="ph-meta">{meta}</div>}
      </header>
      {children}
      <GlassLayers />
    </section>
  );
}

export function GlassLayers() {
  return (
    <>
      <i className="grain" aria-hidden="true" />
      <i className="edge" aria-hidden="true" />
    </>
  );
}

export function Label({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`lab ${className}`}>{children}</span>;
}
