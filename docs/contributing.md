# Contributing to DAM

Thank you for contributing to the Detachable Action Monitor. This guide covers essential information for developers.

---

## Quick Setup

```bash
git clone https://github.com/ez945y/DAM.git && cd DAM

# Fork, clone your fork, add upstream
git remote add upstream https://github.com/ez945y/DAM.git

# One-time setup
make setup

# Start the dev server (backend + Next.js hot-reload)
make dev

# Create a development branch off `dev`
git checkout -b feat/my-feature  # or fix/bug-id, issue/42
```

---

## Branch & Release Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Stable releases only (protected). |
| `dev` | Integration branch. All PRs target here. |
| `feat/*`, `fix/*`, `issue/*` | Development branches. Cut from `dev`, merge back via PR. |

**Workflow:** `dev` → accumulate features → merge to `main` → release tags

---

## Required: Tests & Green CI

### Non-Negotiable Rules

1. **Every PR must include tests** for changed behavior
2. **All CI checks must pass** before merge (no "fix after merge")
3. **Safety regressions must be written** if guard logic changes:
   ```bash
   pytest tests/safety/ -v -x
   ```

### Test Locally

```bash
# Pure Python (always works)
pytest tests/ -m "not hardware and not rust and not ros2"

# Full suite (if Rust compiled)
make test
```

---

## Commit & PR Format

Follow Conventional Commits with DAM scopes:

```
<type>(<scope>): short description

<optional body>

Closes #42
```

**Types:** `feat` `fix` `test` `refactor` `docs` `ci`
**Scopes:** `core` `guard` `boundary` `adapter` `rust` `stackfile` `cli`

**Examples:**
```
feat(guard): L2 motion guard with joint limit clamping
fix(boundary): handle empty node list in advance()
test(guard): add regression for velocity clamping
```

**PR Description:** Include what changed, why, and how you tested it.

---

## Adding Guards

Guards enforce safety constraints. See [Guards Reference](guards-reference.md) for the full guard layer specification.

### Skeleton

```python
from dam.guard.base import Guard
from dam.types import Observation, ActionProposal, GuardResult
import dam

@dam.guard(layer="L2")  # L0, L1, L2, L3, or L4
class MyGuard(Guard):
    """Guard description."""

    def check(self, obs: Observation, action: ActionProposal, my_param: float) -> GuardResult:
        if bad_condition(obs, action, my_param):
            return GuardResult.reject(reason="why", guard_name=self.get_name())
        return GuardResult.pass_(guard_name=self.get_name())
```

**Critical:** Raise exceptions in `check()` — they're caught and converted to REJECT (fail-to-reject principle).

Place in `dam/guard/builtin/` for built-in guards or anywhere for custom ones.

Add to Stackfile:
```yaml
guards:
  custom:
    - class: mypackage.MyGuard
      layer: L2
      params:
        my_param: 0.5
```

---

## Adding Adapters (Hardware/Policy)

DAM uses duck typing. No base class required; implement these methods:

### Source (Sensors)
```python
def read(self) -> Observation:
    """Return latest sensor snapshot."""
```

### Policy
```python
def predict(self, obs: Observation) -> ActionProposal:
    """Run inference."""
```

### Sink (Hardware)
```python
def apply(self, action: ValidatedAction) -> None:
    """Send validated action to hardware."""

def get_hardware_status(self) -> dict:  # optional
    """Return motor health, temperature, etc. for L4 guard."""
```

Register with runtime:
```python
runtime.register_source("main", MySource())
runtime.register_policy(MyPolicy())
runtime.register_sink(MySink())
```

---

## Code Quality

### Python

- **Type hints:** All public interfaces, checked with `mypy --strict`
- **Formatting:** `ruff format` (black-compatible)
- **Linting:** `ruff check` — zero warnings
- **Immutability:** Use `@dataclass(frozen=True)` for core types
- **State:** No mutable module-level state (except registries)

### Pre-commit (Highly Recommended)

This project uses `pre-commit` to ensure code quality on every commit. It automatically runs `ruff`, `mypy`, and `cargo fmt`.

- **Install Hooks:** `make setup-precommit` (already included in `make setup`)
- **Run Manually:** `pre-commit run --all-files`
- **Skip Temporarily:** `git commit --no-verify` (use only if necessary)

```bash
ruff format dam/ tests/
ruff check dam/
mypy --strict dam/
```

### Rust (`dam-rust/`)

- **Format:** `cargo fmt`
- **Lint:** `cargo clippy -- -D warnings`
- **`unsafe` blocks:** Only at PyO3 boundaries. Must have `// SAFETY:` comment.
- **Tests:** `cargo test --release`

```bash
cd dam-rust
cargo fmt --check
cargo clippy -- -D warnings
cargo test --release
```

---

## The Fail-to-Reject Principle

This is non-negotiable. **Any timeout, exception, or error in guard execution must result in immediate action rejection.**

When designing guards:
- Let exceptions propagate (framework catches them → REJECT)
- Don't hide failures with try-except-pass
- Set conservative defaults if uncertain
- Guard timeouts → REJECT (watchdog enforces this)

This is how DAM provides safety guarantees.

---

## License

By contributing, you agree your work is licensed under [Mozilla Public License 2.0](LICENSE).

---

## Questions?

- Check [GitHub Discussions](https://github.com/ez945y/DAM/discussions)
- See [Full Documentation](https://ez945y.github.io/DAM/)
