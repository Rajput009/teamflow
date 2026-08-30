# Feature Spec Template

Every feature is documented **with this template before any code is written**.
If a question can't be answered, the feature isn't understood yet — coding is postponed,
not rushed.

Copy this file to `NN-feature-name.md` and fill every section.

---

## 1. What problem am I solving?

One paragraph. Who needs this and why. If nobody has this problem, cut the feature.

## 2. What data do I need?

Inputs (request body, path/query params), entities involved, which fields are
required/optional, validation rules with exact limits.

## 3. What API endpoint do I need?

Method + path, request example, success response with status code, response schema.

## 4. What should the database do?

Tables touched, inserts/updates/deletes, constraints relied upon, indexes used,
transaction boundaries (what must commit atomically).

## 5. What can go wrong?

Exhaustive failure list, each mapped to an HTTP status + error code:
validation failures, conflicts, missing records, permission violations,
infrastructure failures.

## 6. Who is allowed to perform this operation?

Public / authenticated / role required / ownership restrictions. Reference the
permission matrix in `../05-rbac-multi-tenancy.md`.

## 7. How do I test it?

Test list derived from sections 3–6: happy paths first, then one test per failure
mode from section 5, then security cases from section 6.
