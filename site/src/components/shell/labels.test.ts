import { fixtures } from "../../fixtures";
import {
  defaultFinding,
  designLine,
  dimensionValue,
  inSentence,
  longDate,
  percent,
  studyRows,
  weightsLine,
} from "./labels";

const data = fixtures.synthetic;
const symptoms = data.outcomes[0]!;

it("writes the design line with plural trials", () => {
  expect(designLine(symptoms.summary)).toBe("3 RCTs · 1 cohort · 1 cross-sectional · 1 case series");
  expect(designLine({ ...symptoms.summary, designs: [{ design: "rct", studies: 1 }] })).toBe("1 RCT");
});

it("rounds shares and never rounds a present share to zero", () => {
  expect(percent(0.64)).toBe("64%");
  expect(percent(0.004)).toBe("under 1%");
  expect(percent(0)).toBe("0%");
});

it("lower-cases an outcome inside a sentence unless it starts with an acronym", () => {
  expect(inSentence("Synthetic symptoms")).toBe("synthetic symptoms");
  expect(inSentence("PHQ synthetic score")).toBe("PHQ synthetic score");
});

it("writes the retrieval date in words", () => {
  expect(longDate("2026-01-15")).toBe("January 15, 2026");
});

it("reads the design weights the summary used", () => {
  expect(weightsLine(data, symptoms.summary)).toBe("RCT 3 · cohort 2 · cross-sectional 1 · case series 0.5");
});

it("lists one row per study and says which are shown but not counted", () => {
  const rows = studyRows(data, symptoms);

  expect(rows.map((r) => [r.finding.id, r.notCounted])).toEqual([
    ["999000102-1", null],
    ["999000104-1", null],
    ["999000101-1", null],
    ["999000103-1", null],
    ["999000108-1", "subgroup"],
    ["999000105-1", null],
    ["999000106-1", null],
    ["999000107-1", "unweighted"],
  ]);
  expect(defaultFinding(rows)).toBe("999000102-1");
  expect(defaultFinding([])).toBeNull();
});

it("writes a dimension's value with its unit", () => {
  expect(dimensionValue(50, "mg")).toBe("50 mg");
  expect(dimensionValue("not_reported", null)).toBe("Not reported");
  expect(dimensionValue(null, "mg")).toBe("Not reported");
  expect(dimensionValue(undefined, null)).toBe("Not reported");
  expect(dimensionValue("severely_low", null)).toBe("severely low");
});
