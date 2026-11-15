# Module loading and performance acceptance

Date: 2026-11-15. Baseline: main ef847ee.
Status: selective loading approach proposed for the planned module extraction;
not implemented or benchmarked. Performance guardrails remain in AGENTS.md.

## Context

The current Bash entry script defines feature functions before its main dispatch.
Internal callbacks such as `_preview` can start another Bash process and parse
the script again. Splitting it into files improves maintenance but does not
automatically improve performance if every invocation sources every module.

Sourcing a file parses its function definitions and executes its top-level code.
It does not call every defined function or itself require a child process.
Avoiding unnecessary parsing can help; avoiding expensive top-level work is more
important. The actual savings must be measured, not inferred from file length.

## Proposed approach

Use explicit loading at command/flow entry points, with small shared helpers
loaded normally. Keep `--version` ahead of feature loading. A preview invocation
should load its actual rendering/data dependencies, not import, installer or
settings flows. Entering import loads import's dependencies before using them.

This is a small form of lazy loading: defer loading a feature until needed.
Do not introduce an automatic dependency resolver, plugin registry, background
preloader or function-autoload framework. Explicit dependencies are easier to
review in this Bash app. Do not defer a trivial shared helper merely to satisfy
a rule that every function must be loaded separately.

## Alternatives and tradeoffs

- Source all modules: simplest initial extraction and useful as a behavioral
  baseline, but may retain unnecessary parsing/startup work on hot callbacks.
- Explicit selective loading: preferred where it removes unrelated work without
  complex dependency handling. It can move cost to first feature use, which must
  also be checked.
- Generic automatic lazy loader: deferred; current needs do not justify the
  dependency/cycle/error-handling machinery.

Each callback process has its own shell state: a module loaded in the interactive
parent is not automatically loaded in a new callback process. Preserve the real
dependency order, shared variables, return codes and Bash 3.2 compatibility.
Resolve files relative to the installed app, and test install/update plus
callbacks launched from another directory on supported platforms.

## Acceptance and reconsideration

Use AGENTS.md's budgets and measurement method. Compare the same workload on the
same machine before/after, including warm callbacks, first entry into a feature
and larger fixtures where work scales with host count. Functional success does
not prove performance-budget compliance.

Keep the simplest loading scheme that meets these requirements. Reconsider a
more elaborate loader only if measured startup cost or concrete dependency
maintenance problems justify it. Record the resulting numbers and final choice
here or in the implementation PR; update this status when the decision is adopted.

## Deferred technical debt

New regressions must be corrected. Existing violations in the changed flow must
be addressed or their limits explicitly agreed before acceptance. Unrelated
existing debt must not silently expand an otherwise scoped task.

Record a confirmed, deferred problem in a GitHub issue with:

- affected command/code and user-visible impact;
- revision, fixture size, environment, reproduction/measurement command and
  observed timings versus budget;
- reason for deferral, agreed limitation and trigger for revisiting it;
- measurable completion criteria and linked follow-up work.

Link the issue from relevant guidance or a temporary workaround rather than
duplicating a debt ledger. Use `shortcut:` only for an actual code workaround.
Do not open debt for an unverified suspicion. Reporting "no regression" must not
be presented as "within performance budget" when the baseline still exceeds it.
