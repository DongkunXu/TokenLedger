import { createContext, useCallback, useContext, useLayoutEffect, useRef, useState, type ReactNode } from "react";

interface Tip {
  show: (e: { clientX: number; clientY: number }, content: ReactNode) => void;
  hide: () => void;
}

const TipContext = createContext<Tip>({ show: () => {}, hide: () => {} });

export const useTooltip = () => useContext(TipContext);

// One floating tooltip for the page. Charts call show() on pointer move with their content.
export function TooltipProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ x: number; y: number; content: ReactNode } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: 0, top: 0 });

  const show = useCallback<Tip["show"]>((e, content) => setState({ x: e.clientX, y: e.clientY, content }), []);
  const hide = useCallback(() => setState(null), []);

  useLayoutEffect(() => {
    if (!state || !ref.current) return;
    const w = ref.current.offsetWidth;
    const h = ref.current.offsetHeight;
    let left = state.x + 16;
    let top = state.y + 16;
    if (left + w > window.innerWidth - 8) left = state.x - w - 16;
    if (top + h > window.innerHeight - 8) top = state.y - h - 16;
    setPos({ left: Math.max(8, left), top: Math.max(8, top) });
  }, [state]);

  return (
    <TipContext.Provider value={{ show, hide }}>
      {children}
      <div ref={ref} className={`tip ${state ? "on" : ""}`} style={pos} role="tooltip">
        {state?.content}
      </div>
    </TipContext.Provider>
  );
}

export function TipRow({ color, label, value, total = false }: { color?: string; label: ReactNode; value: ReactNode; total?: boolean }) {
  return (
    <div className={`tip-row ${total ? "total" : ""}`}>
      <span>
        {color && <i style={{ background: color }} />}
        {label}
      </span>
      <b>{value}</b>
    </div>
  );
}
