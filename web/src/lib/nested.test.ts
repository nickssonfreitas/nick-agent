import { describe, expect, it } from "vitest";

import { getNestedValue, setNestedValue } from "./nested";

describe("setNestedValue", () => {
  it("sets and creates intermediate objects", () => {
    expect(setNestedValue({}, "agent.model", "opus")).toEqual({
      agent: { model: "opus" },
    });
  });

  it("does not mutate the input", () => {
    const original = { agent: { model: "sonnet" } };
    const next = setNestedValue(original, "agent.model", "opus");

    expect(original.agent.model).toBe("sonnet");
    expect(next).not.toBe(original);
  });

  // SEM-002: a path walking through __proto__ used to reach Object.prototype
  // and write there, polluting every object in the realm. structuredClone does
  // not help — it clones the target while the walk still lands on the shared
  // prototype.
  it("rejects prototype-polluting paths instead of writing to Object.prototype", () => {
    expect(() => setNestedValue({}, "__proto__.polluted", "yes")).toThrow(
      /Unsafe config path/,
    );
    expect(() => setNestedValue({}, "constructor.prototype.polluted", "yes")).toThrow(
      /Unsafe config path/,
    );
    expect(() => setNestedValue({}, "a.__proto__.polluted", "yes")).toThrow(
      /Unsafe config path/,
    );

    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it("rejects empty path parts", () => {
    expect(() => setNestedValue({}, "agent..model", "opus")).toThrow(
      /Unsafe config path/,
    );
  });
});

describe("getNestedValue", () => {
  it("reads nested values and returns undefined for missing paths", () => {
    const config = { agent: { model: "opus" } };

    expect(getNestedValue(config, "agent.model")).toBe("opus");
    expect(getNestedValue(config, "agent.missing")).toBeUndefined();
    expect(getNestedValue(config, "missing.deeper")).toBeUndefined();
  });

  it("does not read inherited prototype properties", () => {
    expect(() => getNestedValue({}, "__proto__.toString")).toThrow(
      /Unsafe config path/,
    );
    // toString lives on Object.prototype, never on the config object itself.
    expect(getNestedValue({}, "toString")).toBeUndefined();
  });
});
