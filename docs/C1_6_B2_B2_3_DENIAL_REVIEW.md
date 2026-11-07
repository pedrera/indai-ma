# C1.6-B2-B2.3 — Persistent local denial audit

The four later real denials and final closure status are consolidated in the
[C1.6 final review](C1_6_FINAL_REVIEW.md). The original-request classification
below remains unchanged.

## Original request: evidence remains unchanged

Correlation ID: `b526933c25e74080b1975df09ab8099b`.

- HTTP 403: confirmed by evidence from the real request.
- Concrete exception: not verifiable with retained evidence.
- No Supervisor construction: supported by code and tests, not retrospectively
  verifiable for this request. New instrumentation cannot reconstruct past events.

No replay is authorized by this document.

## Design and scope

`security_audit.py` provides an opt-in JSONL sink for POST /api/v1/analysis.
It is disabled unless INDAI_LOCAL_SECURITY_AUDIT is exactly 1. The explicit absolute
INDAI_SECURITY_AUDIT_FILE must be outside the repository and outside artifacts.
The existing file is opened append-only; it must be regular, single-link, and not
be a symlink/reparse point. Parent links are rejected on configuration. Files are
not created by the application. Writes are serialized within each process. Use
one API worker for these local tests; this is not a multi-process audit service.
At 10 MB writes stop, setting the in-memory sink.write_failed flag. This flag is
also set for write errors; no exception message or record is sent to the browser.
Authorization behavior remains unchanged, including when audit writes fail.
A missing record is never proof that an execution point was not reached.

A request-scoped ContextVar propagates into FastAPI worker threads, including
AnalysisService and actual Supervisor construction. HTTP start/end use middleware;
authentication observes the existing authenticator through SecurityContextFactory;
membership and permission events follow the actual successful checks or typed
exceptions. C1.3 and the authorization policy are unchanged. AnalysisService entry
and Supervisor start/completion are emitted at their actual call sites.

X-Validation-ID is accepted only as one 32-character lowercase hex value; otherwise
a fresh server identifier is generated. This is an untrusted correlation label,
never proof of identity, permission, or global uniqueness. SecurityContext uses the
same identifier. Principal is HMAC-SHA256 of issuer/subject using a random, private
process-local key; the key is not persisted, so linkage across restarts is unavailable.
Tenant is included only after identifier syntax validation. No token, cookie,
Authorization header, exception message, URL, user identifier in clear, or Business
payload is written. There are no public audit endpoints or Streamlit log views.

## Activation (manual, before a future reviewed test)

Keep business_first_run.approved=false and business_denial_probe.approved_post=false.
Do not restart current services as part of this implementation. At the next planned
API restart, first provision a dedicated private directory outside the repository.
Example PowerShell, executed by the local user (replace the example directory):

```powershell
$auditDir = Join-Path $env:LOCALAPPDATA 'indai-ma-security-audit'
New-Item -ItemType Directory -Path $auditDir -Force | Out-Null
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
icacls $auditDir /inheritance:r /grant:r "${account}:(OI)(CI)F"
$auditFile = Join-Path $auditDir 'denials.jsonl'
New-Item -ItemType File -Path $auditFile -ErrorAction Stop | Out-Null
icacls $auditFile
$env:INDAI_LOCAL_SECURITY_AUDIT = '1'
$env:INDAI_SECURITY_AUDIT_FILE = $auditFile
# Start FastAPI using its existing TLS/CA/membership configuration, loopback only.
```

Review ACL output: no broad user/group read or write access. The Windows sink
relies on this externally provisioned ACL; it does not set or certify Windows ACLs.
On POSIX provision directory 0700 and file 0600; permissive files are rejected.
Do not use a shared, cloud-synced, redirected or network directory. Directory ACLs
must prevent other users from replacing ancestors/files. No retention of credentials.
Do not print secret configuration while starting the API.

Deactivate for the next API start with:

```powershell
Remove-Item Env:INDAI_LOCAL_SECURITY_AUDIT -ErrorAction SilentlyContinue
Remove-Item Env:INDAI_SECURITY_AUDIT_FILE -ErrorAction SilentlyContinue
```

Environment changes affect new API processes only. Retain audit records only until
review concludes (maximum seven days recommended for this local test). Stop the
owning API before rotation/deletion, review and remove only the explicitly selected
JSONL with Remove-Item -LiteralPath. Do not recursively delete a computed directory.
No automatic rotation, deletion, OS trust changes, or new dependency is introduced.

Fictitious event:

```json
{"timestamp":"2026-10-30T10:00:00+00:00","correlation_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","event":"permission_authorization_denied","stage":"OPERATION_AUTHORIZATION","principal":"2dd8fbc7b40c4aa1e47663ce47f5e4aae5137aaec28311b0e6f2c10ec4ee0b21","tenant":"tenant-alpha","exception_type":"PermissionDenied"}
```

## Next negative test: prepared procedure, not executed

1. Provision and verify the private sink and start the API with audit enabled in a
   separately reviewed maintenance step. Confirm the file is writable privately.
2. Keep both approvals false; renew viewer-a native login manually if needed.
3. Verify session 200, backend principal, tenant-alpha membership, absent analysis
   permission and no pending job. Analyze remains disabled.
4. Obtain independent approval for precisely one new negative POST. Never reuse or
   reinterpret the original correlation ID as a new test.
5. After that future request, correlate request_received through request_completed,
   require PermissionDenied at OPERATION_AUTHORIZATION and HTTP 403. Check absence
   of execution events only together with a complete, healthy audit sequence.
6. Close the one-shot approval and review before any additional user/tenant test.

Tests use local TestClient, generated test JWTs, mock JWKS and deterministic tools;
no real pilot request, external LLM, real credentials or Keycloak changes are used.
