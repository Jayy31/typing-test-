"use client";
/** Recharts wrappers following the dataviz method: one axis, 2px lines, recessive grid,
 * hover tooltip by default, text in ink tokens (never the series colour). */
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

const axis = { stroke: "var(--axis)", tick: { fill: "var(--muted)", fontSize: 11 }, tickLine: false };
const tooltip = {
  contentStyle: { background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, color: "var(--ink)" },
  labelStyle: { color: "var(--ink-2)" },
  cursor: { stroke: "var(--axis)", strokeWidth: 1 },
};

export interface Pt { x: string; y: number }

export function LineSeries({ data, height = 180, unit = "", refs = [], label }: {
  data: Pt[]; height?: number; unit?: string; refs?: { y: number; label: string }[]; label: string;
}) {
  return (
    <figure aria-label={label}>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="x" {...axis} minTickGap={24} />
          <YAxis {...axis} width={56} domain={["auto", "auto"]} tickFormatter={(v: number) => `${v.toLocaleString("en-IN")}${unit}`} />
          {refs.map((r) => (
            <ReferenceLine key={r.label} y={r.y} stroke="var(--axis)" strokeDasharray="4 4"
              label={{ value: r.label, position: "insideTopRight", fill: "var(--ink-2)", fontSize: 10 }} />
          ))}
          <Tooltip {...tooltip} formatter={(v) => [`${Number(v).toLocaleString("en-IN")}${unit}`, label]} />
          <Line type="monotone" dataKey="y" stroke="var(--s1)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </figure>
  );
}

export function Bars({ data, height = 180, unit = "", label, signed = false }: {
  data: Pt[]; height?: number; unit?: string; label: string; signed?: boolean;
}) {
  return (
    <figure aria-label={label}>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }} barCategoryGap={4}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="x" {...axis} interval={0} tick={{ fill: "var(--muted)", fontSize: 10 }} />
          <YAxis {...axis} width={48} tickFormatter={(v: number) => `${v}${unit}`} />
          <ReferenceLine y={0} stroke="var(--axis)" />
          <Tooltip {...tooltip} cursor={{ fill: "var(--grid)" }} formatter={(v) => [`${Number(v).toFixed(1)}${unit}`, label]} />
          <Bar dataKey="y" radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {data.map((d) => (
              <Cell key={d.x} fill={signed && d.y < 0 ? "var(--s2)" : "var(--s1)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </figure>
  );
}

/** Reliability diagram: predicted vs observed frequency, with the perfect-calibration diagonal. */
export function Reliability({ data, height = 220 }: { data: { p: number; obs: number; n: number }[]; height?: number }) {
  const pts = data.map((d) => ({ x: `${Math.round(d.p * 100)}%`, y: Math.round(d.obs * 100), ideal: Math.round(d.p * 100), n: d.n }));
  return (
    <figure aria-label="Reliability: predicted vs observed">
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={pts} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="x" {...axis} />
          <YAxis {...axis} width={52} domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} />
          <Tooltip {...tooltip} formatter={(v, name) => [`${v}%`, name === "y" ? "observed" : "perfectly calibrated"]} />
          <Line dataKey="ideal" stroke="var(--axis)" strokeDasharray="4 4" dot={false} strokeWidth={1.5} isAnimationActive={false} />
          <Line dataKey="y" stroke="var(--s1)" strokeWidth={2} dot={{ r: 4, fill: "var(--s1)", stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </figure>
  );
}
