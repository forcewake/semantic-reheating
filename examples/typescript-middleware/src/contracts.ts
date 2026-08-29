import {
  Ajv2020,
  type AnySchema,
  type ErrorObject,
  type ValidateFunction,
} from "ajv/dist/2020.js";
import canonicalize from "canonicalize";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const exampleDirectory = fileURLToPath(new URL("../", import.meta.url));
const authoritativeContractsDirectory = resolve(exampleDirectory, "../../contracts/v1");
const fixtureSchemaPath = resolve(
  exampleDirectory,
  "fixtures/python-v1-artifacts.schema.json",
);

const artifactSchemaNames: Record<string, string> = {
  trace_event: "trace-event.schema.json",
  run_policy: "run-policy.schema.json",
  detector_finding: "detector-finding.schema.json",
  decision_envelope: "decision-envelope.schema.json",
  recovery_instruction: "recovery-instruction.schema.json",
  recovery_outcome: "recovery-outcome.schema.json",
  evidence_record: "evidence-record.schema.json",
};

function parseJson(path: string): AnySchema {
  return JSON.parse(readFileSync(path, "utf8")) as AnySchema;
}

function validationError(errors: ErrorObject[] | null | undefined): Error {
  const first = errors?.[0];
  const location = first?.instancePath || "$";
  return new Error(`Contract validation failed at ${location}: ${first?.message ?? "unknown error"}`);
}

function createAjv(): Ajv2020 {
  return new Ajv2020({ allErrors: true, strict: false });
}

function authoritativeValidators(): Map<string, ValidateFunction> {
  const ajv = createAjv();
  const schemas = readdirSync(authoritativeContractsDirectory)
    .filter((name) => name.endsWith(".schema.json"))
    .map((name) => parseJson(resolve(authoritativeContractsDirectory, name)));
  for (const schema of schemas) {
    ajv.addSchema(schema);
  }
  return new Map(
    Object.entries(artifactSchemaNames).map(([kind, filename]) => [
      kind,
      ajv.getSchema(
        `https://semantic-reheating.dev/contracts/v1/${filename}`,
      ) ?? (() => {
        throw new Error(`Missing authoritative schema for ${kind}`);
      })(),
    ]),
  );
}

function fixtureValidator(): ValidateFunction {
  const ajv = createAjv();
  for (const filename of Object.values(artifactSchemaNames)) {
    ajv.addSchema(parseJson(resolve(authoritativeContractsDirectory, filename)));
  }
  return ajv.compile(parseJson(fixtureSchemaPath));
}

const validators = authoritativeValidators();
const validateAggregate = fixtureValidator();

export function validatePublicArtifact(kind: string, artifact: unknown): unknown {
  const validator = validators.get(kind);
  if (validator === undefined) {
    throw new Error(`Unknown public artifact kind: ${kind}`);
  }
  if (!validator(artifact)) {
    throw validationError(validator.errors);
  }
  return artifact;
}

export function validateFixture(fixture: unknown): unknown {
  if (!validateAggregate(fixture)) {
    throw validationError(validateAggregate.errors);
  }
  return fixture;
}

export function loadFixture(fixture: unknown): unknown {
  return validateFixture(fixture);
}

export function canonicalBytes(value: unknown): Buffer {
  const serialized = canonicalize(value);
  if (serialized === undefined) {
    throw new Error("Value is not canonicalizable JSON");
  }
  return Buffer.from(serialized, "utf8");
}
