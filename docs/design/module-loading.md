# Module loading and performance acceptance

Date: 2026-11-15. Baseline: main ef847ee.
Status: explicit selective import loading adopted after measurement. Shared UI loads eagerly; sourced fixtures and human commands load import and interactive settings, while unrelated internal callbacks skip them. Import callbacks load import explicitly; `_demo` keeps its rendering primitives in core. Unknown underscore-prefixed searches load import at the default dispatch. Performance guardrails remain in AGENTS.md.

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

## Measurements

On macOS arm64, nine untraced samples at 100×24 with private data and a private tmux socket gave 500-host compact warm `_list` medians of 46.2 ms (baseline ef847ee), 46.6 ms (eager 9732cb9), and 46.0 ms (selective f60f033). Full samples, cold list/index timings and 20-host results are in `docs/superpowers/progress/performance-{baseline,eager,selective}-isolated.json`. Earlier reports included a real tmux session and are retained as historical evidence, not isolated baseline comparisons.

With a private tmux server, 500 hosts and one session with two live panes, fifteen alternating samples showed compact `_list` median 51.91 → 45.87 ms, minimal 46.00 → 40.59 ms and cards 54.04 → 48.27 ms after collecting pane counts in one tmux query. Compact/card maximums were 47.96/50.47 ms; minimal had one 73.53 ms outlier. These are workload-specific measurements, not a guarantee for arbitrary host or session counts. The existing resize and preview-toggle binds synchronously reload `_list`; ordinary pointer moves use inline footer updates.

The 500-host missing-index `_list` single samples were 2085 ms baseline, 1949 ms eager and 1922 ms selective; these include initial index rebuild and are not repeated warm callbacks. This existing cold-start cost needs separate investigation; one sample per revision is insufficient to establish its spread. At 20 hosts, three private-tmux 100×24 launch-to-visible samples with 5 ms polling gave baseline → current medians of 101.6 → 100.6 ms for settings and 113.4 → 113.9 ms for import confirmation. Those include terminal/process startup, not just module loading. TUI smoke sent ten down keys in 46 ms; it does not establish the per-key pointer budget.
