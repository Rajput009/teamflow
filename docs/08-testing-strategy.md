# Testing Strategy

> "It works on my computer" is not evidence. Tests are.

## Test Pyramid

```text
        ╱  E2E (few)   ╲      full request → DB through httpx AsyncClient
       ╱  Service (many) ╲    business rules directly against services
      ╱  Unit (plenty)    ╲   validators, security helpers, pure logic
```

- **Unit:** JWT encode/decode, password hashing, permission predicate functions, Pydantic schemas. No DB. Milliseconds.
- **Service:** `task_service.assign()` with a real test database — verifies rules like "cannot assign to non-member". No HTTP layer.
- **Integration (E2E):** full requests via `httpx.AsyncClient(transport=ASGITransport(app))` — status codes, envelopes, tenancy isolation, auth flows.

## Stack & Setup

| Tool | Role |
|---|---|
| pytest + pytest-asyncio | Runner, async support |
| httpx | Async test client against the ASGI app |
| PostgreSQL (dedicated `teamflow_test` DB) | Real DB — no SQLite substitutes (we use Postgres enums/UUIDs) |
| factories.py | Simple factory functions to create users/orgs/projects fast |

Test DB lifecycle: session-scoped fixture runs `alembic upgrade head` once → each
test runs in a transaction that is rolled back afterward (fast + isolated).

## Fixtures (`conftest.py`)

```python
db_session          # rolled-back transaction per test
client              # httpx client wired to the app
user_factory        # make_user(email=...) -> User
org_factory         # make_org(owner=user) -> (Organization, Membership)
project_factory     # make_project(org=..., member=...)
auth_headers(user)  # {"Authorization": "Bearer <token for user>"}
```

## Coverage Targets

| Area | Target |
|---|---|
| Services (business logic) | ≥ 90% |
| Security helpers / permissions | 100% |
| Repositories | covered via service/integration tests |
| Overall | ≥ 80% |

Coverage is a signal, not a goal — the security tests below are mandatory regardless of percentage.

## The Mandatory Test List

### Authentication
Register/login happy paths; duplicate email; wrong password vs unknown email produce identical responses; expired/tampered JWT rejected; refresh rotation revokes old token; refresh reuse nukes all user tokens; deactivated user blocked.

### Multi-tenancy (the portfolio-critical ones)
User of Org B gets **404** on Org A's project by id; Org B's list endpoints never contain Org A rows; cross-org assignment attempt fails; removed member's token stops working.

### RBAC
One test cell per row of the permission matrix in `05-rbac-multi-tenancy.md`:
member can't delete org; manager can create project; member can update own assigned task but not others'; last-owner protection; admin can't touch an owner.

### Business rules
Task status transitions recorded in activities; project name unique per org; pagination math (`total`, `pages`) correct on edge sizes; filters combine correctly.

## Naming & Style

```python
async def test_member_cannot_delete_organization(client, org_factory, user_factory): ...
```

Pattern: `test_<actor>_<action>_<expected_outcome>`. One behavior per test.
Arrange–Act–Assert sections kept visually separate.

## CI (V4)

GitHub Actions on every push/PR:

```text
1. ruff check + format check
2. spin up postgres service container
3. alembic upgrade head
4. pytest --cov --cov-fail-under=80
```

A PR that doesn't pass CI doesn't merge — even solo, especially solo.
