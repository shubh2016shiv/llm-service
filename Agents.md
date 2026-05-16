# Python Enterprise Clean Code — Agent Instruction File

**Version:** 2.0  
**Scope:** All Python source code, tests, and configuration in this repository  
**Tools:** Claude Code, Cursor, GitHub Copilot, Codex CLI  
**Last Updated:** 2026-04-28

---

## Agent Prime Directive

> *"A new hire should open this repository and know exactly where to look within 30 seconds.  
> An AI agent should never need to ask 'where does this go?'"*

Before writing a single line of code, an agent must:

1. Read this file in full.
2. Read the nearest `__init__.py` for module-level architecture.
3. Scan adjacent files for existing patterns.
4. Ask: *"Am I adding to the system, or am I duplicating it?"*

If the answer is unclear — **stop and surface the ambiguity** rather than guess and proliferate.

---

## Table of Contents

1. [Project Structure: The Module Decision Framework](#1-project-structure-the-module-decision-framework)
2. [Naming Conventions: The Intuitive Contract](#2-naming-conventions-the-intuitive-contract)
3. [SOLID Principles: Enforcement Rules](#3-solid-principles-enforcement-rules)
4. [Clean Code: Non-Negotiable Rules](#4-clean-code-non-negotiable-rules)
5. [Separation of Concerns: Layered Architecture](#5-separation-of-concerns-layered-architecture)
6. [Testing Standards](#6-testing-standards)
7. [Agent-Specific Instructions](#7-agent-specific-instructions)
8. [Progressive Disclosure for Large Projects](#8-progressive-disclosure-for-large-projects)
9. [Quick Reference Checklist](#9-quick-reference-checklist)
10. [File Template for New Modules](#10-file-template-for-new-modules)

---

## 1. Project Structure: The Module Decision Framework

This is the biggest architectural lever. Use this framework every time you are about to create a file.

### 1.1 When to Create a New Module

| Trigger | Action | Example |
|---|---|---|
| Single Responsibility Violation | A file handles >1 distinct concern (I/O + logic + validation) | Split `user_utils.py` → `user_validator.py`, `user_repository.py`, `user_service.py` |
| Import Cycle Risk | Two files import each other | Extract shared interface into `contracts/` or `interfaces/` |
| Reusability Boundary | Code is needed by 2+ unrelated features | Move to `shared/` or `core/` |
| Test Isolation Need | Testing requires heavy mocking of unrelated dependencies | Split into smaller, independently testable modules |
| Team Ownership Boundary | Different sub-teams maintain different parts | Mirror team boundaries in directory structure |

### 1.2 When to Create a New Submodule (Directory with `__init__.py`)

| Trigger | Action | Example |
|---|---|---|
| >3 Related Files | A feature has models, services, repositories, and exceptions | `payments/` → `models.py`, `services.py`, `repositories.py`, `exceptions.py` |
| Public API Surface | You need to control what `from package import *` exposes | Use `__init__.py` to define `__all__` |
| Layered Architecture | You have clear layers (API, business, persistence) | `src/infrastructure/persistence/` |
| Framework Adapter | Wrapping a third-party library | `src/adapters/redis_client/`, `src/adapters/s3_storage/` |

### 1.3 Forbidden Patterns

- **God Modules:** No file over **500 lines**. Split by responsibility.
- **Monolithic Scripts:** No single script that handles ETL, validation, and notification. Separate concerns.
- **Deep Nesting:** Maximum directory depth of **4**. If deeper, flatten or extract a package.
- **Circular Imports:** Never. Use `Protocol` interfaces or dependency injection to break cycles.
- **In-Memory Hoarding:** Never load unbounded datasets into memory. Return iterators or generators (`yield`) for database queries or file processing that could exceed **1,000 records**. Use pagination or chunking for large result sets.

### 1.4 Directory Conventions (The "Unwritten Contract")

These names are standardized across the industry. Use them so anyone knows where to look:

| Directory | Purpose | What Lives Here |
|---|---|---|
| `core/` | Domain-agnostic infrastructure | Config, exceptions, logging, base classes |
| `domain/` or `models/` | Business entities and rules | Pydantic models, enums, value objects |
| `services/` | Business logic orchestration | Use cases, workflows, calculators |
| `repositories/` or `adapters/` | Data access abstraction | Database queries, API clients, file I/O |
| `interfaces/` or `api/` | External entry points | FastAPI routers, CLI commands, event handlers |
| `contracts/` or `protocols/` | Abstract interfaces | Protocol classes, abstract base classes |
| `utils/` | **Avoid.** Use specific names instead | `date_parser.py`, `csv_serializer.py` |

---

## 2. Naming Conventions: The Intuitive Contract

Names must be self-documenting, grep-friendly, and intention-revealing. A name should answer *"what does this do?"* not *"what is this?"*

### 2.1 Module & Package Names

| Pattern | Example | Anti-Pattern |
|---|---|---|
| Specific noun phrases | `invoice_generator.py`, `payment_processor.py` | `utils.py`, `helpers.py`, `misc.py` |
| Layer indicator suffix | `user_repository.py`, `order_service.py` | `user_db.py`, `order_logic.py` |
| Verb for scripts | `sync_customers.py`, `purge_cache.py` | `customer.py`, `cache.py` |

### 2.2 Class Names

| Pattern | Example | Anti-Pattern |
|---|---|---|
| Noun or noun phrase | `CustomerInvoice`, `PaymentGateway` | `Manager`, `Handler`, `Processor` (too vague) |
| Interface/Protocol suffix | `PaymentInterface`, `StorageProtocol` | `IPayment`, `Paymentable` |
| Exception suffix | `InvalidPaymentError`, `CustomerNotFoundError` | `PaymentException`, `Error` |

### 2.3 Function & Method Names

| Pattern | Example | Anti-Pattern |
|---|---|---|
| Verb + Object + Context | `calculate_order_total`, `send_invoice_email` | `calc`, `handle`, `process` |
| Boolean predicates | `is_payment_expired`, `has_active_subscription` | `check_payment` |
| Factory methods | `create_guest_user`, `build_invoice_from_order` | `get_user`, `make_invoice` |

### 2.4 Variable Names

| Pattern | Example | Anti-Pattern |
|---|---|---|
| No abbreviations | `customer_email_address` | `cust_email`, `ce` |
| Units in name | `timeout_seconds`, `price_cents` | `timeout`, `price` |
| Collection type in name | `active_user_ids`, `failed_payment_records` | `users`, `payments` |

---

## 3. SOLID Principles: Enforcement Rules

### 3.1 Single Responsibility Principle (SRP)

**Rule:** One class = one reason to change. One function = one action.

**Check:** Ask *"If requirement X changes, does this file need to change?"* If yes for multiple X's, split it.

- **Max Function Length:** 20 lines. Split if longer.
- **Max Class Length:** 300 lines. Extract helper classes if longer.

### 3.2 Open/Closed Principle (OCP)

**Rule:** Extend behavior without modifying existing code.

**Pattern:** Use `Protocol` interfaces and dependency injection. Add new implementations; don't edit existing ones.

**Forbidden:** Adding `if/elif` chains to handle new types. Use a registry or factory instead.

### 3.3 Liskov Substitution Principle (LSP)

**Rule:** Subtypes must be substitutable for their base types without altering correctness.

**Check:** If `Bird` has `fly()`, `Penguin` cannot inherit `Bird`. Use composition: `Penguin` has a `Walker` behavior.

### 3.4 Interface Segregation Principle (ISP)

**Rule:** Clients should not depend on interfaces they don't use.

**Pattern:** Split fat protocols into focused ones. Prefer `Readable`, `Writable` over `ReadWritable`.

### 3.5 Dependency Inversion Principle (DIP)

**Rule:** High-level modules depend on abstractions, not low-level details.

**Pattern:** Inject dependencies via constructor. Never instantiate database clients or API wrappers inside business logic.

```python
# GOOD: Depends on abstraction
class OrderService:
    def __init__(self, payment_gateway: PaymentInterface) -> None:
        self._payment_gateway = payment_gateway

# BAD: Depends on concrete implementation — NEVER DO THIS
class OrderService:
    def __init__(self) -> None:
        self._payment_gateway = StripePaymentGateway()
```

---

## 4. Clean Code: Non-Negotiable Rules

### 4.1 Type Hints

- **MUST** use type hints for all function signatures, class attributes, and variables.
- **NEVER** use `Any`. Use `object` if truly generic, or define a `Protocol`.
- Use `T | None` (Python 3.10+) or `Optional[T]` for nullable types.
- Prefer `Sequence[T]`, `Mapping[K, V]`, and `Iterable[T]` over `list[T]` and `dict[K, V]` for function *parameters* (Postel's Law: be liberal in what you accept, conservative in what you return).
- Run `mypy` or `pyright` and **resolve all errors before committing**.

### 4.2 Docstrings & Comments

- **MUST** include docstrings for all public functions, classes, and methods.
- **Format:** Google-style docstrings (see example below).
- **Content:** Intent + one usage example for complex functions.
- **Comments explain WHY, not WHAT.** If you need to explain what, rename the variable instead.
- **Agents must preserve** human-written comments during refactor. They carry intent and provenance.

```python
def calculate_discounted_price(
    base_price_cents: int,
    discount_percentage: Decimal,
    minimum_purchase_cents: int = 0,
) -> int:
    """Calculate final price after applying discount with minimum purchase check.

    Args:
        base_price_cents: Original price in smallest currency unit.
        discount_percentage: Discount rate (e.g., 0.15 for 15%).
        minimum_purchase_cents: Minimum spend required for discount eligibility.

    Returns:
        Final price in cents after discount.

    Raises:
        ValueError: If discount_percentage is negative or exceeds 1.0.

    Example:
        >>> calculate_discounted_price(1000, Decimal("0.20"), 500)
        800
    """
```

### 4.3 Architecture Diagrams in Module Headers

Every module's docstring **must** include a text-based architecture diagram showing its place in the system. This goes at the top of every `.py` file and inside every `__init__.py`.

```python
"""
Customer Payment Processing Module
===================================

Handles payment validation, processing, and receipt generation.

Architecture:
-------------
    ┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
    │  API Router     │────▶│  PaymentService  │────▶│  PaymentGateway │
    │  (interfaces/)  │     │  (services/)     │     │  (adapters/)    │
    └─────────────────┘     └──────────────────┘     └─────────────────┘
                                     │
                                     ▼
                            ┌──────────────────┐
                            │  PaymentRecord   │
                            │  (repositories/) │
                            └──────────────────┘

Dependencies:
    - domain/payment_models.py  — Pydantic models
    - core/exceptions.py        — Custom exceptions
    - adapters/stripe_gateway.py — Concrete payment gateway

Author: Engineering Team
Last Updated: 2026-04-28
"""
```

### 4.4 Exception Handling

- Centralize exceptions in `core/exceptions.py` or `domain/exceptions.py`.
- **Never** use bare `except:` clauses. Catch specific exceptions.
- **Never** silently swallow exceptions without logging.
- Exception messages **must** include the offending value and expected shape.

```python
# core/exceptions.py

class DomainError(Exception):
    """Base exception for all domain-level errors."""


class InsufficientFundsError(DomainError):
    """Raised when account balance is too low for transaction."""

    def __init__(self, available_cents: int, required_cents: int) -> None:
        self.available_cents = available_cents
        self.required_cents = required_cents
        super().__init__(
            f"Insufficient funds: {available_cents}c available, "
            f"{required_cents}c required."
        )
```

### 4.5 Configuration Management

- Centralize configuration in `core/config.py` using `pydantic-settings`.
- **Never** scatter `os.environ.get()` calls throughout the codebase.
- Use environment-specific files: `.env`, `.env.test`, `.env.production`.

```python
# core/settings.py
from pydantic import Field
from pydantic_settings import BaseSettings


class ApplicationSettings(BaseSettings):
    """Centralized application configuration.

    All values are sourced from environment variables or .env files.
    Never instantiate third-party clients outside of adapters/.
    """

    database_url: str = Field(..., description="PostgreSQL connection string")
    redis_url: str = Field(default="redis://localhost:6379", description="Redis URL")
    stripe_api_key: str = Field(..., description="Stripe secret key")
    log_level: str = Field(default="INFO", description="Logging level")

    class Config:
        env_file = ".env"
        case_sensitive = False
```

### 4.6 Pydantic Models

- Separate models by operation type: `Create`, `Update`, `Read`.
- **Never** mix Pydantic models inside business logic files. Keep them in `domain/models/` or `domain/schemas/`.
- Use validators for complex business rules, not ad-hoc validation in services.

```python
# domain/models/customer.py
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class CustomerCreate(BaseModel):
    """Schema for creating a new customer record."""
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=100)


class CustomerUpdate(BaseModel):
    """Schema for partial customer updates. All fields optional."""
    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, min_length=1, max_length=100)


class CustomerRead(BaseModel):
    """Schema returned from the API. Includes system-generated fields."""
    id: str
    email: EmailStr
    full_name: str
    created_at: datetime
    updated_at: datetime
```

### 4.7 Async/Await Hygiene

This is **non-negotiable** for FastAPI services. Mixing sync and async code incorrectly blocks the event loop and destroys throughput.

**Rules:**

- **Never** write CPU-bound or blocking I/O inside an `async def` without wrapping it in a thread pool executor (`asyncio.run_in_executor`).
- **Never** use `requests`, `time.sleep()`, or synchronous DB drivers inside an `async def`.
- Use `httpx` (async mode) or `aiohttp` for outbound HTTP calls in async contexts. **Never use `requests`** inside async code.
- If a function is purely CPU-bound (e.g., complex math, image processing), define it as a standard `def` — FastAPI will automatically run it in a thread pool.
- Use `asyncio.gather()` for concurrent awaitable calls; never `await` sequentially in a loop when operations are independent.

```python
# GOOD: CPU-bound → plain def (FastAPI threads it automatically)
def compress_image(image_bytes: bytes) -> bytes:
    """Compress image data. FastAPI runs this in threadpool."""
    ...

# GOOD: I/O-bound → async def with async client
async def fetch_exchange_rate(currency_pair: str) -> Decimal:
    """Fetch live rate using async HTTP client."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{RATES_BASE_URL}/{currency_pair}")
        response.raise_for_status()
        return Decimal(response.json()["rate"])

# BAD: Blocking call inside async — starves the event loop
async def fetch_exchange_rate(currency_pair: str) -> Decimal:
    response = requests.get(...)  # NEVER — blocks the entire event loop
    ...
```

### 4.8 Structured Logging & Observability

Agents default to `print()`. This is forbidden in enterprise code.

**Rules:**

- **Never** use `print()`. Use the module-level `logger` obtained via `logging.getLogger(__name__)`.
- Log contextual data as structured `extra` kwargs — **never** via string concatenation.
- **PII/Security:** Never log passwords, tokens, full credit card numbers, session keys, or raw user-identifiable fields. Mask or omit them.
- Log at the correct level: `DEBUG` for internals, `INFO` for state transitions, `WARNING` for recoverable anomalies, `ERROR` for failures, `CRITICAL` for system-threatening conditions.
- Include correlation IDs (request ID, trace ID) in every log record when available.

```python
import logging

logger = logging.getLogger(__name__)

# GOOD: Structured, context-rich, no PII
logger.info(
    "Order payment processed",
    extra={
        "order_id": order.id,
        "user_id": user.id,
        "amount_cents": order.total_cents,
        "gateway": "stripe",
    },
)

# GOOD: Masking sensitive fields
logger.warning(
    "Payment card declined",
    extra={
        "order_id": order.id,
        "card_last_four": card_number[-4:],  # masked
        "reason": decline_reason,
    },
)

# BAD: String concatenation, no structure, possible PII
print(f"Processed order for {user.email} card {card_number}")  # NEVER
```

---

## 5. Separation of Concerns: Layered Architecture

Enforce a strict three-layer flow for all features:

```
Interfaces (API/CLI)  →  Services (Business Logic)  →  Adapters (Persistence/External)
```

| Layer | Responsibility | Forbidden |
|---|---|---|
| **Interfaces** | HTTP routing, request/response serialization, auth checks | Business logic, database queries |
| **Services** | Business rules, orchestration, calculations | Direct DB imports, HTTP specifics |
| **Adapters** | Database queries, API calls, file I/O | Business rule validation |

### 5.1 Dependency Injection Pattern

Use constructor injection at the composition root (router or `main` function).

```python
# interfaces/api/routers.py — Composition Root
from fastapi import APIRouter, Depends
from services.order_service import OrderService
from adapters.stripe_gateway import StripePaymentGateway
from adapters.sql_order_repository import SqlOrderRepository

router = APIRouter()


def get_order_service() -> OrderService:
    """Factory for OrderService with injected dependencies."""
    return OrderService(
        payment_gateway=StripePaymentGateway(),
        order_repository=SqlOrderRepository(),
    )


@router.post("/orders")
async def create_order(
    order_data: OrderCreate,
    service: OrderService = Depends(get_order_service),
) -> OrderRead:
    return await service.process_order(order_data)
```

### 5.2 Resilience in Adapters

Enterprise code **must** assume external services will fail. All adapter code follows these rules:

- **All external network calls must have explicit timeouts.** Never rely on library defaults (often infinite).
- Use retry logic (the `tenacity` library) for transient failures: HTTP 429, 502, 503, 504. Do **not** retry on 4xx client errors.
- Wrap third-party exceptions in your domain exceptions at the adapter boundary. The service layer must never see `httpx.HTTPStatusError` or `sqlalchemy.exc.OperationalError` — it should see `PaymentGatewayError` or `RepositoryUnavailableError`.
- Apply the circuit-breaker pattern for high-traffic adapters in production.

```python
# adapters/stripe_gateway.py
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from core.exceptions import PaymentGatewayError


class StripePaymentGateway:
    """Adapter for Stripe payment processing.

    Wraps all Stripe exceptions into domain-level PaymentGatewayError.
    Retries on transient network failures with exponential backoff.
    """

    _TIMEOUT_SECONDS = 10.0
    _RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

    @retry(
        retry=retry_if_exception_type(PaymentGatewayError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
    )
    async def charge(self, amount_cents: int, token: str) -> str:
        """Charge a payment token. Returns charge ID.

        Raises:
            PaymentGatewayError: On any Stripe or network failure.
        """
        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT_SECONDS) as client:
                response = await client.post(
                    "https://api.stripe.com/v1/charges",
                    data={"amount": amount_cents, "source": token},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                if response.status_code in self._RETRYABLE_STATUS_CODES:
                    raise PaymentGatewayError(f"Stripe transient error: {response.status_code}")
                response.raise_for_status()
                return response.json()["id"]
        except httpx.TimeoutException as exc:
            raise PaymentGatewayError("Stripe request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise PaymentGatewayError(f"Stripe rejected request: {exc.response.status_code}") from exc
```

---

## 6. Testing Standards

- **Every new function gets a test.** Bug fixes get a regression test.
- Mock external I/O (API, DB, filesystem) with **named fake classes**, not inline stubs.
- Tests must be **F.I.R.S.T:** Fast, Independent, Repeatable, Self-validating, Timely.
- Mirror source structure in `tests/`. If source is `services/order_service.py`, test is `tests/services/test_order_service.py`.
- Use `pytest` fixtures for shared setup. Avoid `setUp`/`tearDown` patterns from `unittest`.
- Parametrize tests over input variants with `@pytest.mark.parametrize` rather than copy-pasting test functions.
- Aim for **branch coverage**, not just line coverage. An untested `else` branch is a hidden bug.

```python
# tests/services/test_order_service.py
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock
from services.order_service import OrderService
from domain.models.order import OrderCreate
from core.exceptions import InsufficientFundsError


class FakePaymentGateway:
    """Controlled fake for PaymentInterface. Does not hit network."""

    def __init__(self, charge_id: str = "ch_test_123") -> None:
        self.charge_id = charge_id
        self.charged_amounts: list[int] = []

    async def charge(self, amount_cents: int, token: str) -> str:
        self.charged_amounts.append(amount_cents)
        return self.charge_id


@pytest.fixture
def fake_gateway() -> FakePaymentGateway:
    return FakePaymentGateway()


@pytest.mark.asyncio
async def test_process_order_charges_correct_amount(fake_gateway: FakePaymentGateway) -> None:
    service = OrderService(payment_gateway=fake_gateway)
    order = OrderCreate(amount_cents=5000, token="tok_visa")

    await service.process_order(order)

    assert fake_gateway.charged_amounts == [5000]
```

---

## 7. Agent-Specific Instructions

### 7.1 Before Writing Code

1. Read the module's `__init__.py` to understand the public API and architecture diagram.
2. Check for existing patterns in adjacent files. **Do not invent new conventions.**
3. Ask: *"Does this belong in an existing module or a new one?"* Use the Section 1 framework.
4. Check `pyproject.toml` or `requirements.txt` before reaching for any library.

### 7.2 During Implementation

- **Preserve existing comments** during refactor. They carry intent and institutional knowledge.
- **Early returns over nested ifs.** Maximum 2 levels of indentation in any function body.
- **No code duplication.** Extract shared logic into a function or module immediately.
- **Use the project's formatter** (`black`, `ruff format`). Do not discuss or debate style.
- **Prefer `pathlib.Path` over `os.path`** for all filesystem operations.
- **Never use mutable default arguments** (`def f(items: list = [])` is a silent bug).
- When writing a loop over a large collection that feeds into I/O, use `yield` (generators) rather than building the full list in memory.

### 7.3 After Implementation

```bash
pytest -x --tb=short          # All tests must pass
mypy src/                     # Zero type errors
ruff check src/               # Zero lint violations
ruff format --check src/      # Formatting verified
```

Update the architecture diagram in the module header if you changed any module relationships.

### 7.4 Dependency Management

- **Do not introduce new third-party packages** (`pip install ...`) without explicit user approval.
- **Always check `pyproject.toml` or `requirements.txt` first** to leverage existing libraries.
- Prefer the **Python standard library** for basic tasks before reaching for third-party packages:

| Task | Prefer | Over |
|---|---|---|
| File paths | `pathlib` | `os.path` |
| Date/time | `datetime` | `arrow`, `pendulum` (unless already in deps) |
| HTTP (sync) | `urllib.request` for simple cases | adding `requests` if not already present |
| Data classes | `dataclasses` or Pydantic | raw `dict` |
| Retries | `tenacity` (if already in deps) | hand-rolled loops |
| Iteration | `itertools` | custom generators for standard patterns |

### 7.5 What Agents Must Never Do

| Forbidden | Why |
|---|---|
| `print()` anywhere in src/ | No observability, no log levels, no structure |
| `except Exception: pass` | Silently swallows failures, hides bugs |
| `import *` | Pollutes namespace, breaks static analysis |
| Hardcoded credentials or URLs | Security incident and environment coupling |
| `Any` without a comment justifying it | Defeats the entire type system |
| Instantiating concrete services inside business logic | Violates DIP, destroys testability |
| Modifying a file to fix one bug, then touching 10 unrelated things | Scope creep — one PR, one concern |
| Removing or overwriting existing comments | They carry intent that isn't in the code |

---

## 8. Progressive Disclosure for Large Projects

For monorepos or large codebases, use hierarchical instruction files:

```
project-root/
├── CLAUDE.md                  ← Global rules (this file)
├── src/
│   ├── core/
│   │   └── CLAUDE.md          ← Core infrastructure rules
│   ├── payments/
│   │   └── CLAUDE.md          ← Payment domain specifics
│   └── notifications/
│       └── CLAUDE.md          ← Notification domain specifics
└── tests/
    └── CLAUDE.md              ← Test-specific conventions
```

Claude Code reads `CLAUDE.md` from the working directory upward. Subdirectory files only activate when working in that area. Keep root files general; put domain-specific rules in subdirectories.

---

## 9. Quick Reference Checklist

Before committing, verify every item:

**Code Quality**
- [ ] All functions have type hints using `Sequence`/`Mapping`/`Iterable` for parameters
- [ ] All public functions, classes, and methods have Google-style docstrings
- [ ] No file exceeds 500 lines
- [ ] No function exceeds 20 lines
- [ ] No bare `except:` clauses — catch specific exceptions only
- [ ] No `Any` types without an inline comment justifying the exception
- [ ] No `print()` statements anywhere in `src/` — use `logger`

**Architecture**
- [ ] Architecture diagram updated in module header if relationships changed
- [ ] New module follows naming conventions (Section 2)
- [ ] Dependencies injected via constructor, not instantiated inside logic
- [ ] Pydantic models live in `domain/`, not mixed with service logic
- [ ] Exceptions defined in `core/exceptions.py` or `domain/exceptions.py`
- [ ] Configuration centralized in `core/config.py`

**Async & I/O**
- [ ] No blocking synchronous calls (`requests`, `time.sleep`, sync DB) inside `async def`
- [ ] All external HTTP calls use `httpx` (async) or `aiohttp`
- [ ] All external network calls have explicit timeout values
- [ ] Retry logic applied to transient adapter failures

**Data & Memory**
- [ ] No unbounded in-memory collections — generators or pagination used for >1,000 records
- [ ] No PII, tokens, or secrets passed to logger

**Tests & Tooling**
- [ ] Tests mirror source structure in `tests/`
- [ ] New functions have tests; bug fixes have regression tests
- [ ] `pytest -x --tb=short` passes cleanly
- [ ] `mypy src/` reports zero errors
- [ ] `ruff check src/` reports zero violations

**Dependencies**
- [ ] No new third-party packages added without explicit approval
- [ ] Standard library used where equivalent third-party isn't already present

---

## 10. File Template for New Modules

Use this template verbatim for every new `.py` file. Fill in every section — do not leave placeholder text.

```python
"""
[Module Name] — [One-line description of single responsibility]

Architecture:
-------------
    [ASCII diagram showing this module's place in the system]
    [At minimum: what calls this module, what this module calls]

    Example:
    ┌────────────────┐     ┌──────────────────┐     ┌─────────────────┐
    │  OrderRouter   │────▶│  [This Module]   │────▶│  OrderRepository│
    │  (interfaces/) │     │  (services/)     │     │  (adapters/)    │
    └────────────────┘     └──────────────────┘     └─────────────────┘

Dependencies:
    - [path/to/module.py] — [why it is needed]
    - [path/to/module.py] — [why it is needed]

Author: [Team or individual name]
Last Updated: [YYYY-MM-DD]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Import types only needed for static analysis to avoid circular imports at runtime
    from domain.models.example import ExampleModel

logger = logging.getLogger(__name__)


class ExampleService:
    """[Single-sentence class responsibility statement].

    Does X given Y. Does not do Z (which belongs in [OtherModule]).

    Example:
        >>> service = ExampleService(dependency=FakeDependency())
        >>> result = service.do_something("input")
        >>> assert result == "expected"
    """

    def __init__(self, dependency: SomeInterface) -> None:
        """Initialize with injected dependencies.

        Args:
            dependency: [What this dependency provides and why it is injected].
        """
        self._dependency = dependency

    def do_something(self, input_value: str) -> str:
        """[One-line description of what this method does].

        Args:
            input_value: [Description of expected format/constraints].

        Returns:
            [Description of return value and its format].

        Raises:
            SpecificDomainError: [When and why this is raised].
        """
        # WHY: [Explain non-obvious decisions, not what the code does]
        if not input_value:
            raise ValueError(f"input_value must be non-empty, got: {input_value!r}")

        return self._dependency.transform(input_value)
```

---

*This file is the contract between the human engineering team and every AI agent working in this codebase. Agents that deviate from these rules introduce technical debt that costs human time to fix. When in doubt: surface the ambiguity, do not resolve it with guesswork.*
