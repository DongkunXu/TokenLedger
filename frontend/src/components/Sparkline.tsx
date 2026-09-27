import { useId } from "react";

interface Props {
  values: number[];
  color: string; // CSS colour, e.g. "var(--codex)"
  mark?: number; // index to highlight with a vertical tick
  kind?: "line" | "bars";
}

const W = 240;
const H = 60;
const PAD = 3;

// Decorative background trend for a KPI card. Stretches to the card; purely visual.
export function Sparkline({ values, color, mark, kind = "line" }: Props) {
  const id = useId().replace(/:/g, "");
  if (values.length < 2) return null;
  const max = Math.max(...values) || 1;
  const x = (i: number) => (i / (values.length - 1)) * W;
  const y = (v: number) => H - PAD - (v / max) * (H - PAD * 2);

  if (kind === "bars") {
    // Height follows the square root of each day's value: active days read clearly, big days still stand out.
    const bw = W / values.length;
    const root = Math.sqrt(max);
    return (
      <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
        {values.map((v, i) => (
          <rect
            key={i}
            x={i * bw + bw * 0.18}
            y={H - (v > 0 ? Math.max(8, (Math.sqrt(v) / root) * (H - 4)) : 5)}
            width={bw * 0.46}
            height={v > 0 ? Math.max(8, (Math.sqrt(v) / root) * (H - 4)) : 5}
            rx={1.5}
            fill={color}
            opacity={v > 0 ? 0.32 : 0.1}
          />
        ))}
      </svg>
    );
  }

  const pts = values.map((v, i) => [x(i), y(v)] as const);
  let line = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1];
    const [x1, y1] = pts[i];
    const mx = (x0 + x1) / 2;
    line += ` C${mx},${y0} ${mx},${y1} ${x1},${y1}`;
  }
  const area = `${line} L${W},${H} L0,${H} Z`;
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id={`g${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#g${id})`} />
      <path d={line} fill="none" stroke={color} strokeOpacity="0.75" strokeWidth="1.3" vectorEffect="non-scaling-stroke" />
      {mark != null && pts[mark] && (
        // A vertical tick keeps its shape when the SVG stretches (a circle would turn into an ellipse).
        <line
          x1={pts[mark][0]}
          x2={pts[mark][0]}
          y1={pts[mark][1]}
          y2={H}
          stroke={color}
          strokeOpacity="0.7"
          strokeWidth="1.2"
          strokeDasharray="2 2"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}

// Sum per bucket so long ranges stay readable (about 60 points at most).
export function bucket(values: number[], maxPoints = 60): number[] {
  if (values.length <= maxPoints) return values;
  const size = Math.ceil(values.length / maxPoints);
  const out: number[] = [];
  for (let i = 0; i < values.length; i += size) out.push(values.slice(i, i + size).reduce((a, b) => a + b, 0));
  return out;
}
