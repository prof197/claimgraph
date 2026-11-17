// The direction colours stay apart for readers with each kind of dichromacy, and stay visible
// on the backgrounds they are drawn on. Colour never carries direction alone on the page, but a
// reader who can still tell the dots apart by colour should not be misled by it either.
import css from "./tokens.css?raw";

type Rgb = [number, number, number];
type Matrix = [Rgb, Rgb, Rgb];

const tokens = Object.fromEntries(
  [...css.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6});/gi)].map((m) => [m[1], m[2]]),
);

function token(name: string): string {
  const hex = tokens[`color-${name}`];
  if (hex === undefined) {
    throw new Error(`tokens.css has no --color-${name}`);
  }
  return hex;
}

// Dichromacy at full severity, from Machado, Oliveira and Fernandes (2009), "A
// Physiologically-based Model for Simulation of Color Vision Deficiency", IEEE TVCG 15(6),
// 1291-1298, Table 1, severity 1.0. The matrices act on linear RGB. The model is weakest for
// tritanopia, which is rare (about 1 in 10,000); protanopia and deuteranopia are the common
// cases and the ones the blue and orange pair is chosen for.
const DICHROMACY: Record<string, Matrix> = {
  protanopia: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deuteranopia: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
  tritanopia: [
    [1.255528, -0.076749, -0.178779],
    [-0.078411, 0.930809, 0.147602],
    [0.004733, 0.691367, 0.3039],
  ],
};
const IDENTITY: Matrix = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
];
const VISION: [string, Matrix][] = [["typical vision", IDENTITY], ...Object.entries(DICHROMACY)];

// Two direction colours must stay at least this far apart, in CIEDE2000, under every
// simulation. 2.3 is about one just-noticeable difference, but that is for large patches side by
// side; the chart's dots are a few pixels across and sit apart, where colour differences shrink
// sharply with size, so the floor asks for several times that: a difference a reader names as a
// different colour, not one they notice on a second look.
const DIRECTION_FLOOR = 10;
// WCAG 2.2's non-text contrast (1.4.11): a graphic needed to understand the content meets 3:1
// against the colours next to it. The segments and swatches sit on the surface, the cards on the
// band, and each direction's dots on that direction's band in the chart.
const GRAPHIC_CONTRAST = 3;

function linear(hex: string): Rgb {
  const [r = 0, g = 0, b = 0] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return [r, g, b];
}

function simulate(rgb: Rgb, m: Matrix): Rgb {
  const row = (r: Rgb) => Math.min(1, Math.max(0, r[0] * rgb[0] + r[1] * rgb[1] + r[2] * rgb[2]));
  return [row(m[0]), row(m[1]), row(m[2])];
}

function luminance([r, g, b]: Rgb): number {
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** CIELAB under D65, from linear sRGB. */
function lab([r, g, b]: Rgb): Rgb {
  const x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047;
  const y = 0.2126729 * r + 0.7151522 * g + 0.072175 * b;
  const z = (0.0193339 * r + 0.119192 * g + 0.9503041 * b) / 1.08883;
  const f = (t: number) => (t > 216 / 24389 ? Math.cbrt(t) : ((24389 / 27) * t + 16) / 116);
  return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
}

/** CIEDE2000, following Sharma, Wu and Dalal (2005), "The CIEDE2000 color-difference formula:
 * implementation notes, supplementary test data, and mathematical observations", Color Research
 * and Application 30(1), 21-30. */
function deltaE2000([l1, a1, b1]: Rgb, [l2, a2, b2]: Rgb): number {
  const rad = Math.PI / 180;
  const c7 = (c: number) => Math.sqrt(c ** 7 / (c ** 7 + 25 ** 7));
  const g = 0.5 * (1 - c7((Math.hypot(a1, b1) + Math.hypot(a2, b2)) / 2));
  const ap1 = (1 + g) * a1;
  const ap2 = (1 + g) * a2;
  const cp1 = Math.hypot(ap1, b1);
  const cp2 = Math.hypot(ap2, b2);
  const hue = (b: number, a: number) => {
    if (a === 0 && b === 0) return 0;
    const h = Math.atan2(b, a) / rad;
    return h < 0 ? h + 360 : h;
  };
  const hp1 = hue(b1, ap1);
  const hp2 = hue(b2, ap2);
  const chromatic = cp1 * cp2 !== 0;

  let dh = 0;
  if (chromatic) {
    dh = hp2 - hp1;
    if (dh > 180) dh -= 360;
    else if (dh < -180) dh += 360;
  }
  const dL = l2 - l1;
  const dC = cp2 - cp1;
  const dH = 2 * Math.sqrt(cp1 * cp2) * Math.sin((dh * rad) / 2);

  const lMean = (l1 + l2) / 2;
  const cMean = (cp1 + cp2) / 2;
  let hMean = hp1 + hp2;
  if (chromatic) {
    if (Math.abs(hp1 - hp2) <= 180) hMean /= 2;
    else hMean = hMean < 360 ? (hMean + 360) / 2 : (hMean - 360) / 2;
  }
  const t =
    1 -
    0.17 * Math.cos((hMean - 30) * rad) +
    0.24 * Math.cos(2 * hMean * rad) +
    0.32 * Math.cos((3 * hMean + 6) * rad) -
    0.2 * Math.cos((4 * hMean - 63) * rad);
  const sl = 1 + (0.015 * (lMean - 50) ** 2) / Math.sqrt(20 + (lMean - 50) ** 2);
  const sc = 1 + 0.045 * cMean;
  const sh = 1 + 0.015 * cMean * t;
  const rotation = -Math.sin(2 * 30 * Math.exp(-(((hMean - 275) / 25) ** 2)) * rad) * 2 * c7(cMean);
  return Math.sqrt(
    (dL / sl) ** 2 + (dC / sc) ** 2 + (dH / sh) ** 2 + rotation * (dC / sc) * (dH / sh),
  );
}

function seenApart(a: string, b: string, m: Matrix): number {
  return deltaE2000(lab(simulate(linear(a), m)), lab(simulate(linear(b), m)));
}

/** Every pair of `colours` that falls under the floor for some vision, as readable lines. */
function tooClose(colours: Record<string, string>, pairs: [string, string][]): string[] {
  return VISION.flatMap(([vision, m]) =>
    pairs.flatMap(([a, b]) => {
      const d = seenApart(colours[a] as string, colours[b] as string, m);
      return d < DIRECTION_FLOOR ? [`${a} and ${b} under ${vision}: ΔE00 ${d.toFixed(1)}`] : [];
    }),
  );
}

const DIRECTIONS: Record<string, string> = {
  benefit: token("benefit"),
  none: token("none"),
  harm: token("harm"),
};
const DIRECTION_PAIRS: [string, string][] = [
  ["benefit", "none"],
  ["benefit", "harm"],
  ["none", "harm"],
];

describe("the colour difference", () => {
  // Sharma, Wu and Dalal's supplementary test data.
  it.each([
    [[50, 2.6772, -79.7751], [50, 0, -82.7485], 2.0425],
    [[50, 0, 0], [50, -1, 2], 2.3669],
    [[50, 2.5, 0], [73, 25, -18], 27.1492],
    [[60.2574, -34.0099, 36.2677], [60.4626, -34.1751, 39.4387], 1.2644],
    [[2.0776, 0.0795, -1.135], [0.9033, -0.0636, -0.5514], 0.9082],
  ] as [Rgb, Rgb, number][])("matches the reference data: %j and %j", (a, b, expected) => {
    expect(deltaE2000(a, b)).toBeCloseTo(expected, 4);
    expect(deltaE2000(b, a)).toBeCloseTo(expected, 4);
  });

  it.each(Object.entries(DICHROMACY))("keeps greys grey under %s", (_, m) => {
    for (const grey of ["#000000", "#808080", "#ffffff"]) {
      expect(seenApart(grey, grey, m)).toBe(0);
      const [r, g, b] = simulate(linear(grey), m);
      expect(Math.abs(r - g) + Math.abs(g - b)).toBeLessThan(1e-3);
    }
  });
});

describe("the direction colours", () => {
  it(`stay at least ΔE00 ${DIRECTION_FLOOR} apart under every simulation`, () => {
    expect(tooClose(DIRECTIONS, DIRECTION_PAIRS)).toEqual([]);
  });

  it(`each meet ${GRAPHIC_CONTRAST}:1 on the surface and the band under every simulation`, () => {
    const faint = VISION.flatMap(([vision, m]) =>
      Object.entries(DIRECTIONS).flatMap(([direction, hex]) =>
        ["surface", "band"].flatMap((bg) => {
          const ratio = contrast(simulate(linear(hex), m), simulate(linear(token(bg)), m));
          return ratio < GRAPHIC_CONTRAST
            ? [`${direction} on ${bg} under ${vision}: ${ratio.toFixed(2)}:1`]
            : [];
        }),
      ),
    );
    expect(faint).toEqual([]);
  });

  it(`each meet ${GRAPHIC_CONTRAST}:1 on their own band in the chart under every simulation`, () => {
    const tint: Record<string, string> = { benefit: "benefit-tint", none: "rule", harm: "harm-tint" };
    const faint = VISION.flatMap(([vision, m]) =>
      Object.entries(DIRECTIONS).flatMap(([direction, hex]) => {
        const bg = tint[direction] as string;
        const ratio = contrast(simulate(linear(hex), m), simulate(linear(token(bg)), m));
        return ratio < GRAPHIC_CONTRAST ? [`${direction} on ${bg} under ${vision}: ${ratio.toFixed(2)}:1`] : [];
      }),
    );
    expect(faint).toEqual([]);
  });

  it("stay clear of the tab accent under every simulation", () => {
    const colours = { ...DIRECTIONS, accent: token("accent") };
    expect(
      tooClose(colours, [
        ["accent", "benefit"],
        ["accent", "harm"],
      ]),
    ).toEqual([]);
  });

  it("would catch a red and green palette, which deuteranopia merges", () => {
    const redGreen = { benefit: "#2ca02c", none: "#7f7f7f", harm: "#d62728" };
    expect(tooClose(redGreen, DIRECTION_PAIRS)).toContain(
      "benefit and harm under deuteranopia: ΔE00 4.6",
    );
  });
});
