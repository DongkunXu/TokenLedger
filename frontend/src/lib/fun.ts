import type { Lang, Strings } from "../i18n/strings";
import { tokens } from "./format";

// Playful equivalents of a token count. Every token counts as one Chinese character, or
// 0.75 of an English word; the constants are round figures picked for fun, not research.
// The sentence for each item lives in strings.ts under fun.items, keyed by id.

export interface FunStats {
  tokens: number;
  minutes: number; // how long the period lasted, for the speed items
}

export type FunGroup = "books" | "paper" | "people" | "speed" | "odd";
type Kind = "count" | "percent" | "duration" | "length" | "mass" | "bytes" | "rate" | "kmh";

export interface FunItem {
  id: string;
  group: FunGroup;
  kind: Kind;
  value: (s: FunStats) => number;
}

const WORDS = 0.75; // English words per token
const MM_PER_CHAR = 3.7; // a 五号 (10.5 pt) character
const CHARS_PER_PAGE = 1700; // A4, 五号
const KEYS_PER_CHAR = 3; // pinyin
const FINGER_M = 0.02; // finger travel per keystroke
const HOUR = 60;
const DAY = 24 * HOUR;

const per = (n: number) => (s: FunStats) => s.tokens / n;
const perWords = (words: number) => per(words / WORDS);
const minutesAt = (perMinute: number) => (s: FunStats) => s.tokens / perMinute;
const rate = (scale: number) => (s: FunStats) => (s.minutes > 0 ? (s.tokens * scale) / s.minutes : 0);

export const FUN_GROUPS: FunGroup[] = ["books", "paper", "people", "speed", "odd"];

export const FUN_ITEMS: FunItem[] = [
  { id: "hongloumeng", group: "books", kind: "count", value: per(730_000) },
  { id: "xiyouji", group: "books", kind: "count", value: per(820_000) },
  { id: "sanguo", group: "books", kind: "count", value: per(640_000) },
  { id: "shuihu", group: "books", kind: "count", value: per(960_000) },
  { id: "santi", group: "books", kind: "count", value: per(880_000) },
  { id: "jinyong", group: "books", kind: "count", value: per(14_000_000) },
  { id: "siku", group: "books", kind: "count", value: per(800_000_000) },
  { id: "yongle", group: "books", kind: "count", value: per(370_000_000) },
  { id: "shakespeare", group: "books", kind: "count", value: perWords(884_000) },
  { id: "potter", group: "books", kind: "count", value: perWords(1_084_000) },
  { id: "rings", group: "books", kind: "count", value: perWords(480_000) },
  { id: "wikipedia", group: "books", kind: "count", value: perWords(5_000_000_000) },
  { id: "jueju", group: "books", kind: "count", value: per(28) },
  { id: "xinjing", group: "books", kind: "count", value: per(260) },
  { id: "thesis", group: "books", kind: "count", value: per(100_000) },

  { id: "line", group: "paper", kind: "length", value: (s) => (s.tokens * MM_PER_CHAR) / 1000 },
  { id: "earth", group: "paper", kind: "count", value: (s) => (s.tokens * MM_PER_CHAR) / 1000 / 40_075_000 },
  { id: "moon", group: "paper", kind: "percent", value: (s) => (s.tokens * MM_PER_CHAR) / 1000 / 384_400_000 },
  { id: "wall", group: "paper", kind: "count", value: (s) => (s.tokens * MM_PER_CHAR) / 1000 / 21_196_000 },
  { id: "pages", group: "paper", kind: "count", value: per(CHARS_PER_PAGE) },
  { id: "stack", group: "paper", kind: "length", value: (s) => (s.tokens / CHARS_PER_PAGE) * 0.0001 },
  { id: "trees", group: "paper", kind: "count", value: per(CHARS_PER_PAGE * 8_000) },
  { id: "paperWeight", group: "paper", kind: "mass", value: (s) => (s.tokens / CHARS_PER_PAGE) * 0.005 },

  { id: "handwrite", group: "people", kind: "duration", value: minutesAt(30) },
  { id: "type", group: "people", kind: "duration", value: minutesAt(60) },
  { id: "read", group: "people", kind: "duration", value: minutesAt(400) },
  { id: "speak", group: "people", kind: "duration", value: minutesAt(250) },
  { id: "webnovel", group: "people", kind: "duration", value: (s) => (s.tokens / 4_000) * DAY },
  { id: "count", group: "people", kind: "duration", value: (s) => s.tokens / 60 },

  { id: "speedMin", group: "speed", kind: "rate", value: rate(1) },
  { id: "speedSec", group: "speed", kind: "rate", value: rate(1 / 60) },
  { id: "keys", group: "speed", kind: "rate", value: rate(KEYS_PER_CHAR / 60) },
  { id: "finger", group: "speed", kind: "kmh", value: rate((KEYS_PER_CHAR * FINGER_M * 60) / 1000) },
  { id: "bolt", group: "speed", kind: "count", value: rate((KEYS_PER_CHAR * FINGER_M * 60) / 1000 / 44.7) },

  { id: "rice", group: "odd", kind: "mass", value: (s) => s.tokens * 0.00002 },
  { id: "steps", group: "odd", kind: "length", value: (s) => s.tokens * 0.7 },
  { id: "bytes", group: "odd", kind: "bytes", value: (s) => s.tokens * 3 },
  { id: "floppy", group: "odd", kind: "count", value: (s) => (s.tokens * 3) / 1_440_000 },
  { id: "tweets", group: "odd", kind: "count", value: per(140) },
  { id: "keyboards", group: "odd", kind: "count", value: per(50_000_000 / KEYS_PER_CHAR) },
];

// The number in an item's sentence, already formatted with its unit.
export function funValue(item: FunItem, stats: FunStats, t: Strings, lang: Lang): string {
  const v = item.value(stats);
  const u = t.fun.units;
  switch (item.kind) {
    case "count":
      return amount(v, lang);
    case "rate":
      return amount(v, lang);
    case "percent":
      return `${amount(v * 100, lang)}%`;
    case "kmh":
      return `${amount(v, lang)} ${u.kmh}`;
    case "length":
      return v >= 1000 ? `${amount(v / 1000, lang)} ${u.km}` : `${amount(v, lang)} ${u.m}`;
    case "mass":
      return v >= 1000 ? `${amount(v / 1000, lang)} ${u.t}` : `${amount(v, lang)} ${u.kg}`;
    case "bytes":
      return v >= 1e12 ? `${amount(v / 1e12, lang)} TB` : v >= 1e9 ? `${amount(v / 1e9, lang)} GB` : `${amount(v / 1e6, lang)} MB`;
    case "duration":
      return duration(v, t, lang);
  }
}

export function duration(minutes: number, t: Strings, lang: Lang): string {
  const u = t.fun.units;
  if (minutes >= 365 * DAY) return `${amount(minutes / (365 * DAY), lang)} ${u.years}`;
  if (minutes >= DAY) return `${amount(minutes / DAY, lang)} ${u.days}`;
  if (minutes >= HOUR) return `${amount(minutes / HOUR, lang)} ${u.hours}`;
  return `${amount(minutes, lang)} ${u.minutes}`;
}

// Small numbers keep a couple of digits; large ones use 万/亿 or K/M/B.
function amount(n: number, lang: Lang): string {
  if (n >= 10_000) return tokens(n, lang, 2);
  if (n > 0 && n < 1) return String(Number(n.toPrecision(2)));
  const d = n >= 100 ? 0 : n >= 10 ? 1 : 2;
  return Number(n.toFixed(d)).toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { maximumFractionDigits: d });
}

// Minutes from local midnight of `start` until now; the speed items divide by this.
export function minutesSince(start: string): number {
  const [y, m, d] = start.split("-").map(Number);
  return Math.max(1, (Date.now() - new Date(y, m - 1, d).getTime()) / 60_000);
}
