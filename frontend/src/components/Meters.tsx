// Pixel-grid meters shared by several panels.

// A row of `n` square segments; the first `fraction` of them are lit. An optional marker
// (0-1) sits above the row.
export function Segs({ fraction, n, color, marker, markerTitle, className = "", delay = 0.3 }: {
  fraction: number;
  n: number;
  color: string;
  marker?: number | null;
  markerTitle?: string;
  className?: string;
  delay?: number;
}) {
  const lit = fraction > 0 ? Math.max(1, Math.ceil(Math.min(1, fraction) * n)) : 0;
  return (
    <div className={`segs ${className}`} style={{ gridTemplateColumns: `repeat(${n}, 1fr)`, ["--c" as string]: color }}>
      {Array.from({ length: n }, (_, i) => (
        <i key={i} className={i < lit ? "on" : undefined} style={i < lit ? { animationDelay: `${delay + i * 0.02}s` } : undefined} />
      ))}
      {marker != null && <span className="seg-mark" style={{ left: `${Math.min(100, marker * 100)}%` }} title={markerTitle} />}
    </div>
  );
}

// Columns of stacked squares: each value lights a share of `rows` squares, scaled to the
// largest value. Unlit squares stay as dim slots, like an LED matrix.
export function PixelColumns({ values, rows, color, cell = 4, gap = 1, highlightLast = false, label }: {
  values: number[];
  rows: number;
  color: string;
  cell?: number;
  gap?: number;
  highlightLast?: boolean;
  label: string;
}) {
  const max = Math.max(...values, 1e-9);
  const pitch = cell + gap;
  const width = values.length * pitch;
  const height = rows * pitch;
  return (
    <svg className="pixcols" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={label}>
      {values.flatMap((v, i) => {
        const lit = v > 0 ? Math.max(1, Math.round((v / max) * rows)) : 0;
        const dim = highlightLast && i < values.length - 1;
        return Array.from({ length: rows }, (_, r) => (
          <rect
            key={`${i}-${r}`}
            x={i * pitch}
            y={height - (r + 1) * pitch}
            width={cell}
            height={cell}
            className={r < lit ? "lit" : "slot"}
            fill={r < lit ? color : undefined}
            opacity={r < lit && dim ? 0.55 : undefined}
            style={r < lit ? { animationDelay: `${0.4 + i * 0.02 + r * 0.01}s` } : undefined}
          />
        ));
      })}
    </svg>
  );
}
