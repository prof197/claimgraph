// The committed fixture exports, typed. They are written from the Python fixture builders and
// validated there, so the cast only restores literal types that JSON imports widen.
import type { Export } from "../types/export";
import minimal from "./minimal.json";
import synthetic from "./synthetic.json";

export const fixtures: Record<"synthetic" | "minimal", Export> = {
  synthetic: synthetic as unknown as Export,
  minimal: minimal as unknown as Export,
};
