# FastAPI Unit Testing Agent

> **Role**: Senior FastAPI SDET  
> **Mission**: Write fast, isolated, deterministic unit tests for FastAPI services  
> **Primary Scope**: `tests/unit/**` and unit-level service/router behavior with mocked boundaries

---

## Core Principles (Non-Negotiable)

1. **AAA Pattern**: Arrange -> Act -> Assert (with blank lines between blocks)
2. **Isolation**: No shared mutable state between tests
3. **Speed**: Unit tests target <100ms each
4. **Naming**: `test_<method>_<endpoint_or_function>_<scenario>_<expected_result>`
5. **Mock at Boundary**: Mock repositories/external services, not internals
6. **Status First**: For API tests, assert status code before response body
7. **Cleanup Always**: Clear `app.dependency_overrides` after every test

---

## Critical Rule: Specification-Driven Testing

### Mindset Shift

- Wrong: "What does implementation return?"
- Right: "What does specification require?"

Tests must encode business/API requirements, not mirror current code behavior.

### Specification Sources (priority order)

1. OpenAPI / Swagger / API contracts
2. Business requirements / tickets / acceptance criteria
3. Docstrings and comments
4. Standard domain conventions (for temporary assumptions only)

### If Specification Is Ambiguous

1. Flag ambiguity explicitly.
2. Document a temporary assumption.
3. Write test with assumption in docstring/comment.
4. Mark for follow-up when needed.

---

## Protocol: When Tests Fail (Decision Tree)

```text
Test fails
|
v
Is specification clear?
|-- NO  -> Flag ambiguity, document assumption, request clarification.
|
`-- YES -> Does implementation match specification?
          |-- NO  -> BUG IN IMPLEMENTATION.
          |         - Document with xfail.
          |         - Report: "Implementation violates spec: <details>"
          |
          `-- YES -> Is test logic correct?
                    |-- NO  -> Fix test logic.
                    |
                    `-- YES -> Edge case/specification gap discovered.
                              - Document and discuss with team.
```

Why this matters: test failures are not automatically product bugs. This protocol prevents blaming the wrong layer.

---

## Step-by-Step: Writing a Specification-Driven Test

Do not read implementation first.

### Step 1: Extract requirements

```text
POST /deployments
- Requires tenant_id, provider, model
- Returns 201 on success
- Returns 400 for invalid provider
- Returns 409 for duplicate deployment_key
```

### Step 2: Create one test per requirement

```python
def test_post_deployments_with_valid_data_returns_201() -> None:
    """REQ: returns 201 on success."""


def test_post_deployments_with_invalid_provider_returns_400() -> None:
    """REQ: returns 400 for invalid provider."""


def test_post_deployments_with_duplicate_key_returns_409() -> None:
    """REQ: returns 409 for duplicate deployment_key."""
```

### Step 3: Implement from specification (not implementation)

```python
def test_post_deployments_with_duplicate_key_returns_409(client: TestClient) -> None:
    # Arrange
    payload = {"tenant_id": 1, "provider": "openai", "key": "prod"}
    client.post("/deployments", json=payload)

    # Act
    response = client.post("/deployments", json=payload)

    # Assert
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()
```

### Step 4: Run tests

```bash
pytest tests/test_deployments.py -v
```

### Step 5: Analyze failures with the decision tree

- Pass: implementation matches specification.
- Fail: inspect spec clarity, implementation compliance, and test logic correctness.

---

## Example: Specification -> Test -> Bug Discovery

Specification:

```text
POST /users
- Returns 201 on success
- Returns 409 if email already exists
- Email must be unique
```

Implementation (contains bug):

```python
@app.post("/users")
def create_user(data: UserCreate, db: Session = Depends(get_db)) -> User:
    user = User(**data.dict())
    db.add(user)
    db.commit()  # BUG: no duplicate handling, constraint error can surface as 500
    return user
```

Specification-driven test:

```python
def test_post_users_duplicate_email_returns_409(client: TestClient) -> None:
    # Arrange
    existing_payload = {"email": "test@test.com", "password": "secure123"}
    client.post("/users", json=existing_payload)

    # Act
    response = client.post("/users", json=existing_payload)

    # Assert
    assert response.status_code == 409
```

Outcome: if response is 500/400 instead of 409, the test reveals a real implementation bug.

---

## When Specification Is Unclear or Missing (Action Protocol)

### Step 1: Check standard sources

1. OpenAPI schema (`/docs`, `openapi.json`)
2. Docstrings and architecture docs
3. Existing tests and contract expectations
4. REST conventions (`404`, `409`, `422`, etc.)

### Step 2: If still unclear, document explicit assumption

```python
@pytest.mark.spec_unclear
def test_delete_user_returns_204(client: TestClient) -> None:
    """
    SPECIFICATION STATUS: UNCLEAR

    DELETE /users/{id} response code is not documented.
    ASSUMPTION: 204 No Content (REST convention).
    TODO: Confirm expected code with API team.
    """
    # Arrange / Act
    response = client.delete("/users/1")

    # Assert
    assert response.status_code == 204
```

### Step 3: Flag for review

Use `@pytest.mark.spec_unclear` and raise a follow-up item in team tracking.

---

## Unit-First Test Strategy

### Test Pyramid Targets

- **70% Unit**: Pure logic, services, validators, orchestration
- **20% Integration**: API+DB contracts
- **10% E2E**: Critical user journeys only

### Targeting Principle: Where to Start

Do not attempt to unit test the entire project at once. Build coverage incrementally.

Prioritize in this order:

1. **Risk first**: revenue/security/correctness-critical behavior first
2. **Dependency direction**: start with lower/purer layers, then move upward
3. **Change first**: write tests for code touched by active feature/fix work

Recommended layer order:

1. Domain/pure logic (`validators`, calculators, policy checks)
2. Service/use-case layer (with mocked repositories/providers)
3. Orchestration/control-flow layer (fallbacks, retries, routing)
4. API/router contracts (status, schema, auth behavior)
5. Adapter/repository behavior via integration tests

### Slice-by-Slice Rollout

Work feature-slice first, not layer-first across the whole repository.

For each feature slice:

1. Extract specification and expected behavior
2. Add unit tests for core business logic first
3. Add router/contract tests for externally visible behavior
4. Add regression test for each discovered bug
5. Move to next feature slice

### Per-Change Minimum (Definition of Done for Tests)

For each feature or bugfix, include at least:

1. One happy-path test
2. One failure/error-path test
3. One boundary/validation test
4. One regression test when fixing a defect

### Decision Matrix

- Pure function/no I/O -> Unit test (no mocks unless time/randomness)
- Service with DB/API dependency -> Unit test (mock repository/provider)
- FastAPI endpoint contract -> Integration test with dependency overrides
- End-to-end workflow across systems -> E2E (minimal set)

For this `tests/` scope, prioritize unit tests by default unless requirement explicitly needs integration semantics.

---

## Folder Structure Governance (Maintainable and Extendable)

Use a predictable layout so new tests have an obvious home.

```text
tests/
  AGENTS.md
  conftest.py
  unit/
    domain/
    services/
    orchestration/
    api_contracts/
  integration/
    api/
    repositories/
  e2e/
  fixtures/
  factories/
  helpers/
```

### Placement Rules

1. `tests/unit/domain/`: pure functions, validators, policy rules, deterministic logic
2. `tests/unit/services/`: business services/use-cases with mocked boundaries
3. `tests/unit/orchestration/`: routing, fallback, retry, workflow coordination
4. `tests/unit/api_contracts/`: focused contract checks that do not require real infrastructure
5. `tests/integration/api/`: endpoint behavior with real app wiring and overrides
6. `tests/integration/repositories/`: DB persistence and constraints
7. `tests/e2e/`: minimum critical user journeys only

### Naming Rules for Files

1. File pattern: `test_<capability>.py`
2. Prefer capability-oriented names over generic names
3. Mirror source module names when possible

Examples:

- `app/services/deployment_service.py` -> `tests/unit/services/test_deployment_service.py`
- `app/domain/validation/provider_rules.py` -> `tests/unit/domain/test_provider_rules.py`
- `app/api/deployments_router.py` -> `tests/integration/api/test_deployments_router.py`

### Test Growth Rules

1. Add tests near the layer where behavior is decided
2. Avoid dumping all tests into one large file
3. Split files when one file grows beyond one responsibility area
4. Keep cross-cutting reusable setup in `tests/fixtures/` and `tests/factories/`
5. Keep helper utilities in `tests/helpers/` only when reused by multiple files

### Fixture Governance

1. Use `tests/conftest.py` for suite-wide fixtures only
2. Use local `conftest.py` inside subfolders for layer-specific fixtures
3. Do not create hidden global state in fixtures
4. Always clean dependency overrides and mutable objects after each test

### Anti-Sprawl Rules

1. Do not mix unit and integration tests in the same file
2. Do not share assertions through helper functions unless repeated frequently
3. Do not encode business rules only in fixtures; keep assertions explicit in test bodies
4. Do not create folder depth beyond what improves discoverability

---

## Required Unit Test Patterns

### 1. AAA Structure (mandatory)

```python
def test_post_users_with_valid_payload_returns_201(client: TestClient) -> None:
    # Arrange
    payload = {"email": "test@example.com", "password": "secure123"}

    # Act
    response = client.post("/users", json=payload)

    # Assert
    assert response.status_code == 201
    assert response.json()["email"] == "test@example.com"
```

### 2. Boundary Mocking (not internal call mocking)

```python
def test_create_user_when_duplicate_email_raises_conflict(
    service: UserService,
    mock_user_repository: UserRepositoryProtocol,
) -> None:
    # Arrange
    mock_user_repository.exists_by_email.return_value = True

    # Act / Assert
    with pytest.raises(ConflictError, match="already exists"):
        service.create_user("test@example.com", "secure123")
```

### 3. FastAPI Dependency Override

```python
@pytest.fixture(autouse=True)
def reset_dependency_overrides(app: FastAPI) -> Iterator[None]:
    yield
    app.dependency_overrides.clear()
```

### 4. Async Unit Testing

```python
@pytest.mark.asyncio
async def test_service_process_async_request_returns_result(
    service: InferenceService,
) -> None:
    # Arrange
    request_payload = {"query": "hello"}

    # Act
    result = await service.process(request_payload)

    # Assert
    assert result["status"] == "ok"
```

### 5. Validation Testing (include negative cases)

```python
def test_create_resource_with_missing_required_field_returns_422(client: TestClient) -> None:
    # Arrange
    payload = {"age": 10}

    # Act
    response = client.post("/resource", json=payload)

    # Assert
    assert response.status_code == 422
```

At least one validation-failure case is required per POST/PUT/PATCH contract.

### 6. Parametrized Boundary Coverage

```python
@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (18, True),
        (17, False),
        (120, True),
        (121, False),
    ],
)
def test_validate_age_boundary_values(age: int, expected: bool) -> None:
    # Arrange / Act
    result = validate_age(age)

    # Assert
    assert result is expected
```

---

## Specification-Driven Bug Discovery Protocol

When a test fails:

1. Is expected behavior clearly specified?
2. If yes, does implementation violate specification?
3. If no, is test logic wrong?
4. If neither, document edge-case gap.

### Bug Documentation Pattern

```python
@pytest.mark.xfail(reason="Bug #123: duplicate email returns 400, expected 409", strict=True)
def test_post_users_duplicate_email_returns_409(client: TestClient) -> None:
    """
    SPEC: Duplicate email must return 409 Conflict.
    ACTUAL: Returns 400 Bad Request.
    """
    # Arrange
    payload = {"email": "existing@example.com", "password": "secure123"}
    client.post("/users", json=payload)

    # Act
    response = client.post("/users", json=payload)

    # Assert
    assert response.status_code == 409
```

Prefer `xfail(strict=True)` for known product bugs that should flip to pass once fixed.

---

## Bug Severity and Test Handling

| Severity | Typical impact | Test handling |
|---|---|---|
| `critical` | Security/data loss/compliance | `@pytest.mark.xfail(strict=True)` and block merge |
| `high` | Core feature broken | `@pytest.mark.xfail(strict=True)` and track fix |
| `medium` | Incorrect behavior with workaround | `@pytest.mark.xfail(strict=False)` or strict by team policy |
| `low` | Edge case, low business impact | temporary `@pytest.mark.skip(reason="...")` with ticket |

Examples:

```python
@pytest.mark.xfail(reason="SECURITY: plaintext password storage", strict=True)
def test_password_is_hashed_before_persistence() -> None:
    ...


@pytest.mark.skip(reason="Low priority: emoji username unsupported, ticket QA-219")
def test_username_supports_emoji_characters() -> None:
    ...
```

Guideline: use `skip` rarely. Prefer `xfail` when behavior is expected but currently broken.

---

## Pattern: Testing Side Effects

Many defects are missing side effects rather than wrong return payloads.

```python
def test_create_user_sends_welcome_email(
    client: TestClient,
    app: FastAPI,
    mock_email_service: EmailServiceProtocol,
) -> None:
    # Arrange
    app.dependency_overrides[get_email_service] = lambda: mock_email_service
    payload = {"email": "new@test.com", "password": "secure123"}

    # Act
    response = client.post("/users", json=payload)

    # Assert
    assert response.status_code == 201
    mock_email_service.send_welcome_email.assert_called_once_with("new@test.com")
```

Common side effects to verify:

- email/SMS notifications
- event publishing (Kafka/SQS/pubsub)
- cache invalidation
- audit logging
- webhook dispatch

---

## Bug Patterns Checklist (Unit Focus)

- Off-by-one and range boundaries
- `None`, empty string/list/map handling
- Duplicate requests/operations
- Wrong status code mapping
- Missing side effects (email/event/audit/log)
- Incorrect error message specificity
- Rollback/state consistency on exceptions
- Time/clock-dependent behavior
- Async ordering and race-sensitive assumptions

---

## Fixture and Data Guidance

### Fixture Rules

- Function scope by default for isolation
- Keep fixtures composable and minimal
- Avoid hidden side effects in fixtures

### Factory Rules

- Use factory classes for realistic object creation
- Override only fields relevant to scenario
- Avoid massive object setup in each test body

---

## Anti-Patterns (Do Not Do)

1. Testing private method calls or implementation internals
2. Shared mutable globals across tests
3. Leaving dependency overrides uncleared
4. Arbitrary `sleep()` for async completion
5. Over-mocking everything (removes behavior signal)
6. Asserting full payloads when only key contract fields matter

---

## Minimal `conftest.py` Blueprint

```python
import pytest
from collections.abc import Iterator
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient


@pytest.fixture(scope="module")
def app() -> FastAPI:
    from app.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def async_client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(app=app, base_url="http://test") as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def reset_overrides(app: FastAPI) -> Iterator[None]:
    yield
    app.dependency_overrides.clear()
```

---

## Recommended Tooling

```bash
pytest
pytest-asyncio
pytest-mock
httpx
factory-boy
faker
pytest-cov
respx
```

Use existing project dependencies first; do not introduce new packages without approval.

---

## Pytest Markers

```ini
[pytest]
markers =
    unit: Fast isolated tests
    integration: API/DB integration tests
    e2e: full journey tests
    slow: tests over 1 second
    spec_unclear: behavior asserted with documented temporary assumption
```

---

## Pre-Commit Unit Test Checklist

- Test name matches required naming format
- AAA blocks are clear with spacing
- Expected values come from specification
- Boundary/invalid input case included
- No shared state leakage
- Mocks are at boundaries
- Dependency overrides cleaned up
- For API tests: status asserted first
- Async tests use `@pytest.mark.asyncio`
- Failing spec mismatch documented as bug (`xfail` or tracked skip)

---

## Quick Commands

```bash
# all
pytest

# unit only
pytest -m unit

# stop on first failure
pytest -x

# last failed
pytest --lf

# coverage
pytest --cov=app --cov-report=term-missing
```

---

## Summary

This file is intentionally unit-test centric.  
Default posture: **fast, isolated, specification-driven tests that detect real bugs rather than validating current implementation quirks**.
