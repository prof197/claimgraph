// The shell's colours are tokens only, and its text meets 4.5:1 on what it sits on. Pairs the
// token test already covers are not repeated here.
import css from "../../tokens.css?raw";
import shellCss from "./shell.css?raw";

const tokens = Object.fromEntries(
  [...css.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6});/gi)].map((m) => [m[1], m[2]]),
);

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r = 0, g = 0, b = 0] = channels.map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

it("uses no colour literals", () => {
  const withoutComments = shellCss.replace(/\/\*[\s\S]*?\*\//g, "");
  expect(withoutComments.match(/#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(/gi)).toBeNull();
});

it("uses only tokens that tokens.css defines", () => {
  const defined = new Set([...css.matchAll(/--([\w-]+):/g)].map((m) => m[1]));
  const used = [...shellCss.matchAll(/var\(--([\w-]+)\)/g)].map((m) => m[1]);
  expect(used.filter((name) => !defined.has(name))).toEqual([]);
});

const SHELL_TEXT_ON: [string, string][] = [
  // Step text, quiet badges and the tab counts.
  ["text-tertiary", "surface"],
  // The selected row and the quote block sit on the page colour.
  ["text-secondary", "page"],
  ["text-tertiary", "page"],
  // The pressed view toggle.
  ["surface", "text"],
];

it.each(SHELL_TEXT_ON)("%s text on %s meets 4.5:1", (fg, bg) => {
  const a = tokens[`color-${fg}`];
  const b = tokens[`color-${bg}`];
  expect(a && b).toBeTruthy();
  expect(contrast(a as string, b as string)).toBeGreaterThanOrEqual(4.5);
});
