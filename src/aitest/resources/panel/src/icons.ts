/** Small inline icon set. Icons are static, trusted markup; all text is set via textContent. */

export type IconName =
  | "play"
  | "pause"
  | "stop"
  | "check"
  | "check-circle"
  | "alert"
  | "info"
  | "refresh"
  | "arrow-right"
  | "arrow-left"
  | "chevron-down"
  | "chevron-right"
  | "folder"
  | "file"
  | "settings"
  | "sliders"
  | "terminal"
  | "shield"
  | "list"
  | "chart"
  | "external"
  | "copy"
  | "plus"
  | "x"
  | "eye"
  | "link"
  | "layers"
  | "clock"
  | "user"
  | "zap"
  | "spark"
  | "bug"
  | "report"
  | "search"
  | "cloud-off";

const NS = "http://www.w3.org/2000/svg";

const PATHS: Record<IconName, string> = {
  play: '<path d="M8 5.5v13l11-6.5z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="7" y="5" width="3.5" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="13.5" y="5" width="3.5" height="14" rx="1" fill="currentColor" stroke="none"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor" stroke="none"/>',
  check: '<path d="m5 12.5 4.2 4.2L19 7"/>',
  "check-circle": '<circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.2 2.4 2.4 4.7-5"/>',
  alert: '<path d="M12 3.5 21 20H3z"/><path d="M12 9v5"/><path d="M12 17.2v.1"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5"/><path d="M12 7.7v.1"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v5h-5"/>',
  "arrow-right": '<path d="M5 12h13"/><path d="m13 6.5 5.5 5.5-5.5 5.5"/>',
  "arrow-left": '<path d="M19 12H6"/><path d="m11 6.5-5.5 5.5 5.5 5.5"/>',
  "chevron-down": '<path d="m6 9 6 6 6-6"/>',
  "chevron-right": '<path d="m9 6 6 6-6 6"/>',
  folder: '<path d="M3.5 7.5A2.5 2.5 0 0 1 6 5h4l2 2h6a2.5 2.5 0 0 1 2.5 2.5v7A2.5 2.5 0 0 1 18 19H5.5A2 2 0 0 1 3.5 17z"/>',
  file: '<path d="M6 3.5h7l4.5 4.5v12H6z"/><path d="M13 3.5V8h4.5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4v-.2a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3v-4h.2a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6V3h4v.2a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1z"/>',
  sliders: '<path d="M4 7h10"/><path d="M18 7h2"/><circle cx="16" cy="7" r="2"/><path d="M4 17h2"/><path d="M10 17h10"/><circle cx="8" cy="17" r="2"/>',
  terminal: '<path d="M4 5.5h16v13H4z"/><path d="m7.5 10 2.5 2.5-2.5 2.5"/><path d="M12.5 15h4"/>',
  shield: '<path d="M12 3.5 19 6v5.5c0 4.2-2.9 7.3-7 9-4.1-1.7-7-4.8-7-9V6z"/><path d="m9 12 2 2 4-4"/>',
  list: '<path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/><path d="M4.5 6h.1"/><path d="M4.5 12h.1"/><path d="M4.5 18h.1"/>',
  chart: '<path d="M4 20V4"/><path d="M4 20h16"/><path d="M8 16v-5"/><path d="M12 16V8"/><path d="M16 16v-3"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 14v5H5V6h5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  x: '<path d="m6 6 12 12"/><path d="m18 6-12 12"/>',
  eye: '<path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.1 0l2-2a5 5 0 0 0-7.1-7.1L10.8 5"/><path d="M14 11a5 5 0 0 0-7.1 0l-2 2a5 5 0 0 0 7.1 7.1L13.2 19"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5"/><path d="m3 16 9 5 9-5"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
  user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  zap: '<path d="M13 2 4.5 13H11l-1 9 8.5-11H12z"/>',
  spark: '<path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16v4"/><path d="M17 18h4"/>',
  bug: '<rect x="7" y="8" width="10" height="11" rx="5"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/><path d="M3 12h4"/><path d="M17 12h4"/><path d="M5 18h3"/><path d="M16 18h3"/>',
  report: '<path d="M6 3.5h9l4 4V20H6z"/><path d="M15 3.5V8h4"/><path d="M9 12h6"/><path d="M9 16h6"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  "cloud-off": '<path d="M4 4l16 16"/><path d="M7 17.5h9.5a4 4 0 0 0 2.2-7.3"/><path d="M6.5 14.5A4.5 4.5 0 0 1 8 6"/><path d="M11 6h1a4.5 4.5 0 0 1 4.2 3"/>',
};

export function icon(name: IconName, size = 16): SVGSVGElement {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = PATHS[name];
  return svg;
}
