import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  canonicalBytes,
  loadFixture,
  validateFixture,
  validatePublicArtifact,
} from "../src/contracts.js";
import { runHostLoop } from "../src/index.js";

const fixturePath = fileURLToPath(
  new URL("../fixtures/python-v1-artifacts.json", import.meta.url),
);

async function fixture(): Promise<unknown> {
  return loadFixture(JSON.parse(await readFile(fixturePath, "utf8")));
}

describe("Python v1 public artifact fixture", () => {
  it("validates the unmodified Python-emitted aggregate through AJV", async () => {
    const bundle = await fixture();
    expect(validateFixture(bundle)).toEqual(bundle);
  });

  it("rejects unknown wrapper and nested fields and unknown v1 majors", async () => {
    const bundle = (await fixture()) as {
      fixture_version: string;
      artifacts: { trace_event: Record<string, unknown> };
    };

    expect(() => validateFixture({ ...bundle, unexpected: true })).toThrow();
    expect(() =>
      validateFixture({
        ...bundle,
        artifacts: {
          ...bundle.artifacts,
          trace_event: { ...bundle.artifacts.trace_event, unexpected: true },
        },
      }),
    ).toThrow();
    expect(() => validateFixture({ ...bundle, fixture_version: "2.0" })).toThrow();
    expect(() =>
      validateFixture({
        ...bundle,
        artifacts: {
          ...bundle.artifacts,
          trace_event: {
            ...bundle.artifacts.trace_event,
            contract_version: "2.0",
          },
        },
      }),
    ).toThrow();
  });

  it("preserves the explicit host-owned escalation decision", async () => {
    const bundle = (await fixture()) as {
      artifacts: {
        decision_envelope: { decision: string; requires_host_action: boolean };
        run_policy: {
          recovery_ladder: { escalate: { requires_host_action: boolean } };
        };
      };
    };

    expect(bundle.artifacts.decision_envelope).toMatchObject({
      decision: "escalate",
      requires_host_action: true,
    });
    expect(bundle.artifacts.run_policy.recovery_ladder.escalate.requires_host_action).toBe(
      true,
    );
  });

  it("uses canonical RFC 8785 UTF-8 bytes and SHA-256 from Python", async () => {
    const bundle = (await fixture()) as {
      canonical_i_json: { value: unknown; utf8: string; sha256: string };
    };
    const bytes = canonicalBytes(bundle.canonical_i_json.value);

    expect(bytes.toString("utf8")).toBe(bundle.canonical_i_json.utf8);
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(
      bundle.canonical_i_json.sha256,
    );
    expect(bundle.canonical_i_json.utf8).toContain("e\u0301");
  });

  it("validates every Python-emitted artifact directly against its authoritative schema", async () => {
    const bundle = (await fixture()) as {
      artifacts: Record<string, unknown>;
    };
    for (const [kind, artifact] of Object.entries(bundle.artifacts)) {
      expect(validatePublicArtifact(kind, artifact)).toEqual(artifact);
    }
  });
});

describe("generic async host loop", () => {
  const scenarios = [
    ["normal progress", "continue", "continue"],
    ["stagnation", "nudge", "nudge"],
    ["bounded recovery", "reheat", "reheat"],
    ["cooling", "reheat", "cool"],
    ["safe stop", "stop", "stop"],
  ] as const;

  it.each(scenarios)("keeps %s execution in the host callback", async (_, decision, action) => {
    const executorCalls: string[] = [];
    const result = await runHostLoop({
      trace: [{ event: "host-observed" }],
      policy: { policy_id: "host-policy" },
      analyze: async () => ({ decision, requires_host_action: decision === "stop" }),
      execute: async (advisory) => {
        executorCalls.push(advisory.decision);
        return { action, owner: "host" };
      },
    });

    expect(result.decision.decision).toBe(decision);
    expect(result.execution).toEqual({ action, owner: "host" });
    expect(executorCalls).toEqual([decision]);
  });
});
