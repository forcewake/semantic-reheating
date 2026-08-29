# Clean-checkout reproduction

This procedure independently reproduces a committed candidate from a detached
local clone. It is a **local verification gate**, not a publication or an
external certification.

## Prerequisites

Prepare the lockfile-backed dependency caches before asking the verifier to run
offline. From a clean repository checkout, run:

```bash
uv sync --all-groups
npm ci --prefix examples/typescript-middleware
npm ci --prefix tools/assets
rm -rf examples/typescript-middleware/node_modules tools/assets/node_modules
```

The verifier requires the Python cache to be available because it runs
`UV_OFFLINE=1 uv sync --frozen --offline --all-groups`. It also uses locked npm
installs in offline mode. If an offline cache is missing, fetch only the
packages selected by the existing lockfiles, then repeat the offline command;
do not treat a network-backed first attempt as reproducible evidence.

## Reproduce the current clean candidate before commit

Run this from the repository root. The clone is a caller-selected, disposable
safe leaf under the system temporary directory. The receipt directory is
external state, never a repository directory.

```bash
set -euo pipefail
FEATURE_ROOT=$(pwd)
RECEIPT_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/releases"
rm -rf /tmp/semantic-reheating-clean-checkout
mkdir -p "$RECEIPT_DIR"
uv run python tools/clean_checkout_verify.py \
  --clone-dir /tmp/semantic-reheating-clean-checkout \
  --receipt "$RECEIPT_DIR/precommit-check.json"
cd /tmp/semantic-reheating-clean-checkout
git status --short
git diff --check
cd "$FEATURE_ROOT"
```

The gate clones the exact current `HEAD` with `git clone --no-local`, checks out
the resolved SHA detached, and verifies the clone is clean. It then performs
locked offline Python installation, installed CLI help, fixture validation,
deterministic benchmark replay with byte comparison, the generic Python
example, locked TypeScript install/typecheck/test, article and cached asset
checks, registry validation, the deterministic Python suite, public tracked and
history hygiene, explicit tracked/object artifact-path checks, and final Git
cleanliness checks. It does not invoke a pressure-live or provider-backed
executor.

## Reproduce an exact release candidate after commit

Use the commit-derived filename below rather than adding the receipt to Git.

```bash
set -euo pipefail
RELEASE_SHA=$(git rev-parse HEAD)
RECEIPT_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/semantic-reheating/releases"
FINAL_RECEIPT="$RECEIPT_DIR/clean-checkout-${RELEASE_SHA}.json"
rm -rf /tmp/semantic-reheating-clean-checkout
uv run python tools/clean_checkout_verify.py \
  --commit "$RELEASE_SHA" \
  --clone-dir /tmp/semantic-reheating-clean-checkout \
  --receipt "$FINAL_RECEIPT"
uv run python -c "import json,sys; r=json.load(open(sys.argv[1])); assert r['commit_sha']==sys.argv[2] and r['status']=='pass'" "$FINAL_RECEIPT" "$RELEASE_SHA"
test -z "$(git status --porcelain)"
```

## Receipt contract and invalidation

The external JSON receipt is canonical (sorted compact JSON with a trailing
newline) and has a closed schema: version, exact commit SHA, pass/fail status,
archive SHA-256 and byte size, and ordered named gate outcomes. It intentionally
contains no raw command output, local paths, credentials, or source-controlled
self-reference. A safely established external target receives a typed failure
receipt when a gate fails; unsafe paths are rejected before writing.

Any repository commit changes the candidate and invalidates an earlier receipt.
After every commit, rerun the exact-release procedure and retain only the new
external receipt for the SHA being considered.
