# M0 Honest Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the half-built MFA, add password reset, and remove every invented claim, dead control, stub test and stale README statement, so that what the product shows is what the code does.

**Architecture:** MFA and password reset follow the existing auth pattern: a small service class with a pluggable store (Postgres when `DATABASE_URL` is set, in-memory otherwise), selected by a `select_*` function and held on the FastAPI `state` object. The frontend adds thin functions to `lib/auth-api.ts` and three screens. Cleanup tasks delete invented fields at the API and the UI together and pin them with a source-scan test.

**Tech Stack:** Python 3.11, FastAPI, psycopg, stdlib `unittest`; Next.js 16, React 19, TypeScript, Tailwind, Vitest. One new frontend dependency: `qrcode` (with `@types/qrcode`).

**Spec:** `docs/superpowers/specs/2026-10-11-omero-completion-design.md`, section 7, "M0 — Honest baseline".

## Global Constraints

- No new paid services.
- The no-database local path keeps working: every new store has an in-memory equivalent.
- Schema changes are a new numbered SQL file in `packages/db/drizzle`, with `packages/db/src/schema.ts` kept in step.
- Every new query path is tenant-scoped where a tenant exists.
- New behaviour is written test-first. Existing tests keep passing (baseline: 417 backend, 26 frontend).
- Work stays on branch `omero-m0-honest-baseline`. Nothing is pushed or merged to `main` without the owner's say-so.
- Secrets are never committed.
- Backend tests: `cd services/api && python -m unittest discover -s tests -p "test_*.py"`. Frontend: `cd apps/web && npx tsc --noEmit && npx vitest run`.

## Review Focus

1. A reset link opened twice, or after it expired: the second use is refused with a clear message and the password is unchanged. (Task 3)
2. "Forgot password" for an address with no account: same response as a real one, no email sent. (Task 3)
3. Wrong MFA code five times on one login: the challenge dies and the user must enter the password again. (Task 1)
4. A user who started MFA enrolment but never confirmed it: login must not ask for a code. (Task 1)
5. A valid MFA code submitted a second time inside its 30-second window: refused. (Task 1)

## File Structure

| File | Responsibility |
|---|---|
| `packages/db/drizzle/0009_auth_recovery.sql` (create) | `user_mfa` and `password_reset_tokens` tables |
| `services/api/src/omni_modal/security/mfa.py` (modify) | Add Postgres store, replay guard, store selection order |
| `services/api/src/omni_modal/security/password_reset.py` (create) | Reset-token issue and consume, with in-memory and Postgres stores |
| `services/api/src/omni_modal/security/accounts.py` (modify) | `set_password` |
| `services/api/src/omni_modal/security/sessions.py` (modify) | Per-user session index and `revoke_user` |
| `services/api/src/omni_modal/api/app.py` (modify) | MFA attempt limit, reset endpoints, removal of invented fields |
| `apps/web/src/lib/auth-api.ts` (modify) | MFA and reset client functions, error-detail parsing |
| `apps/web/src/app/sign-in/page.tsx` (modify) | Code step, forgot-password link, dev-only token sign-in |
| `apps/web/src/app/forgot-password/page.tsx`, `reset-password/page.tsx` (create) | Reset screens |
| `apps/web/src/app/settings/page.tsx` (modify) | Real Security tab: MFA enrolment, real member list |
| `apps/web/src/__tests__/no-invented-claims.test.ts` (create) | Source scan pinning the cleanup |

---

### Task 1: MFA backend — durable, bounded, tested

**Files:**
- Create: `packages/db/drizzle/0009_auth_recovery.sql`, `services/api/tests/test_mfa.py`
- Modify: `services/api/src/omni_modal/security/mfa.py`, `services/api/src/omni_modal/api/app.py` (login and `/auth/mfa/*` routes), `packages/db/src/schema.ts`, `services/api/tests/test_fastapi_app.py`

**Interfaces:**
- Produces: `POST /auth/login` returns either the existing `AuthResponse` body, or `{"mfa_required": true, "mfa_token": str}`. `POST /auth/mfa/login {mfa_token, code}` returns `AuthResponse`, or 401 with `detail = {"error": str, "mfa_token": str | null}` (`null` once attempts are exhausted). `POST /auth/mfa/enroll` returns `{"secret": str, "otpauth_uri": str}`. `POST /auth/mfa/confirm {code}` returns `{"enrolled": true}`. `GET /auth/mfa/status` returns `{"enrolled": bool}`. `POST /auth/mfa/disable {code}` returns `{"enrolled": false}`.

- [ ] **Step 1: Write `tests/test_mfa.py` (unit, no HTTP)**

```python
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()

def test_totp_matches_rfc6238_vector(self):
    self.assertEqual(totp(RFC_SECRET, for_time=59, digits=8), "94287082")

def test_verify_accepts_adjacent_step_and_rejects_far_step(self):
    code = totp(RFC_SECRET, for_time=1000)
    self.assertTrue(verify_totp(RFC_SECRET, code, for_time=1030))
    self.assertFalse(verify_totp(RFC_SECRET, code, for_time=1090))

def test_unconfirmed_enrolment_is_not_enrolled(self):
    svc = MfaService(InMemoryMfaStore()); svc.begin_enroll("t", "u", "a@b.c")
    self.assertFalse(svc.is_enrolled("t", "u"))

def test_same_code_cannot_be_used_twice(self):
    svc = MfaService(InMemoryMfaStore()); s = svc.begin_enroll("t", "u", "a@b.c")["secret"]
    self.assertTrue(svc.confirm("t", "u", totp(s)))
    self.assertFalse(svc.verify("t", "u", totp(s)))   # confirm consumed this step

def test_disable_requires_valid_code(self): ...  # wrong code -> False and still enrolled
```

- [ ] **Step 2: Run it.** `python -m unittest tests.test_mfa -v`. Expected: `test_same_code_cannot_be_used_twice` fails; the rest pass.

- [ ] **Step 3: Add the replay guard in `mfa.py`.** Add `matching_counter(secret_b32, code, *, for_time=None, step=30, window=1) -> int | None` returning the counter that matched. `MfaService.confirm`, `verify` and `disable` use it, refuse a counter `<= rec.get("last_counter", -1)`, and store the new `last_counter`. `verify_totp` stays as a thin wrapper.

- [ ] **Step 4: Add `PostgresMfaStore(pool)` in `mfa.py`** with the same `get/put/delete(key)` methods, parsing `omero:mfa:{tenant}:{user}` keys into the two columns. `select_mfa_service()` order becomes Postgres when `DATABASE_URL` is set, else Redis, else in-memory.

- [ ] **Step 5: Write `0009_auth_recovery.sql`** (both tables; the second is used by Task 3) and mirror them in `schema.ts`.

```sql
CREATE TABLE IF NOT EXISTS user_mfa (
  tenant_id text NOT NULL,
  user_id text NOT NULL,
  secret text NOT NULL,
  confirmed boolean NOT NULL DEFAULT false,
  last_counter bigint NOT NULL DEFAULT -1,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id)
);
CREATE TABLE IF NOT EXISTS password_reset_tokens (
  token_hash text PRIMARY KEY,
  email text NOT NULL,
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS password_reset_tokens_email_idx ON password_reset_tokens (lower(email), created_at);
```

Give `user_mfa` the same row-level-security policy shape that `0008_rls.sql` gives other tenant tables. `password_reset_tokens` gets none: it is read before any tenant is known.

- [ ] **Step 6: Add HTTP tests to `test_fastapi_app.py`**

```python
def test_login_without_mfa_keeps_auth_response_shape(self):   # token, refresh_token, roles present; no mfa_required key
def test_mfa_enrol_confirm_then_login_requires_code(self):    # login -> {"mfa_required": True, "mfa_token": ...}; no "token" key
def test_mfa_login_with_valid_code_issues_tokens(self):       # 200, token has 3 dot-separated parts
def test_mfa_token_is_single_use(self):                       # second /auth/mfa/login with the same mfa_token -> 401
def test_mfa_five_wrong_codes_kills_challenge(self):          # 5th failure: detail["mfa_token"] is None; a valid code with any earlier token -> 401
def test_mfa_endpoints_require_bearer(self):                  # enroll/confirm/status/disable without header -> 401
```

Codes for the tests come from `totp(secret, for_time=time.time() + 30 * n)` with increasing `n` so the replay guard does not reject them.

- [ ] **Step 7: Run them.** Expected: the five-wrong-codes test fails; fix by carrying an `attempts` count in the stashed challenge in `mfa_login` and not re-stashing at 5. Use the account email, not the user id, as the authenticator label in `mfa_enroll` when the JWT claims carry it.

- [ ] **Step 8: Full backend suite passes.**

- [ ] **Step 9: Commit** `feat(auth): durable TOTP MFA with replay guard and attempt limit`.

### Task 2: Frontend test hygiene and MFA screens

**Files:**
- Delete: `apps/web/src/__tests__/api-client.test.ts`, `error-boundary.test.tsx`, `error-capture.test.ts`, `middleware.test.ts`, `sentry-init.test.ts` (comment-only stubs with no suites)
- Modify: `apps/web/vitest.config.ts` (`include: ["src/**/*.test.{ts,tsx}"]`), `apps/web/src/lib/auth-api.ts`, `apps/web/src/app/sign-in/page.tsx`, `apps/web/src/app/settings/page.tsx`, `apps/web/package.json`
- Create: `apps/web/src/__tests__/auth-api.test.ts`, `apps/web/src/components/mfa-settings.tsx`

**Interfaces:**
- Consumes: Task 1's endpoints.
- Produces, in `auth-api.ts`:
  - `type MfaChallenge = { mfa_required: true; mfa_token: string }`
  - `login(email, password): Promise<AuthResult | MfaChallenge>`
  - `mfaLogin(mfaToken: string, code: string): Promise<AuthResult>`; on 401 throws `MfaCodeError` carrying `nextToken: string | null`
  - `mfaStatus(): Promise<{ enrolled: boolean }>`, `mfaEnroll(): Promise<{ secret: string; otpauth_uri: string }>`, `mfaConfirm(code): Promise<void>`, `mfaDisable(code): Promise<void>` (these four send the bearer token)
  - Errors read FastAPI's `detail`, whether a string or an object with `error`.

- [ ] **Step 1: Delete the five stubs and switch the Vitest include to the glob.** Run `npx vitest run`; expected: 26 tests pass.
- [ ] **Step 2: Write `auth-api.test.ts`** with `fetch` stubbed as in `saas-api.test.ts`: `login` returns the challenge object unchanged when the body has `mfa_required`; `mfaLogin` posts `{mfa_token, code}` to `/auth/mfa/login`; a 401 with `detail.mfa_token` throws `MfaCodeError` with that `nextToken`; a 401 with string `detail` surfaces that string as the message; `mfaEnroll` sends `Authorization: Bearer`.
- [ ] **Step 3: Run; expected to fail on missing exports. Implement them. Run; expected to pass.**
- [ ] **Step 4: Sign-in code step.** When `login` returns a challenge, replace the form with a six-digit code field (`inputMode="numeric"`, `autoComplete="one-time-code"`, labelled). On `MfaCodeError` with a `nextToken`, keep the step and show the error; with `null`, return to the password form with "Too many attempts. Sign in again."
- [ ] **Step 5: `mfa-settings.tsx`,** rendered in the Settings "Security" tab. States: off (button "Turn on two-step sign-in"), enrolling (QR image from `qrcode.toDataURL(otpauth_uri)`, the secret as text for manual entry, code field, confirm), on (code field and "Turn off"). `npm install qrcode @types/qrcode --legacy-peer-deps -w apps/web`.
- [ ] **Step 6: `npx tsc --noEmit && npx vitest run` pass. Start both servers and walk enrol, sign out, sign in with code, disable, in a browser.**
- [ ] **Step 7: Commit** `feat(web): two-step sign-in and MFA settings; drop stub tests`.

### Task 3: Password reset backend

**Files:**
- Create: `services/api/src/omni_modal/security/password_reset.py`, `services/api/tests/test_password_reset.py`
- Modify: `security/accounts.py`, `security/sessions.py`, `api/app.py`, `services/api/tests/test_fastapi_app.py`, `services/api/tests/test_sessions.py`

**Interfaces:**
- Produces:
  - `PasswordResetService(store, *, ttl_seconds=1800, max_per_hour=3)` with `issue(email: str) -> str | None` (raw token, or `None` when the hourly cap is hit) and `consume(token: str) -> str | None` (the email, once). Only `sha256(token)` is stored. `select_password_reset_service()` picks Postgres or in-memory.
  - `AccountService.set_password(email: str, new_password: str) -> Account | None`; raises `AccountError` under 8 characters; `None` when no account.
  - `SessionService.revoke_user(tenant_id: str, user_id: str) -> int` (families revoked). `issue` records each family in a per-user set.
  - `POST /auth/password/forgot {email}` always returns 200 `{"sent": true}`.
  - `POST /auth/password/reset {token, password}` returns 200 `{"reset": true}`, or 400 with `detail` "This reset link is invalid or has expired." or the password-length message.

- [ ] **Step 1: Write `test_password_reset.py`**

```python
def test_token_consumed_once(self):        # consume -> email; second consume -> None
def test_expired_token_refused(self):      # ttl_seconds=0 -> consume is None
def test_raw_token_not_stored(self):       # token string absent from every stored value
def test_hourly_cap(self):                 # 4th issue for the same email -> None
def test_email_match_is_case_insensitive(self):
```

- [ ] **Step 2: Run, see failures, implement the service and both stores, run to green.**
- [ ] **Step 3: Add to `test_sessions.py`:** after `revoke_user`, every refresh token issued to that user raises `RefreshTokenError`; another user's token still refreshes. Implement.
- [ ] **Step 4: Add HTTP tests to `test_fastapi_app.py`**

```python
def test_forgot_unknown_email_returns_sent_and_sends_nothing(self):  # 200 {"sent": True}; console adapter's .sent unchanged
def test_forgot_then_reset_changes_password(self):                   # old password 401, new password 200
def test_reset_link_cannot_be_reused(self):                          # second POST -> 400; password still the first new one
def test_reset_revokes_existing_refresh_tokens(self):                # pre-reset refresh_token -> 401 on /auth/refresh
def test_reset_rejects_short_password(self):                         # 400; token still usable afterwards
```

The token is read from the console email adapter's last message.

- [ ] **Step 5: Implement the endpoints.** The email goes through `state.saas.email.send`, subject "Reset your OMERO password", body containing `{APP_BASE_URL}/reset-password?token=...` and "This link works once and expires in 30 minutes." A short password must be checked before the token is consumed. Record `password_reset_requested` and `password_reset_completed` on the audit sink without the token or password.
- [ ] **Step 6: Full backend suite passes. Commit** `feat(auth): password reset with single-use tokens and session revocation`.

### Task 4: Password reset screens

**Files:**
- Create: `apps/web/src/app/forgot-password/page.tsx`, `apps/web/src/app/reset-password/page.tsx`
- Modify: `apps/web/src/lib/auth-api.ts`, `apps/web/src/components/app-shell.tsx` (`PUBLIC_ROUTES`), `apps/web/src/middleware.ts` (same public list, if it keeps one), `apps/web/src/app/sign-in/page.tsx`, `apps/web/src/__tests__/auth-api.test.ts`

**Interfaces:**
- Consumes: Task 3's two endpoints.
- Produces: `forgotPassword(email: string): Promise<void>`, `resetPassword(token: string, password: string): Promise<void>`.

- [ ] **Step 1: Add tests** that each function posts the right body to the right path and that a 400 surfaces `detail` as the error message. Run to red, implement, run to green.
- [ ] **Step 2: Build the two pages** in the visual style of `sign-up/page.tsx`. Forgot: one email field; after submit always shows "If an account exists for that address, a reset link is on its way." Reset: reads `token` from the query string, two password fields with a match check, success state linking to sign-in; with no token it shows the invalid-link message.
- [ ] **Step 3: Add "Forgot password?" under the password field on sign-in. Add both routes to the public lists.**
- [ ] **Step 4: Typecheck and tests pass. Walk the flow in a browser using the link printed by the console email adapter.**
- [ ] **Step 5: Commit** `feat(web): forgot and reset password screens`.

### Task 5: Remove invented claims and dead controls

**Files:**
- Create: `apps/web/src/__tests__/no-invented-claims.test.ts`
- Modify: `services/api/src/omni_modal/api/app.py` (`/projects`, `/archives`), `services/api/tests/test_fastapi_app.py`, `apps/web/src/components/research-chat.tsx`, `apps/web/src/app/archives/page.tsx`, `apps/web/src/app/projects/page.tsx`, `apps/web/src/app/settings/page.tsx`, `apps/web/src/app/sign-in/page.tsx`

- [ ] **Step 1: Backend test:** a `/projects` item has no `status`, `priority` or `updated` key; an `/archives` item has no `kind`, `classification`, `expiry`, `archived`, `accessed` or `size` key. Run to red, delete the fields, run to green.
- [ ] **Step 2: Write `no-invented-claims.test.ts`:** read every `.tsx` under `src/app` and `src/components` and assert none contains `"Confidence: High"`, `"AES-256"`, `"7 years"`, `"Sarah Jenkins"`, `"compliance mandates"`. Run to red.
- [ ] **Step 3: Frontend edits.**
  - `research-chat.tsx`: remove the confidence label (keep "Synthesizing..." while streaming) and the attach and mic icons.
  - `archives/page.tsx`: remove the retention banner, the Retention and classification columns and inspector rows, and the "Sync Storage" and row-menu buttons.
  - `projects/page.tsx`: remove the status badge and priority.
  - `settings/page.tsx`: replace the hard-coded `users` array with live data from `listMembers` in `lib/saas-api.ts`; remove buttons with no handler; check the "General" and "Models" tabs and remove any value not read from `/health`.
  - `sign-in/page.tsx`: render the demo-token and paste-token blocks only when `process.env.NODE_ENV !== "production"`, and update the page copy and header comment to describe email sign-in.
- [ ] **Step 4: Dead-control sweep.** For each file under `src/app` and `src/components`, every `<button` is `type="submit"` inside a form with `onSubmit`, or has `onClick`. List the exceptions found and fix each by wiring or removing.
- [ ] **Step 5: Typecheck, frontend tests and backend suite pass. Commit** `fix: remove invented claims and dead controls`.

### Task 6: Truthful README

**Files:**
- Modify: `README.md`, `apps/web/src/lib/phase.ts` (update, or delete if nothing imports it), `.env.example`

- [ ] **Step 1: Correct every statement that names the stdlib server as the backend** (lines 9, 25 and 43 today) to FastAPI, keeping the note that `omni_modal.main` is a reference implementation.
- [ ] **Step 2: Remove QLoRA from the layout comment and feature lists;** keep the single limitations entry that says no fine-tuned model exists.
- [ ] **Step 3: Document MFA and password reset** (endpoints, the Postgres tables, the `APP_BASE_URL` requirement for reset links) and add `0009_auth_recovery.sql` to the migration list.
- [ ] **Step 4: Add a "Known gaps" section** copied from the "Missing or misleading" column of the spec's audit table, minus what M0 fixed, so the README does not overstate M1 to M5 work.
- [ ] **Step 5: Replace the stated test counts with the numbers from a fresh run of both suites.**
- [ ] **Step 6: Commit** `docs: README matches the code`.

## Deployment note for the owner (after merge)

`0009_auth_recovery.sql` must be applied to Neon (`python scripts/migrate.py`), and `APP_BASE_URL` and `RESEND_API_KEY` must be set on Render for reset emails to carry a working link.
