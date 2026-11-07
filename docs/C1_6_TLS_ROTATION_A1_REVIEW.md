# C1.6 TLS — A1 joint CA/server staging review

Executed 2026-10-31: **A1 complete, operational substitution NOT executed**.
The user selected joint CA/server rotation and explicitly confirmed CA=true for
the root and CA=false for localhost. This report contains public metadata only.
No production code, Keycloak configuration, clients, memberships, C1.3, active
certificate, active trust store or running service was changed.

## Preflight and baseline

- Git read-only reference: main, HEAD
  2f6193b1dc0a3cc7c9d98162d6b7233eabda504b. The working tree contains the earlier
  pilot changes; no new Git checkpoint exists. The formal closure/proposed baseline
  is available in C1_6_FINAL_REVIEW.md and C1_6_GIT_BASELINE_PROPOSAL.md.
- Active private root:
  C:\Users\pedre\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\indai-ma-keycloak-dev.
  Key paths and their ancestors were checked for symlink/reparse redirection.
- Current ca/ca-cert.pem, ca/ca-cert.cer, ca/ca-key.pem,
  tls/localhost-cert.pem and tls/localhost-key.pem exist. Leaf+CA signature and
  dates verified. Old leaf expires 08/11/2026 16:30:15 Europe/Madrid; old CA
  24/11/2026 16:30:15. They remain the active material.
- Keycloak running/healthy; TLS directory bind-mounted read-only at
  /opt/keycloak/conf/dev-tls, only 127.0.0.1:8443 published. No mount changed.
  FastAPI and Streamlit listener addresses remain 127.0.0.1:8000/8501.
- FastAPI uses explicit INDAI_OIDC_CA_FILE plus certifi public roots. Streamlit
  startup uses SSL_CERT_FILE for the same external public CA. Nothing was installed
  into certifi. Existing issuer, audience, discovery, JWKS and redirect remain fixed.
- CurrentUser\Root and LocalMachine\Root were read only: neither the old local CA
  fingerprint nor the staged new CA was present. This does NOT establish all
  browser trust sources. Chrome metadata showed a User Data root and no insecure
  TLS flags, not an unambiguous isolated profile/certificate-store mapping.
  Historical browser trust succeeded, but the exact profile-local trust source
  requires manual certificate-manager verification before A2. Do not import into
  either Windows Root store as an inferred substitute.
- The existing recovery procedure is expanded below. approved=false and
  approved_post=false were verified before preparation; no Business POST.

## Private output and recoverable backup

New external directory:
`C:\Users\pedre\indai-ma-tls-rotation-a1-20261031`.

| Private subpath | Purpose |
| --- | --- |
| backup/ca/ca-cert.pem, ca-cert.cer, ca-key.pem | Original CA and available private key |
| backup/tls/localhost-cert.pem, localhost-key.pem | Original server chain/key |
| backup/tls-config-snapshot.json | Allowlisted public TLS/OIDC path configuration, mount, image and private file hash manifest; no client/cookie/password secrets |
| staging/ca/ca-cert.pem, ca-cert.cer, ca-key.pem | New local CA public copies and fresh key |
| staging/tls/localhost-cert.pem, localhost-key.pem | New leaf+CA PEM chain and fresh server key |
| validation-summary.json | Safe machine-readable A1 results and public fingerprints |

Parent inheritance is disabled. ACLs reviewed on the parent and representative
backup/staging key files allow only PEDRE\pedre, SYSTEM and Administrators. Child
files inherit those restricted entries. Keys are unencrypted PKCS8 as required by
the existing local server setup, protected by filesystem ACLs, never copied into
the repository. No secret file is printed or exposed through an endpoint.

All five backup file hashes equal the corresponding original bytes. Both backup
private keys deserialize and match their certificates. A random in-memory
challenge was signed and verified with each backed-up pair, proving usable key
recovery without revealing content. The filtered configuration snapshot is enough
for the planned path-preserving replacement: OAuth secrets/config are not changed.
Restoration into active paths was not tested or executed.

Keep this private backup through the approved rollback window. Old leaf expiry
is the hard rollback cutoff, even though its CA lasts longer. Review disposal of
old keys and the failed/unused staging set after A2; do not leave private-key
duplicates indefinitely. No backup deletion is authorized by this report.

## New PKI and validation results

Generation time: 2026-10-31 12:39:09Z. Development lifetimes retained: CA 30 days,
server 14 days; both backdated five minutes for notBefore only. These dates start
at staging generation, not activation. Reassess remaining life before A2.

| Property | New CA | New server |
| --- | --- | --- |
| Key | Fresh RSA3072, exponent 65537 | Fresh RSA2048, exponent 65537 |
| Signature | SHA-256 with RSA | SHA-256 with RSA, signed by new CA |
| Basic Constraints (critical) | CA=true, path_length=0 | CA=false |
| Key Usage (critical) | keyCertSign, cRLSign | digitalSignature, keyEncipherment; no CA signing |
| Extended Key Usage | Not needed for root | serverAuth |
| SAN | Not needed for root | DNS localhost, IP 127.0.0.1 |
| Expiry UTC | 2026-11-30 12:39:09Z | 2026-11-14 12:39:09Z |
| Expiry Europe/Madrid | 30/11/2026 13:39:09 | 14/11/2026 13:39:09 |

New CA SHA-256:
`61cee7e1acb89174a474ca9c3616ea8b22ce5a011d72b7ebfd10591a7559f04d`.
New leaf SHA-256:
`b3a381136b3d81a6f1db4d95e406e6096d5ae5d1a8b3848d238dca15dbe8abad`.

Sixteen checks passed:

1. Backup bytes and recovered keys usable.
2. Both new keys differ from old keys.
3. New serials positive/random/distinct from one another and originals.
4. Root self-signature and server issuance signature verify.
5. Correct CA/non-CA constraints.
6. Appropriate CA/server key usages.
7. ServerAuth EKU.
8. Exact localhost/IP SAN.
9. Current, nested validity windows; leaf expires before CA.
10. RSA strengths and SHA-256 signatures.
11. Persisted PEM key/certificate pairs match.
12. Offline TLS 1.3 handshake succeeds for localhost.
13. Offline TLS 1.3 handshake succeeds for 127.0.0.1.
14. Old CA rejects new server chain.
15. Wrong hostname rejected under new CA.
16. All five original active-file hashes unchanged after generation.

TLS handshakes used SSL MemoryBIO only: no network listener/service was created.
The existing Keycloak PEM/PKCS8 configuration format is compatible with the new
material and Python/OpenSSL loads the chain/key. Actual Keycloak runtime loading
and non-root readability of the staged pair remain A2 checks, not claimed results.

## Exact proposed A2 transition — not authorized here

0. Recheck fingerprints/dates, approve the maintenance window and inspect the
   backup. Verify no jobs and both flags false. Manually identify the intended
   isolated Chrome Profile Path and its certificate manager; find the old public
   CA by fingerprint. If trust cannot be scoped there, stop and request a specific
   trust-store decision. Preserve the old public trust during transition.
1. Stop Streamlit and FastAPI gracefully first, then stop only Keycloak. Revalidate
   process identities; do not kill cached PIDs, remove the data volume or reimport
   realm/users. Keep all JWT signing keys/clients/mappers/memberships intact.
2. While stopped, replace ONLY the five active ca/tls files with the reviewed
   staging counterparts, preserving physical paths, private ACLs, matched pairs
   and the existing read-only mount. No compose/env/client edit is needed for this
   path-preserving option. Verify hashes against staging before starting anything.
3. Verify FastAPI INDAI_OIDC_CA_FILE still points to active ca/ca-cert.pem, now the
   new public CA, while retaining certifi public roots. Verify Streamlit
   SSL_CERT_FILE points to that same active file. These contexts reload on the
   later process starts; do not silently use default/system trust instead.
4. With user participation and explicit authorization, add ONLY the staged/new
   public CA to the identified approved Chrome trust source. Never import a key,
   change Windows global trust implicitly or use insecure browser flags. Retain
   old public trust until consumer inventory and success checks finish.
5. Start only Keycloak. Confirm unprivileged key readability, healthy status,
   loopback/HTTPS-only publication and exact new served fingerprint. Verify TLS1.3
   hostname/chain, discovery issuer and JWKS using explicit new CA trust.
6. Start FastAPI with the reviewed CA configuration. Confirm verified JWKS fetch,
   ready/health 200 and unauthenticated /session 401, no analysis request. API has
   no dependency on a logged-in browser or Streamlit for startup.
7. Start Streamlit in session-only mode with new explicit CA trust and existing
   private OIDC config. Use the intended Chrome profile for fresh manual login.
   Confirm the real API principal, /session 200, expected grants, empty tenant
   selection, no inherited data and disabled Business. Token exchange/discovery
   must verify TLS; never substitute the ID token for access credentials.
8. Test native logout and a fresh manual login without a POST. Distinguish local
   logout from SSO/token revocation. Leave both approvals false.
9. Inventory other consumers of old CA by configuration/profile/store, not by
   assumption. Only after success and separate reviewed cleanup remove the exact
   old public trust entry and retire obsolete private keys/backups. Do not remove
   unrelated roots, shared bundles or old material still required for rollback.

No circular dependency exists: public CA and files are prepared offline; Keycloak
starts first, then API/JWKS, then Streamlit, then browser login. Client contexts
load reviewed trust before any OIDC request. Old trust is not removed prematurely.

## Success, stop criteria and recovery

Success requires the new live fingerprint and chain/SAN/dates, TLS1.3, healthy
Keycloak, exact discovery/JWKS/issuer, ready API/UI on loopback, real native login
and /session 200 with expected principal, logout, no residual data and both flags
false. Offline A1 checks alone do not establish operational success.

Stop on unexpected trust scope, changed clients/issuer, mismatched key pair,
unreadable file, unavailable health/JWKS, TLS failure, wrong identity/grants or any
unexpected execution. Do not retry Business or relax verification to diagnose.

Rollback while old material remains valid: stop affected clients/Keycloak, restore
the five backed-up files byte-for-byte, recheck the backed-up public fingerprints,
paths/mount/ACLs, restore explicit trust references (same paths in the proposed
option), and undo only specifically added browser trust if needed. Start Keycloak,
verify old TLS/discovery/JWKS, then API/Streamlit and native login. Leave approvals
false. Old leaf 08/11/2026 16:30:15 is the effective rollback deadline.

After old leaf/CA expiry, restoring them is NOT recovery. Keep the affected service
unavailable and correct/reissue a valid matched set with a trusted, valid CA under
fresh explicit authorization. Revalidate the full chain and trust distribution.
No verify=false, expired certificate acceptance, clock change or HTTP fallback.

## Risks, remaining dependencies and authorizations

- A1 staging validity is already running: new leaf expires 14/11 and CA 30/11.
  After activation revise follow-up targets (for example leaf before 12/11 and CA
  before 26/11); do not keep the old 20/11 CA target as a substitute for earlier
  new-leaf maintenance. No reminder/rotation is scheduled by this report.
- Exact Chrome profile/local trust source remains manually unverified. Neither
  Windows Root store contains the old CA; this is a trust-inventory limitation,
  not a reason to install a global root.
- Backup/staging are sensitive despite being outside Git; restrict access and
  define cleanup after rollback acceptance. Docker administrators retain broad
  host/container powers. New CA private key must never be mounted in Keycloak.
- No durable new Git baseline was created; active TLS recovery uses the private
  verified backup and filtered configuration snapshot, not an uncommitted Git diff.

Actions requiring further explicit authorization:

1. Maintenance downtime / stopping and starting Keycloak, API and Streamlit.
2. Substitution of the five active files and any approved path/config deviation.
3. Loading new trust in running Python processes via restart.
4. Installing the public CA in the specifically identified Chrome trust source;
   any Windows-store alternative requires its own decision.
5. Manual login/logout and real /session verification after substitution.
6. Removal of old trust after consumer inventory, deletion of old keys/backups or
   private audit records, or regeneration if staging is stale/expired.

No A2 action, POST, audit deletion, Git write, C2 or C3 has occurred.
