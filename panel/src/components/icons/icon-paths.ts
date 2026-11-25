export type IconName =
  | "menu"
  | "overview"
  | "close"
  | "chevron-right"
  | "chevron-down"
  | "check"
  | "file"
  | "plus"
  | "download"
  | "upload"
  | "refresh"
  | "warn"
  | "info"
  | "stop"
  | "play"
  | "settings"
  | "folder"
  | "clock"
  | "search"
  | "arrow-left"
  | "lock"
  | "copy"
  | "shield"
  | "history"
  | "more"
  | "layout"
  | "server"
  | "database"
  | "cloud"
  | "sun"
  | "moon"
  | "bolt"
  | "arch"
  | "wrench";

export const ICONS: Record<IconName, string[]> = {
  menu: ["M3 4.5h10M3 8h10M3 11.5h10"],
  overview: [
    "M2.5 2.5h4.5v4.5H2.5z",
    "M9 2.5h4.5v4.5H9z",
    "M2.5 9h4.5v4.5H2.5z",
    "M9 9h4.5v4.5H9z",
  ],
  close: ["M4 4l8 8M12 4l-8 8"],
  "chevron-right": ["M6 3.5L10.5 8 6 12.5"],
  "chevron-down": ["M4 6.5l4 4 4-4"],
  check: ["M3.5 8.5l3 3 6-6"],
  file: ["M4 2.5h5l3 3v8H4z", "M9 2.5v3h3"],
  plus: ["M8 3.5v9M3.5 8h9"],
  download: ["M8 3v7M5 7.5l3 3 3-3M3.5 12.5h9"],
  upload: ["M8 12V4M5 7l3-3 3 3M3.5 13h9"],
  refresh: ["M12.5 6.5A5 5 0 003.8 6M3.5 9.5A5 5 0 0012.2 10", "M12.5 3v3.5H9M3.5 13V9.5H7"],
  warn: ["M8 2.5l6 10.5H2z", "M8 6v3.5M8 11.7v.3"],
  info: ["M8 2.5a5.5 5.5 0 110 11 5.5 5.5 0 010-11z", "M8 7.3v4M8 5.2v.3"],
  stop: ["M4.5 4.5h7v7h-7z"],
  play: ["M4.5 3.5l8 4.5-8 4.5z"],
  settings: [
    "M3 5h10M3 11h10",
    "M7.6 5a1.6 1.6 0 11-3.2 0 1.6 1.6 0 013.2 0z",
    "M11.6 11a1.6 1.6 0 11-3.2 0 1.6 1.6 0 013.2 0z",
  ],
  folder: ["M2.5 5.5h11v8h-11z", "M2.5 5.5V3.5h4l1.2 2"],
  clock: ["M8 2.5a5.5 5.5 0 110 11 5.5 5.5 0 010-11z", "M8 5v3.3l2 1.2"],
  search: ["M7 2.5a4.5 4.5 0 110 9 4.5 4.5 0 010-9z", "M10.5 10.5l3 3"],
  "arrow-left": ["M10 3.5L5.5 8l4.5 4.5", "M5.5 8H13"],
  lock: ["M4.5 7.5h7v5h-7z", "M6 7.5V5.5a2 2 0 014 0v2"],
  copy: ["M5.5 5.5h7v7h-7z", "M3.5 10.5v-7h7"],
  shield: ["M8 2.5l4.5 2v4c0 2.5-2 4-4.5 5-2.5-1-4.5-2.5-4.5-5v-4z"],
  more: ["M3.5 8h.01M8 8h.01M12.5 8h.01"],
  history: ["M8 2.5a5.5 5.5 0 110 11 5.5 5.5 0 010-11z", "M8 5v3.3l2 1.2", "M2.5 5.5H5V3"],
  layout: ["M2.5 3h11v10h-11z", "M2.5 6.5h11M6.5 6.5V13"],
  server: ["M2.5 3h11v4h-11z", "M2.5 9h11v4h-11z", "M5 5h.01M5 11h.01"],
  database: ["M8 2.5c3 0 5.5 1 5.5 2.2S11 7 8 7 2.5 6 2.5 4.8 5 2.5 8 2.5z", "M2.5 4.8v6.4c0 1.2 2.5 2.3 5.5 2.3s5.5-1.1 5.5-2.3V4.8", "M2.5 8c0 1.2 2.5 2.2 5.5 2.2s5.5-1 5.5-2.2"],
  cloud: ["M4.5 12.5h7.3a2.7 2.7 0 00.3-5.4 3.8 3.8 0 00-7.2-.9A2.6 2.6 0 004.5 12.5z"],
  sun: [
    "M8 4.8a3.2 3.2 0 110 6.4 3.2 3.2 0 010-6.4z",
    "M8 1.8v1.4M8 12.8v1.4M1.8 8h1.4M12.8 8h1.4M3.6 3.6l1 1M11.4 11.4l1 1M12.4 3.6l-1 1M4.6 11.4l-1 1",
  ],
  moon: ["M12.8 9.8A5.2 5.2 0 016.2 3.2a5.2 5.2 0 106.6 6.6z"],
  bolt: ["M9 2L4 9h4l-1 5 5-7H8l1-5z"],
  arch: [
    "M2.5 3h3.5v3.5H2.5z",
    "M10 3h3.5v3.5H10z",
    "M6.25 9.5h3.5V13h-3.5z",
    "M6 4.75h4M4.25 6.5V11.25h2M11.75 6.5v4.75h-2",
  ],
  wrench: [
    "M10.4 3.2a3 3 0 00-3.9 3.9L3.2 10.4a1.2 1.2 0 001.7 1.7l3.3-3.3a3 3 0 003.9-3.9l-1.5 1.5-1.7-1.7 1.5-1.5z",
  ],
};
