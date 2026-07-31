# Research Inspirations and Attribution

This document records how cited work informs the design. Metadata, exact authors,
versions, repository commits, and access dates are in
`docs/sources/source-register.yaml`.

No entry is marked reproduced. An adapted interface or concept is not a reproduction,
and repository compatibility is not claimed unless separately tested.

## Project Synthesis

The scientific research operating system, immutable epistemic kernel, Atomic Claim
Ledger schema, claim-status lifecycle, claim-drift firewall, authority model, four
learning levels, QLoRA quarantine and promotion lifecycle, anti-self-gaming policy,
constitutional governance model, CLI, package boundaries, persistence design, and
combined development-governance workflow are project-specific decisions.

## Source Review

### S01 - ATLAS

- Source/version: arXiv:2606.12386v1.
- Mechanism: iterative ensembles of sparse mechanistic models and experiments selected
  to maximize ensemble disagreement.
- Evidence: eight in-silico runs for each of two bandit-agent recovery problems,
  evaluated for behavioral, structural, and dynamical similarity.
- Limitation: the experiments and reported sample efficiency are domain-specific.
- Adaptation/layer: hypothesis portfolios and discriminating-experiment interfaces in
  ordinary orchestration, not the kernel.
- Transfer risk: high until evaluated in a new scientific domain.
- Licensing/code: paper consulted; no code imported and paper license not relied upon.
- Open validation: test whether information-gain rankings remain useful with imperfect,
  heterogeneous hypotheses.

### S02 - SciConBench and SciConHarness

- Source/version: arXiv:2606.11337v1.
- Mechanism: atomic-fact decomposition, factual precision/recall, and controlled
  clean-room retrieval.
- Evidence: a 9.11K-question benchmark; the reported clean-room comparison uses 268
  recent review questions and finds a best factual F1 of 0.337. Expert validation of
  the model judge shows useful but imperfect agreement.
- Limitation: evidence-based medicine dominates the benchmark, and factual metrics do
  not cover causality, method validity, or scientific importance.
- Adaptation/layer: atomic claim evaluation and clean-room tests in the Claim Ledger
  and evaluation layer.
- Transfer risk: medium; atomic decomposition helps inspection but does not prevent
  unsupported claims.
- Licensing/code: Cochrane-authored content is not reused; repository pinned for
  attribution only.
- Open validation: design domain-neutral claim units and compare deterministic checks
  with independent expert review.

### S03 - Chain of Operators

- Source/version: arXiv:2606.12318v1.
- Mechanism: explicit prompt-side and prediction-side operator compositions around a
  frozen in-context operator model.
- Evidence: two PDE settings plus transfer of one learned chain to another PDE class.
- Limitation: results do not establish general agent orchestration.
- Adaptation/layer: deferred optional scientific-computing plug-in.
- Transfer risk: high.
- Licensing/code: no implementation imported.
- Open validation: define an operator protocol only when a concrete domain requires it.

### S04 - HORMA

- Source/version: arXiv:2606.11680v1.
- Mechanism: separate hierarchical memory construction and navigation-based retrieval,
  with summaries linked to raw trajectories.
- Evidence: ALFWorld, LoCoMo, and LongMemEval comparisons under constrained context.
- Limitation: management quality depends on capable models and retrieval uses RL;
  benchmark efficiency is not scientific reliability.
- Adaptation/layer: deterministic source-linked memory and coverage diagnostics in
  orchestration.
- Transfer risk: medium.
- Licensing/code: paper consulted; no code imported.
- Open validation: measure retrieval omissions and prove that summaries cannot replace
  immutable evidence.

### S05 - EurekAgent

- Source/version: arXiv:2606.13662v2; repository HEAD pinned in the register.
- Mechanism: permissions, artifacts, budgets, isolation, and human supervision as
  first-class environment design.
- Evidence: metric-driven mathematics, GPU-kernel, and seven-task ML evaluations.
- Limitation: executable metrics make these tasks unlike open-ended scientific claims.
- Adaptation/layer: permissions, budgets, artifact boundaries, and intervention points
  in orchestration and execution providers.
- Transfer risk: medium-high.
- Licensing/code: no code imported; compatibility is untested.
- Open validation: test prompt injection, evaluator isolation, budget exhaustion, and
  recovery independently of model quality.

### S06 - AgentBeats

- Source/version: arXiv:2606.13608v2.
- Mechanism: agent-neutral judge/subject interaction using standardized protocols.
- Evidence: a field study with 298 judge agents and 467 subject agents, plus a coding
  case study and harness-swapping experiment.
- Limitation: judge agents can be wrong, and standardized communication does not ensure
  fairness.
- Adaptation/layer: provider-neutral evaluation interfaces in the evaluation layer.
- Transfer risk: medium.
- Licensing/code: no protocol implementation imported; no compatibility claim.
- Open validation: compare judge-agent outputs against deterministic and human checks.

### S07 - HarnessBridge

- Source/version: arXiv:2606.12882v1; repository HEAD pinned in the register.
- Mechanism: learned observation projection and action pass/reject projection while
  retaining raw trajectories.
- Evidence: Terminal-Bench 2.0 and SWE-bench Verified comparisons across several
  generators, primarily using single runs.
- Limitation: coding-only evaluation and possible learned compression or rejection
  errors.
- Adaptation/layer: deterministic typed observation/action adapters in orchestration;
  learned projections remain optional.
- Transfer risk: high.
- Licensing/code: no code imported or compatibility claimed.
- Open validation: independently measure information loss and false rejection before
  any learned projection is enabled.

### S08 - HIPIF

- Source/version: arXiv:2606.10507v1.
- Mechanism: trained subgoal planning, reflection, and folding of completed histories.
- Evidence: three simulated long-horizon agent benchmarks and ablations.
- Limitation: structured simulated environments, RL training, and structured output
  assumptions.
- Adaptation/layer: explicit plan trees and source-linked checkpoint views in
  orchestration, without process rewards.
- Transfer risk: high.
- Licensing/code: no implementation imported.
- Open validation: verify completion criteria deterministically where possible and
  measure summary loss.

### S09 - AHOIS

- Source/version: arXiv:2606.26722v1.
- Mechanism: a question-only Socratic physics critic separated from strategy,
  execution, and integrity roles.
- Evidence: a real multimode-fibre platform, several discovery tasks, and critic
  ablations under human supervision.
- Limitation: natural-language ambiguity, human safety gating, and one physical domain.
- Adaptation/layer: non-authoritative critic interface in orchestration.
- Transfer risk: high.
- Licensing/code: paper consulted; no code imported.
- Open validation: test whether critic challenges improve falsifiability without
  becoming a second proposer or circular evaluator.

### S10 - NAVI-Orbital

- Source/version: arXiv:2606.18271v1.
- Mechanism: persistent graph-based state orchestration, dedicated model roles, local
  inference, and semantic compression under resource constraints.
- Evidence: a 7,960-image ground benchmark, flatsat checks, and two live in-orbit
  captures.
- Limitation: small flight sample, no adversarial dialogue study, and no bound on VLM
  hallucination from retries or regex validation.
- Adaptation/layer: state persistence and bounded roles in orchestration.
- Transfer risk: medium.
- Licensing/code: no flight-system code imported.
- Open validation: test crash recovery and retain source artifacts behind every
  semantic view.

### S11 - GeoCert

- Source/version: arXiv:2604.23474v1.
- Mechanism: forecasting on a hyperbolic constraint manifold with generated
  constraint-satisfaction certificates.
- Evidence: time-series benchmark comparisons and theoretical statements conditional
  on regularity assumptions and encoded constraints.
- Limitation: the named repository was unavailable; broad certification and transfer
  claims require independent review.
- Adaptation/layer: deferred conditional-verification plug-in, excluded from the
  immutable kernel.
- Transfer risk: very high.
- Licensing/code: no code available or imported.
- Open validation: independently inspect proofs, reproduce experiments, and define
  exactly what a certificate establishes before integration.

### S12 - Mnemosyne

- Source/version: arXiv:2607.00269v2; paper-linked repository unavailable.
- Mechanism: untrusted proposals, deterministic admission under a declared constraint
  set, append-only committed transitions, effective-state projection, and bounded
  evidence-preserving repair.
- Evidence: nine falsification tests, bounded live-proposer pilots, reported overhead,
  and proofs relative to a finite constraint set and gate closure.
- Limitation: constraint completeness and gate closure are assumptions; the Python
  implementation does not have a machine-checked noninterference proof.
- Adaptation/layer: proposal/admission authority pattern in the immutable kernel.
- Transfer risk: high if transaction validity is confused with scientific truth.
- Licensing/code: repository was unavailable and no code was imported.
- Open validation: test gate closure, concurrency, idempotency, audit replay, and every
  scientific-domain invariant independently.

### S13 - Space Is Intelligence

- Source/version: arXiv:2606.18828v1.
- Mechanism: scene-conditioned Riemannian metric generation for geodesic planning.
- Evidence: one 2D training scene, selected tests, dense stress tests, and 100 random
  synthetic scenes.
- Limitation: 2D point robots with explicit keypoints; higher dimensions and perception
  are unevaluated.
- Adaptation/layer: deferred specialized plug-in.
- Transfer risk: very high.
- Licensing/code: no code imported.
- Open validation: establish a concrete scientific-planning use case before designing
  any interface.

### S14 - Lithium POMDP

- Source/version: arXiv:2606.18598v1; repository HEAD pinned in the register.
- Mechanism: belief-state planning that trades exploration, production, profit, and
  emissions under uncertainty.
- Evidence: simulated comparisons against human-inspired heuristics across several
  price models.
- Limitation: exogenous prices and simplified demand and environmental models; results
  depend on the selected reward.
- Adaptation/layer: deferred value-of-information planning plug-in.
- Transfer risk: high.
- Licensing/code: no domain model or reward code imported.
- Open validation: define non-scalar scientific value and belief calibration before
  adapting a planner.

### S15 - OpenAgent

- Source/version: arXiv:2607.01084v1; repository HEAD pinned in the register.
- Mechanism: controlled shifts in query, action, observation, and domain distributions.
- Evidence: 6,050 synthetic training samples and 880 evaluation samples comparing SFT
  and RL agents plus a perturbation-augmented intervention.
- Limitation: synthetic setting and one backbone family; robustness does not imply
  scientific validity. The arXiv acceptance note was not independently confirmed in
  ICML proceedings.
- Adaptation/layer: perturbation and abstention tests in evaluation.
- Transfer risk: medium.
- Licensing/code: no code imported or compatibility claimed.
- Open validation: create domain-neutral perturbations and check that tests do not
  expose hidden evaluation answers to training.

### S16 - HASE

- Source/version: arXiv:2607.03935v1.
- Mechanism: co-evolution of solutions and whitelisted harness components, with
  editable local evaluators anchored by an immutable external oracle.
- Evidence: text classification, alpha mining, circle packing, and Heilbronn triangle
  tasks.
- Limitation: four optimization settings; current mismatch agreement cannot prove a
  repaired evaluator correct on future candidates.
- Adaptation/layer: quarantined governance proposals; correctness-defining assets are
  not self-editable.
- Transfer risk: high.
- Licensing/code: no code imported.
- Open validation: test that optimizers cannot see or modify holdouts, thresholds,
  metrics, or promotion policy.

### S17 - AlphaEvolve

- Source/version: arXiv:2506.13131v1 white paper.
- Mechanism: evolutionary candidate-program search driven by one or more automated
  evaluators.
- Evidence: reported mathematical discoveries and production infrastructure
  optimizations with executable evaluation.
- Limitation: requires meaningful machine-gradeable evaluators; many natural-science
  questions do not provide them.
- Adaptation/layer: deferred optional program-search provider.
- Transfer risk: high where a proxy can replace scientific validity.
- Licensing/code: no AlphaEvolve implementation is imported.
- Open validation: require evaluator validity, holdout separation, and independent
  review for each proposed use.

### S18 - QLoRA

- Source/version: NeurIPS 2023 paper and arXiv:2305.14314v1; official repository HEAD
  pinned in the register.
- Mechanism: gradients pass through a frozen quantized base model into low-rank
  adapters, using NF4, double quantization, and paged optimizers.
- Evidence: more than 1,000 fine-tunes across datasets, model families, and scales.
- Limitation: parity with full 16-bit tuning was not established at every large scale;
  the paper documents benchmark and automated-judge limitations.
- Adaptation/layer: optional quarantined procedural-learning provider.
- Transfer risk: medium; efficient training says nothing by itself about scientific
  validity, alignment, contamination, or promotion safety.
- Licensing/code: no code vendored; provider compatibility is untested.
- Open validation: contamination tests, role-specific evaluations, rollback, and
  independent promotion review.

### S19 - Superpowers

- Source/version: installed plug-in v6.1.1 at Git tag commit `d884ae0`.
- Mechanism: brainstorming, written design, planning, isolated work, TDD, systematic
  debugging, review, verification, and deliberate branch completion.
- Evidence: software-development methodology; no scientific-validity evidence is
  claimed by this project.
- Limitation: it governs development rather than research conclusions.
- Adaptation/layer: development governance only.
- Transfer risk: low when represented as workflow evidence rather than runtime truth.
- Licensing/code: MIT; installed files are followed but not vendored.
- Open validation: capture artifact references without coupling runtime behavior to the
  external product.

### S20 - RepoQualityGate

- Source/version: local `SKILL.md`, SHA-256 recorded in the source register.
- Mechanism: spec-first design, testing, maintainability, auditability, and final
  quality review.
- Evidence: development instructions; no empirical evaluation is declared.
- Limitation: no canonical repository, author, version, or redistribution license is
  available. Local history identifies it as a locally created skill.
- Adaptation/layer: development governance only.
- Transfer risk: medium because provenance and redistribution terms are incomplete.
- Licensing/code: not copied or distributed.
- Open validation: keep the repository quality command independently specified and
  record only the local skill hash and result.

### S21-S29 - Governed adaptation and harness evolution

- **S21**, arXiv `2607.07663v1` (CC BY 4.0), supplies vocabulary for
  bounded adaptation, loop closure, grounding, verification hierarchy, collapse, and
  governance measurement. SSOH adapts the classification but treats the survey's
  observations as contingent, not formal laws.
- **S22**, arXiv `2607.13104v1` and the MIT-licensed companion repository at
  `06a48f9beddeb0ff711a3f63be857e3e95709923`, separates foundation-model
  configuration, scaffold configuration, and execution state. SSOH persists metadata
  only and adds its own strict version, rollback, and admission contracts; the
  surveyed systems are not reproduced.
- **S23**, arXiv `2607.08964v2` (CC BY 4.0), motivates dense long-horizon
  diagnostics. SSOH retains complete trajectories and failed attempts while refusing
  to equate partial progress with final success.
- **S24**, arXiv `2607.09328v1` under the arXiv non-exclusive distribution
  license, motivates source-first natural evidence trails and separated validation
  stages. In SSOH a trail remains evidence, not proof.
- **S25**, arXiv `2607.09560v1` under the arXiv non-exclusive distribution
  license, identifies vocabulary and verifier gaps. Representational primitives remain
  deferred and quarantined until independently checked under source-controlled rules.
- **S26**, arXiv `2607.12227v1` (CC BY 4.0), motivates matched
  search/inference budgets, protected transfer, and causal attribution. Its companion
  repository is pinned at `ffd1ba1c2c3e31099264f630b9ed44aec63a86a7`, has no
  license file, and is subject to a no-code-reuse boundary.
- **S27**, arXiv `2607.13091v1` under the arXiv non-exclusive distribution
  license, inspires retained feedback and pre-submission rule review. SSOH's conflict,
  redundancy, independent-review, and consolidation design is original synthesis.
- **S28**, arXiv `2607.13285v1` (CC BY 4.0), motivates behavior-centric
  navigation and progressive disclosure. SSOH uses a deterministic, rebuildable,
  non-authoritative source index and does not reproduce the paper's LLM-assisted
  generation system.
- **S29** is the unlicensed public GitHub Pages repository pinned at
  `d907a3c18ac97fe6bf7b0bbe43ba938acb023b72`. It is architectural inspiration
  only for a generic hypothesis-model-checker-revision-admission loop, is not peer
  reviewed, and reports results that were not independently verified. No source code,
  benchmark-specific logic, or hidden task assumptions enter SSOH.

All nine sources have `reproduction_status: not_reproduced` in the source register.
Their exact consulted versions, repository commits, licenses, evidence boundaries, and
limitations are recorded there.

## Architecture Attribution Matrix

| Project capability | Primary inspiration | Project-specific adaptation |
| --- | --- | --- |
| Competing hypothesis portfolio | S01 | Generalized beyond ATLAS's experimental domain |
| Atomic conclusion evaluation | S02 | Integrated into the Claim Ledger |
| Operator-chain plug-ins | S03 | Deferred optional domain adapter |
| Hierarchical memory | S04 | Deterministic first implementation |
| Permission, artifact, and budget environment | S05 | Combined with transactional governance |
| Agent-neutral evaluation | S06 | Deterministic tests remain authoritative where possible |
| Observation/action controller | S07 | Typed deterministic adapters before learned adapters |
| Subgoal planning and context folding | S08 | Raw evidence cannot be discarded |
| Socratic scientific criticism | S09 | Authority-separated critic role |
| Explicit orchestration state machine | S10 | Generalized beyond orbital vision systems |
| Conditional certification | S11 | Deferred and explicitly assumption-relative |
| Proposal/admission separation | S12 | Extended to scientific claims and adapter governance |
| Geometric planning | S13 | Deferred specialized plug-in |
| Belief-state planning | S14 | Deferred research value-of-information plug-in |
| Open-world testing | S15 | Added to regression and adapter evaluation |
| Harness evolution | S16 | Quarantined, externally governed proposals only |
| Evolutionary program discovery | S17 | Restricted to valid executable evaluators |
| Quantized adapter learning | S18 | Procedural learning with promotion and rollback |
| Software-development discipline | S19 | Required development-governance workflow |
| Repository admission gate | S20 | Exact local skill followed; project command independently defined |
| Governed adaptation classification | S21, S22 | Typed persistence and authority rules with complete measurements |
| Dense progress diagnostics | S23 | Full trajectory retained; final validation remains separate |
| Natural evidence trails | S24 | Source-first append-only graph with independent checks |
| Representational primitives | S25 | Deferred source-controlled quarantine |
| Fair harness evaluation | S26 | Matched budgets, protected transfer, and explicit confounds |
| Behavioral-rule retention | S27 | Independent review and governed consolidation |
| Behavior-to-code handbook | S28 | Deterministic rebuildable source projection |
| Hypothesis/checker loop | S29 | Domain-neutral contracts with independently specified safety boundaries |

## Language Rules

Repository documentation uses qualified terms such as "inspired by," "adapted
from," "conditionally verifies," "not yet reproduced," and "correct relative to
declared constraints." It does not claim to solve hallucination, guarantee truth,
provide unconditional formal safety, generalize without a stated distribution,
reproduce untested work, or support untested compatibility.
