// Text tokens meet WCAG's 4.5:1 on the backgrounds they are used on.
import css from "./tokens.css?raw";

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

const TEXT_ON: [string, string][] = [
  ["text", "page"],
  ["text", "surface"],
  ["text", "band"],
  ["text-secondary", "surface"],
  ["text-secondary", "band"],
  ["text-tertiary", "band"],
  ["link", "surface"],
  ["link", "band"],
  ["benefit", "benefit-tint"],
  ["harm-text", "harm-tint"],
  ["harm-text", "badge-tint"],
  ["accent", "surface"],
];

it.each(TEXT_ON)("%s text on %s meets 4.5:1", (fg, bg) => {
  const a = tokens[`color-${fg}`];
  const b = tokens[`color-${bg}`];
  expect(a && b).toBeTruthy();
  expect(contrast(a as string, b as string)).toBeGreaterThanOrEqual(4.5);
});
