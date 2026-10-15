// 站点的字标和圆环标记：页面用 Wordmark 画站名（size 是高度，单位像素），RingMark 是小标记，转起来就是加载动画。
// 这里用站名排字；有自己的 Logo 时，把 Wordmark 换成你的 SVG（保持同样的参数）。
import { SITE } from "../site.ts";

export function Wordmark({ size = 24, className = "", title = SITE.name }: { size?: number; className?: string; title?: string }) {
  return (
    <span className={`inline-flex items-center font-black leading-none tracking-[-0.03em] ${className}`} style={{ fontSize: Math.round(size * 0.92) }} aria-label={title} role="img">
      <svg viewBox="0 0 513 501" width={size * 1.25} height={size * 1.25} className="mr-[0.3em] shrink-0" fill="none" strokeWidth="34" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <g stroke="currentColor">
          <path d="M 202 212 C 170 244 134 273 117 313 C 92 384 153 439 213 431 C 253 426 278 396 306 365 L 424 244 C 481 186 474 99 404 64 C 344 35 293 63 248 112 L 210 153" />
          <path d="M 210 153 L 220 76" />
          <path d="M 210 153 L 285 154" />
        </g>
        <path d="M 205 341 L 355 191" stroke="#FF332F" />
      </svg>
      <span aria-hidden="true">{SITE.name}</span>
    </span>
  );
}

/** A ring with a dot; spinning, it is the loader. */
export function RingMark({ className = "", spinning = false }: { className?: string; spinning?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <g style={spinning ? { transformOrigin: "12px 12px", animation: "spin-slow 1.1s linear infinite" } : undefined}>
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeDasharray="42 15" />
      </g>
      <circle cx="12" cy="12" r="2.6" fill="currentColor" />
    </svg>
  );
}
