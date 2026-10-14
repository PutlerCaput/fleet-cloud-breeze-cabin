import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import type { Point } from "@/lib/desk-store";

export function EquityChart({ data }: { data: Point[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <YAxis hide domain={["dataMin - 2", "dataMax + 2"]} />
        <Tooltip
          contentStyle={{
            background: "#181b18",
            border: "1px solid #2e342c",
            borderRadius: 8,
            color: "#e8ebe4",
            fontFamily: "IBM Plex Mono, ui-monospace, monospace",
            fontSize: 12,
          }}
          formatter={(value) => [`${Number(value).toFixed(2)} USDT`, "Капитал"]}
        />
        <Area
          type="monotone"
          dataKey="equity"
          stroke="#d6a24a"
          fill="#d6a24a"
          fillOpacity={0.16}
          strokeWidth={2}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
