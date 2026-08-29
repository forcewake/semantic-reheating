export type AdvisoryDecision = {
  decision: string;
  requires_host_action: boolean;
};

export type HostExecution = {
  action: string;
  owner: "host";
};

export type HostLoopInput<Trace, Policy> = {
  trace: Trace;
  policy: Policy;
  analyze: (trace: Trace, policy: Policy) => Promise<AdvisoryDecision>;
  execute: (decision: AdvisoryDecision) => Promise<HostExecution>;
};

export type HostLoopResult = {
  decision: AdvisoryDecision;
  execution: HostExecution;
};

/**
 * Invoke a caller-supplied advisory analysis function, then leave execution to
 * the host callback. This example deliberately contains no controller logic,
 * framework adapter, or tool implementation.
 */
export async function runHostLoop<Trace, Policy>(
  input: HostLoopInput<Trace, Policy>,
): Promise<HostLoopResult> {
  const decision = await input.analyze(input.trace, input.policy);
  const execution = await input.execute(decision);
  return { decision, execution };
}
