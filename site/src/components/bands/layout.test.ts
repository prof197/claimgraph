import { fixtures } from "../../fixtures";
import { type StudyRow, studyRows } from "../shell/labels";
import { CUT, STEP, keyboardOrder, layoutBands, radius, solidity, yearAxis, zoneStats } from "./layout";

const data = fixtures.synthetic;
const symptoms = studyRows(data, data.outcomes[0]!);
const every = data.outcomes.map((o) => studyRows(data, o));

/** The rows with some studies moved to older years, which the fixture does not have. */
function withYears(rows: StudyRow[], years: number[]): StudyRow[] {
  return rows.map((row, i) => ({ ...row, study: { ...row.study, year: years[i] ?? row.study.year } }));
}

describe("a dot", () => {
  it("grows with sample size, and a missing size draws small", () => {
    expect(radius(100)).toBeLessThan(radius(1000));
    expect(radius(1000)).toBeLessThan(radius(100_000));
    expect(radius(null)).toBeLessThan(radius(100));
    expect(radius(3)).toBe(radius(10));
  });

  it("is solid for a trial and an outline for a design without a weight", () => {
    expect(solidity(3)).toBe(1);
    expect(solidity(2)).toBeGreaterThan(solidity(1));
    expect(solidity(1)).toBeGreaterThan(solidity(0.5));
    expect(solidity(null)).toBe(0);
  });
});

describe("the year axis", () => {
  const axis = yearAxis(2026, 100, 700);

  it("puts every year before 2010 in one section as wide as a two-year step", () => {
    const before = [1993, 2001, CUT - 1].map((y) => axis.section(y));
    expect(new Set(before.map((s) => s.key)).size).toBe(1);
    const step = axis.section(CUT).width * STEP;
    expect(before[0]!.width).toBeCloseTo(step, 6);
    expect(before[0]!.centre).toBeCloseTo(100 + step / 2, 6);
  });

  it("gives each year from 2010 its own equal slot, through the last year", () => {
    const slots = [2010, 2011, 2020, 2026].map((y) => axis.section(y));
    expect(new Set(slots.map((s) => s.key)).size).toBe(4);
    for (const s of slots) expect(s.width).toBeCloseTo(slots[0]!.width, 6);
    expect(slots[3]!.centre + slots[3]!.width / 2).toBeCloseTo(700, 6);
  });

  it("labels the pre-2010 section and every two-year boundary", () => {
    expect(axis.ticks.flatMap((t) => (t.label === null ? [] : [t.label]))).toEqual([
      "<2010",
      ...[2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024, 2026].map(String),
    ]);
    expect(axis.ticks.filter((t) => t.boundary).length).toBe(10);
  });

  it("names fewer years when the axis is narrow, keeping every gridline", () => {
    const narrow = yearAxis(2026, 14, 260);
    const labels = narrow.ticks.flatMap((t) => (t.label === null || !t.boundary ? [] : [Number(t.label)]));
    expect(labels[0]).toBe(2014);
    expect(labels[1]! - labels[0]!).toBeGreaterThanOrEqual(4);
    expect(narrow.ticks.some((t) => t.label === "<2010")).toBe(true);
    expect(narrow.ticks.filter((t) => t.boundary).length).toBe(10);
  });
});

describe("the band labels' counts", () => {
  it("count only counted studies, and follow the designs picked", () => {
    const all = zoneStats(symptoms, new Set());
    const counted = symptoms.filter((r) => r.notCounted === null);
    expect(all.benefit.studies + all.none.studies + all.harm.studies).toBe(counted.length);
    expect((all.benefit.share ?? 0) + (all.none.share ?? 0) + (all.harm.share ?? 0)).toBeCloseTo(1, 9);

    const trials = zoneStats(symptoms, new Set(["rct"]));
    const rcts = counted.filter((r) => r.study.design === "rct");
    expect(trials.benefit.studies + trials.none.studies + trials.harm.studies).toBe(rcts.length);
    const share = rcts.filter((r) => r.finding.direction === "benefit").length / rcts.length;
    expect(trials.benefit.share).toBeCloseTo(share, 9);
  });

  it("have no share when nothing picked is counted", () => {
    const none = zoneStats(symptoms, new Set(["case_report"]));
    expect(none.benefit).toEqual({ studies: 0, share: null });
  });
});

describe("the band layout", () => {
  const layout = layoutBands(symptoms, every, 2026, 760, 480)!;

  it("draws the same picture every time", () => {
    const again = layoutBands(symptoms, every, 2026, 760, 480)!;
    expect(again.k).toBe(layout.k);
    expect(again.bands).toEqual(layout.bands);
  });

  it("puts each study in its direction's band, inside its year's section", () => {
    expect(layout.bands.map((b) => b.direction)).toEqual(["benefit", "none", "harm"]);
    for (const band of layout.bands) {
      expect(band.marks.length).toBe(symptoms.filter((r) => r.finding.direction === band.direction).length);
      for (const m of band.marks) {
        const { centre, width } = layout.axis.section(m.row.study.year);
        expect(Math.abs(m.x - centre)).toBeLessThanOrEqual(width / 2 + 1e-9);
        expect(band.mid + m.y - m.r).toBeGreaterThanOrEqual(band.top);
        expect(band.mid + m.y + m.r).toBeLessThanOrEqual(band.top + band.height);
      }
    }
  });

  it("fills the panel's height", () => {
    const last = layout.bands.at(-1)!;
    expect(layout.height).toBe(480);
    expect(last.top + last.height).toBeCloseTo(480 - 4, 6);
  });

  it("sizes dots alike on every outcome, by the busiest", () => {
    const thin = studyRows(data, data.outcomes[2]!);
    const busiest = withYears(symptoms, symptoms.map(() => 2015));
    const alone = layoutBands(thin, [thin], 2026, 760, 260)!;
    const shared = layoutBands(thin, [thin, busiest], 2026, 760, 260)!;
    expect(shared.k).toBeLessThan(alone.k);
    expect(shared.k).toBe(layoutBands(busiest, [thin, busiest], 2026, 760, 260)!.k);
  });

  it("gathers studies before 2010 into the first section", () => {
    const old = withYears(symptoms, [1995, 2004, 2009]);
    const at = layoutBands(old, [old], 2026, 760, 480)!;
    const first = at.axis.section(1995);
    const gathered = at.bands.flatMap((b) => b.marks).filter((m) => m.row.study.year < CUT);
    expect(gathered).toHaveLength(3);
    for (const m of gathered) expect(Math.abs(m.x - first.centre)).toBeLessThanOrEqual(first.width / 2);
  });

  it("scrolls rather than overlap when the panel is too short", () => {
    const short = layoutBands(symptoms, every, 2026, 760, 60)!;
    expect(short.height).toBeGreaterThan(60);
  });

  it("moves the labels above each band on a narrow screen", () => {
    const narrow = layoutBands(symptoms, every, 2026, 340, 480)!;
    expect(narrow.compact).toBe(true);
    expect(narrow.axis.left).toBeLessThan(layout.axis.left);
    for (const band of narrow.bands.filter((b) => b.marks.length > 0)) {
      expect(Math.min(...band.marks.map((m) => band.mid + m.y - m.r))).toBeGreaterThan(band.top + 20);
    }
  });

  it("walks the keyboard band by band, oldest first", () => {
    const order = keyboardOrder(layout.bands);
    expect(order).toHaveLength(symptoms.length);
    const directions = order.map((m) => m.direction);
    expect(directions).toEqual([...directions].sort((a, b) => ["benefit", "none", "harm"].indexOf(a) - ["benefit", "none", "harm"].indexOf(b)));
    for (let i = 1; i < order.length; i += 1) {
      if (order[i]!.direction === order[i - 1]!.direction) expect(order[i]!.x).toBeGreaterThanOrEqual(order[i - 1]!.x);
    }
  });
});
