// Translation catalogue (spec §10.1): every user-facing string lives in i18n/<lang>.json as an
// ICU MessageFormat string. Supported: {name}, {n, plural, one {...} other {...}} (# is the
// number) and {x, select, a {...} other {...}}. No text in code.
export const LANGUAGES = { en: "English", sw: "Kiswahili" };
let catalogue = {}, fallback = {}, lang = "en";

export async function setLanguage(code) {
  if (!LANGUAGES[code]) throw new Error(`unknown language ${code}`);
  const load = async c => (await fetch(`/static/i18n/${c}.json`)).json();
  fallback = Object.keys(fallback).length ? fallback : await load("en");
  catalogue = code === "en" ? fallback : await load(code);
  lang = code;
  document.documentElement.lang = code;
  try { localStorage.setItem("lang", code); } catch { /* private window: not remembered */ }
  apply(document);
  document.dispatchEvent(new Event("languagechange"));
}

export function savedLanguage() {
  try { return localStorage.getItem("lang") || "en"; } catch { return "en"; }
}

export const language = () => lang;
export const reviewed = () => catalogue._meta?.reviewed !== false;

export function t(key, vars = {}) {
  const src = catalogue[key] ?? fallback[key];
  if (src === undefined) throw new Error(`missing string ${key}`);
  return format(src, vars);
}

// Fill [data-i18n] text and [data-i18n-<attr>] attributes under root.
export function apply(root) {
  for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of root.querySelectorAll("*")) {
    for (const a of el.getAttributeNames()) {
      if (a.startsWith("data-i18n-")) el.setAttribute(a.slice(10), t(el.getAttribute(a)));
    }
  }
}

export function format(src, vars) {
  let out = "", i = 0;
  while (i < src.length) {
    if (src[i] !== "{") { out += src[i++]; continue; }
    const end = matching(src, i);
    out += argument(src.slice(i + 1, end), vars);
    i = end + 1;
  }
  return out;
}

function matching(s, open) {
  let depth = 0;
  for (let i = open; i < s.length; i++) {
    if (s[i] === "{") depth++;
    else if (s[i] === "}" && --depth === 0) return i;
  }
  throw new Error(`unbalanced braces in ${s}`);
}

function argument(body, vars) {
  const [name, kind] = body.split(",", 2).map(x => x.trim());
  if (!(name in vars)) throw new Error(`missing value {${name}}`);
  const value = vars[name];
  if (kind === undefined) return String(value);
  const options = {};
  let rest = body.slice(body.indexOf(",", body.indexOf(",") + 1) + 1);
  while (rest.trim()) {
    const open = rest.indexOf("{"), close = matching(rest, open);
    options[rest.slice(0, open).trim()] = rest.slice(open + 1, close);
    rest = rest.slice(close + 1);
  }
  if (kind === "plural") {
    const exact = options[`=${value}`];
    const rule = new Intl.PluralRules(lang).select(value);
    const chosen = exact ?? options[rule] ?? options.other;
    return format(chosen.replaceAll("#", String(value)), vars);
  }
  if (kind === "select") return format(options[value] ?? options.other, vars);
  throw new Error(`unsupported argument type ${kind}`);
}
