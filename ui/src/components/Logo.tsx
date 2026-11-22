import { cn } from "@/lib/utils";

/** Two overlapping squares containing "mh". */
export function LogoMark({ className, size = 32 }: { className?: string; size?: number }) {
  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label="mhsquare logo"
    >
      <rect
        x="9"
        y="9"
        width="32"
        height="32"
        rx="6"
        fill="none"
        strokeWidth="4"
        style={{ stroke: "var(--accent)" }}
      />
      <rect
        x="23"
        y="23"
        width="32"
        height="32"
        rx="6"
        fill="none"
        strokeWidth="4"
        style={{ stroke: "var(--accent-2)" }}
      />
      <text
        x="32"
        y="38.5"
        textAnchor="middle"
        fontWeight="700"
        fontSize="17"
        style={{ fill: "var(--text)", fontFamily: "var(--font-sans)" }}
      >
        mh
      </text>
    </svg>
  );
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <LogoMark />
      <span className="text-lg font-semibold tracking-tight">mhsquare</span>
    </span>
  );
}
