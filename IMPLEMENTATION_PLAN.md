# Reliability and dynamic dataset implementation

Baseline: `f3b0a5d`. Keep CodeActV2's arbitrary scientific Python. This plan is
implementation work, not a request for approval. Production data, credentials,
old research records and pinned NOOA sources must remain untouched.

## 1. Repair confirmed failures

- [x] Correct native skills wiring; verify actual model messages and essential rules.
- [x] Deliver saved canonical results after recoverable writer failures; preserve cancellation.
- [x] Accept optional reference-chamber tables consistently.
- [x] Reject stale frontend responses and coalesce event refreshes.
- [x] Isolate per-sample operators; fit learned operators inside training folds.

Acceptance: convert the review counterexamples to meaningful regression tests.

## 2. Versioned, multimodal datasets

- [x] Immutable DatasetRef manifests, content-addressed assets and SQLite metadata.
- [x] CodeAct-generated arbitrary derivations: parents, source, parameters, reason,
      modality, schema, sample identity, split/group roles and output hashes.
- [x] Agent-scoped selection stack, parent/root return, persisted recovery and lineage.
- [x] Table, time-series and file-asset/image/text adapters; preserve etching import.
- [x] Bind scientific execution, evidence, branches and formula protocols to references.

Acceptance: root -> subset -> nested subset -> parent -> root, restart, branching,
scope-specific evidence, formula comparison on the selected subset, input tamper
rejection and multimodal fixtures. An import/derivation may not overwrite parents.

## 3. Abstract acquisition boundary (user-scoped revision)

The user explicitly deferred actual data-source connectors and knowledge graph
integration. Do not access or implement those services in this change.

- [x] A typed, host-owned acquisition request/result interface with source,
      selection criteria, provenance and newly supplied entry identities.
- [x] An injectable provider boundary for a future unified fetch implementation;
      unavailable providers report pending/unavailable, never pretend to fetch.
- [x] Import already supplied local assets as immutable versions and record parent
      references; arbitrary merge/transform code creates a new dataset.

Acceptance: an in-process test provider supplies new entries through the same
interface; unconfigured acquisition is explicitly unavailable; old frozen studies
remain valid. No HTTP/SQL/MCP/KG connector or real service access in scope.

## 4. Task-aware prompts and bounded runtime

- [x] Task/role/dataset context; lightweight plotting avoids mandatory scientific review.
- [x] Bounded historical context, purpose-aware reservation under the user's model caps.
- [x] Bounded worker output and event batching; distinct review/delivery failure states.
- [x] Incremental artifact publishing and deduplicated snapshots with narrow DB locks.

Acceptance: plot-only, discussion, exploratory and formal comparison routes;
large-output, long-history, replay, cancellation and budget regressions.

## 5. Product integration and delivery

- [x] Vue dataset browser, lineage, current scope, switch/return and local import UI.
- [x] Persistent SSE dataset events, multi-agent ownership and reconnect state.
- [x] Sphinx requirements, architecture, protocols, examples and Mermaid diagrams.
- [x] Full Python/Vue/build/docs checks, native NOOA + real-worker end-to-end acceptance.
- [ ] Browser verification, public GitHub push/Pages and precise capability report.

Update this checklist with executable evidence as stages finish. Do not represent
test fixtures as an independent production diagnosis or claim untested modalities.

## Executed evidence (2026-10-17)

- Full Python regression: 83 passed, 100 upstream warnings, macOS/Python 3.12.
- Vue: 11 passed; TypeScript/Vite production build passed.
- Ruff passed. Strict Sphinx: 26 HTML pages, 24 rendered Mermaid SVGs; local links and Chinese search checks passed.
- Native CodeAct plus real scientific worker exercises a generated subset script, persistent scope restoration and return.
- Real GLM generated and executed both subset scripts and verified 90 -> 10 -> 4 -> 10 -> 90 on existing discovery data. Its initial invalid sample-ID evidence was rejected and corrected; independent review reached the 600-second acceptance deadline. This is not recorded as a passed end-to-end review. A read-only real-model follow-up delivered in 38.5 seconds with verified references and not_required/delivered status, explicitly preserving the prior review timeout. Browser selection, lineage and return-to-root were exercised. No credentials or research records are published.
- External data-source connectors and knowledge graph integration remain explicitly deferred by the user. Only the typed injectable boundary and already supplied local imports are implemented.
