/** TraceLayer mark: three data layers with one traced path from source to destination. */
export function LogoMark({ size = 36, variant = "dark" }: { size?: number; variant?: "dark" | "light" }) {
  const layer = variant === "dark" ? { fill: "#1B2C45", stroke: "#5CD1C2", strokeWidth: 0.7 } : { fill: "#0E1A2B" };
  return (
    <svg role="img" aria-label="TraceLayer logo" viewBox="0 0 40 40" width={size} height={size}>
      <rect x="4" y="5" width="32" height="8" rx="3" {...layer} />
      <rect x="4" y="16" width="32" height="8" rx="3" {...layer} />
      <rect x="4" y="27" width="32" height="8" rx="3" {...layer} />
      <path d="M10 9 L20 20 L30 31" fill="none" stroke="#5CD1C2" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="10" cy="9" r="2.6" fill="#F2B84B" />
      <circle cx="20" cy="20" r="2.2" fill="#5CD1C2" />
      <circle cx="30" cy="31" r="2.6" fill="#F2B84B" />
    </svg>
  );
}
