# review-loop judge: hand labels

For each item, write `Label: yes` if the comment identifies the defect (a developer
reading only the comment would find and fix it), or `Label: no` if it's about something
else, only vague, or only describes the change. The judge's answers are not shown.

## item-28a5ecc4ad

**Defect:** a file of exactly 10 MiB is rejected

```diff
--- a/backend/src/vendor_intake/services/documents.py
+++ b/backend/src/vendor_intake/services/documents.py
@@ -34,7 +34,7 @@
 
 def validate_upload(filename: str, declared_type: str | None, size: int, max_bytes: int) -> str:
     """Returns the canonical content type, inferred from the extension."""
-    if size > max_bytes:
+    if size >= max_bytes:
         raise AppError(413, "file_too_large", f"Files must be {max_bytes} bytes or smaller")
     expected = CONTENT_TYPES.get(Path(filename).suffix.lower())
     declared = (declared_type or GENERIC_TYPE).split(";")[0].strip().lower()
```

**Comment:**

> backend/src/vendor_intake/services/documents.py:37 (issue, blocking)
> Size check changed from > to >=, rejecting files exactly at the limit
>
> The PR title says 'Update documents' and gives no rationale for the change. With >=, a file whose size is exactly max_bytes is now rejected, though the error message says 'Files must be {max_bytes} bytes or smaller', which permits exactly max_bytes. The new check contradicts the message and is an off-by-one behavior change with no test or stated intent.

Label: yes

## item-49d361a343

**Defect:** uploads are recorded as document.added instead of document.uploaded

```diff
--- a/backend/src/vendor_intake/services/documents.py
+++ b/backend/src/vendor_intake/services/documents.py
@@ -75,7 +75,7 @@
         tenant_id=user.tenant_id,
         vendor_id=vendor.id,
         actor_id=user.id,
-        action="document.uploaded",
+        action="document.added",
         details={"document_id": document_id, "kind": kind, "filename": document.filename},
     )
     session.commit()
```

**Comment:**

> backend/src/vendor_intake/services/documents.py:78 (issue)
> Renaming the audit action splits the audit history and may break consumers
>
> Audit rows already stored with `document.uploaded` keep that name, while new uploads will be recorded as `document.added`. Anything that filters, labels, or tests on `document.uploaded` will silently stop matching new uploads. That includes audit log UIs, reports, test assertions and exports. The PR has no migration for existing rows and no matching consumer updates. The new name also breaks the past-tense convention the other events use (`document.extracted`, `document.failed`). Keep the original name, or rename it together with a data migration and consumer updates.

Label: yes

## item-c5ffaf4589

**Defect:** missing sessions report code unauthorized instead of not_authenticated

```diff
--- a/backend/src/vendor_intake/deps.py
+++ b/backend/src/vendor_intake/deps.py
@@ -50,7 +50,7 @@
 ) -> User:
     token = _token_from(request)
     if token is None:
-        raise AppError(401, "not_authenticated", "Sign in to continue")
+        raise AppError(401, "unauthorized", "Sign in to continue")
     claims = decode_token(token, settings.jwt_secret)
     user = UserRepository(session).get(claims["sub"], claims["tid"]) if claims else None
     if user is None:
```

**Comment:**

> backend/src/vendor_intake/deps.py:53 (issue, blocking)
> Machine-readable error code for missing credentials changed from `not_authenticated` to `unauthorized`
>
> The error code is part of the API contract. Clients and tests that branch on `not_authenticated` (for example, to redirect to sign-in) will stop matching. Unlike the free-text message, a code is meant to stay stable. The PR title "Update deps" gives no hint of an intentional contract change, and the diff does not update any clients, docs or tests. Either revert the code or treat this as a coordinated breaking change.

Label: yes

## item-a69a4f9b7e

**Defect:** limit=101 is accepted

```diff
--- a/backend/src/vendor_intake/routes/vendors.py
+++ b/backend/src/vendor_intake/routes/vendors.py
@@ -25,7 +25,7 @@
 def list_vendors(
     q: str | None = Query(default=None, max_length=120),
     status: VendorStatus | None = None,
-    limit: int = Query(default=20, ge=1, le=100),
+    limit: int = Query(default=20, ge=1, le=1000),
     offset: int = Query(default=0, ge=0),
     user: User = Depends(current_user),
     session: Session = Depends(get_session),
```

**Comment:**

> backend/src/vendor_intake/routes/vendors.py:28 (issue, blocking)
> Page size cap raised from 100 to 1000 with no stated reason
>
> The PR title 'Update vendors' does not explain raising the maximum page size tenfold. Allowing up to 1000 rows per request increases load on the database and the per-row to_out serialization, and it makes it easier for a client to pull a tenant's whole vendor list in one call. This is a behavior change to a public API that should be justified or reverted.

Label: yes

## item-2bb93ab552

**Defect:** a file one byte over 10 MiB is accepted

```diff
--- a/backend/src/vendor_intake/routes/review.py
+++ b/backend/src/vendor_intake/routes/review.py
@@ -9,7 +9,7 @@
 from vendor_intake.services import review
 
 router = APIRouter(tags=["review"])
-reviewer = require_roles(Role.REVIEWER, Role.ADMIN)
+reviewer = require_roles(Role.MEMBER, Role.REVIEWER, Role.ADMIN)
 
 
 @router.get("/review-queue")
```

**Comment:**

> backend/src/vendor_intake/routes/review.py:12 (issue, blocking)
> Ordinary members can now approve or reject extractions in the review workflow.
>
> Adding Role.MEMBER to the `reviewer` dependency opens both the review queue and the `/extractions/{id}/decision` endpoint to every tenant member. That removes the separation of duties the review step exists for. A member could submit a vendor document and then approve its extraction. The PR gives no reason for the escalation, and no test covers it.

Label: no

## item-8fd6d20ca3

**Defect:** an invalid contact email is accepted

```diff
--- a/backend/src/vendor_intake/schemas.py
+++ b/backend/src/vendor_intake/schemas.py
@@ -55,7 +55,7 @@
 class VendorCreate(BaseModel):
     name: VendorName
     category: VendorCategory
-    contact_email: EmailStr
+    contact_email: str
 
 
 class VendorUpdate(BaseModel):
```

**Comment:**

> backend/src/vendor_intake/schemas.py:58 (issue, blocking)
> VendorCreate.contact_email no longer validates email format
>
> Changing EmailStr to plain str removes format validation when a vendor is created. Any string is now accepted, including an empty one, and gets stored as the contact email. VendorUpdate still uses EmailStr, so create and update now disagree: a value accepted at creation would be rejected by an update. The LoginRequest comment explains why login uses a plain string, but that reasoning does not apply to vendor creation, and the PR gives no justification.

Label: yes

## item-7cbb68a95b

**Defect:** each page starts one row late, so the last page is short

```diff
--- a/backend/src/vendor_intake/repositories/vendors.py
+++ b/backend/src/vendor_intake/repositories/vendors.py
@@ -41,7 +41,7 @@
             query = query.where(Vendor.status == status)
         total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
         rows = self.session.scalars(
-            query.order_by(Vendor.created_at.desc(), Vendor.id).limit(limit).offset(offset)
+            query.order_by(Vendor.created_at.desc(), Vendor.id).limit(limit).offset(offset + 1)
         )
         return list(rows), total
```

**Comment:**

> backend/src/vendor_intake/repositories/vendors.py:44 (issue, blocking)
> Off-by-one offset skips the first vendor on every page
>
> Using `offset + 1` drops one row per request. The first page (offset=0) never returns the newest vendor, and the row at the boundary between pages is never shown. `total` still counts every row, so callers paginating to `total` will see inconsistent results.

Label: yes

## item-b8a43cbd4c

**Defect:** an empty upload is accepted

```diff
--- a/backend/src/vendor_intake/services/documents.py
+++ b/backend/src/vendor_intake/services/documents.py
@@ -41,7 +41,7 @@
     if expected is None or declared not in (expected, GENERIC_TYPE):
         allowed = ", ".join(sorted(CONTENT_TYPES))
         raise AppError(415, "unsupported_type", f"Upload a file with one of: {allowed}")
-    if size == 0:
+    if size < 0:
         raise AppError(422, "empty_file", "The uploaded file is empty")
     return expected
```

**Comment:**

> backend/src/vendor_intake/services/documents.py:44 (issue, blocking)
> Empty-file check no longer rejects zero-byte uploads
>
> A size can never be negative, so `size < 0` is always false and the check never fires. Zero-byte uploads are now accepted. They get stored, and extraction then fails on them or produces nothing. The error code and message still say 'empty_file', which shows the intent was to reject size 0.

Label: yes

## item-497d70489b

**Defect:** audit events are listed newest first

```diff
--- a/backend/src/vendor_intake/services/documents.py
+++ b/backend/src/vendor_intake/services/documents.py
@@ -57,7 +57,6 @@
     content_type: str,
     data: bytes,
 ) -> Document:
-    ensure_editable(vendor)
     document_id = new_id()
     document = Document(
         id=document_id,
```

**Comment:**

> backend/src/vendor_intake/services/documents.py:60 (issue, blocking)
> Upload no longer checks whether the vendor is editable
>
> The removed ensure_editable(vendor) call was the guard that stopped documents from being uploaded to vendors in a non-editable state. Without it, any authorized user can add documents to locked vendors, which bypasses the workflow rule. Nothing else in this diff enforces that rule.

Label: no

## item-285d8d6a82

**Defect:** the profile shows the tenant id where the tenant name belongs

```diff
--- a/backend/src/vendor_intake/services/extraction.py
+++ b/backend/src/vendor_intake/services/extraction.py
@@ -24,7 +24,7 @@
             return datetime.strptime(raw, fmt).date().isoformat(), confidence
         except ValueError:
             continue
-    return None, 0.3
+    return None, 0.9
 
 
 def _money(raw: str) -> tuple[str | None, float]:
```

**Comment:**

> backend/src/vendor_intake/services/extraction.py:27 (issue, blocking)
> Unparseable dates now report high confidence (0.9) with a null value
>
> A date that fails to parse produces no value, but it now carries 0.9 confidence, higher than a successfully parsed MM/DD/YYYY date (0.85). `lowest_confidence` drives routing to manual review. Documents with garbled expiry or effective dates would skip review and be treated as confidently extracted, even though the date is missing. This is inconsistent with `_money` and `_tax_id_last4`, which return 0.3 on failure.

Label: no

## item-f526a377e1

**Defect:** an unknown email gets a different error code than a wrong password

```diff
--- a/backend/src/vendor_intake/routes/auth.py
+++ b/backend/src/vendor_intake/routes/auth.py
@@ -31,7 +31,9 @@
     settings: Settings = Depends(get_settings),
 ) -> LoginResponse:
     user = UserRepository(session).by_email(str(body.email))
-    if not check_credentials(user, body.password) or user is None:
+    if user is None:
+        raise AppError(401, "unknown_email", "No account uses that email")
+    if not check_credentials(user, body.password):
         raise AppError(401, "invalid_credentials", "Email or password is incorrect")
     token = issue_token(user, settings.jwt_secret, settings.jwt_ttl_minutes)
     response.set_cookie(
```

**Comment:**

> backend/src/vendor_intake/routes/auth.py:34 (issue, blocking)
> Login now distinguishes unknown email from wrong password, enabling account enumeration
>
> Returning a different error code and message for a nonexistent email versus a wrong password lets an attacker enumerate valid accounts. The previous code deliberately returned the same generic invalid_credentials response for both cases. Splitting them weakens authentication and also changes the API error contract clients may depend on.

Label: yes

## item-f7ce9fcaae

**Defect:** POST /api/vendors answers 200 instead of 201

```diff
--- a/backend/src/vendor_intake/routes/vendors.py
+++ b/backend/src/vendor_intake/routes/vendors.py
@@ -42,7 +42,7 @@
     )
 
 
-@router.post("", status_code=201)
+@router.post("", status_code=200)
 def create_vendor(
     body: VendorCreate,
     user: User = Depends(current_user),
```

**Comment:**

> backend/src/vendor_intake/routes/vendors.py:45 (issue, blocking)
> Create endpoint status code changed from 201 to 200, a silent API contract break
>
> POST /vendors now returns 200 instead of 201 Created. Clients checking for 201 or relying on REST semantics will break, and the PR title ('Update vendors') does not mention this change. It should be reverted unless the contract change is intentional and documented.

Label: yes

## item-98da7261da

**Defect:** failed sign-in answers 403 instead of 401

```diff
--- a/backend/src/vendor_intake/deps.py
+++ b/backend/src/vendor_intake/deps.py
@@ -50,7 +50,7 @@
 ) -> User:
     token = _token_from(request)
     if token is None:
-        raise AppError(401, "not_authenticated", "Sign in to continue")
+        raise AppError(401, "unauthorized", "Sign in to continue")
     claims = decode_token(token, settings.jwt_secret)
     user = UserRepository(session).get(claims["sub"], claims["tid"]) if claims else None
     if user is None:
```

**Comment:**

> backend/src/vendor_intake/deps.py:53 (issue, blocking)
> Machine-readable error code for missing auth changed from `not_authenticated` to `unauthorized`
>
> The error code is part of the API contract. Clients may branch on `not_authenticated` to trigger the sign-in flow, and existing tests may assert on it. Any such client or test silently breaks after this rename. The PR title ('Update deps') doesn't mention the contract change, and the diff updates no clients or tests to match.

Label: yes

## item-4725420079

**Defect:** a certificate expiring in exactly 30 days is not flagged

```diff
--- a/backend/src/vendor_intake/services/vendors.py
+++ b/backend/src/vendor_intake/services/vendors.py
@@ -36,7 +36,7 @@
     vendor = Vendor(
         tenant_id=user.tenant_id,
         name=data.name,
-        name_key=data.name.lower(),
+        name_key=data.name,
         category=data.category,
         contact_email=str(data.contact_email).lower(),
         created_by=user.id,
```

**Comment:**

> backend/src/vendor_intake/services/vendors.py:39 (issue, blocking)
> name_key no longer lowercased on create, breaking case-insensitive uniqueness
>
> update_vendor still sets name_key to changes["name"].lower(), so name_key is meant to be a normalized key. name_taken most likely compares against a lowercased name_key. Storing the raw name on create lets "Acme" and "acme" both exist. It also leaves create and update inconsistent: the same vendor gets a different key after a rename.

Label: no

## item-6c508462af

**Defect:** rejecting without a note is accepted

```diff
--- a/backend/src/vendor_intake/schemas.py
+++ b/backend/src/vendor_intake/schemas.py
@@ -113,7 +113,7 @@
 class ReviewDecision(BaseModel):
     decision: Literal["approve", "reject"]
     # validate_default: an omitted note must still trip the reject rule below.
-    note: str | None = Field(default=None, max_length=1000, validate_default=True)
+    note: str | None = Field(default=None, max_length=1000, validate_default=False)
 
     @field_validator("note")
     @classmethod
```

**Comment:**

> backend/src/vendor_intake/schemas.py:116 (issue, blocking)
> Setting validate_default=False lets a reject decision without a note pass validation
>
> With validate_default=False, Pydantic skips the field validator when note is omitted, so _reject_needs_note never runs for the default None. A request like {"decision": "reject"} with no note is accepted, which breaks the rule the adjacent comment describes. The comment now also contradicts the code.

Label: yes

## item-4d86a77877

**Defect:** uploads are recorded as document.added instead of document.uploaded

```diff
--- a/backend/src/vendor_intake/routes/review.py
+++ b/backend/src/vendor_intake/routes/review.py
@@ -9,7 +9,7 @@
 from vendor_intake.services import review
 
 router = APIRouter(tags=["review"])
-reviewer = require_roles(Role.REVIEWER, Role.ADMIN)
+reviewer = require_roles(Role.MEMBER, Role.REVIEWER, Role.ADMIN)
 
 
 @router.get("/review-queue")
```

**Comment:**

> backend/src/vendor_intake/routes/review.py:12 (issue, blocking)
> Role.MEMBER added to reviewer dependency, granting review and decision rights to members
>
> The reviewer dependency guards the review queue and the approve/reject decision endpoint. Adding Role.MEMBER widens access so that lower-privileged members can now read the review queue and approve or reject vendor extractions, which is an authorization regression. The PR title ('Update review') gives no justification for this change.

Label: no

## item-0a1f4b5202

**Defect:** an invalid contact email is accepted

```diff
--- a/backend/src/vendor_intake/services/vendors.py
+++ b/backend/src/vendor_intake/services/vendors.py
@@ -50,7 +50,6 @@
 
 
 def update_vendor(session: Session, user: User, vendor: Vendor, data: VendorUpdate) -> Vendor:
-    ensure_editable(vendor)
     changes = data.model_dump(exclude_unset=True, exclude_none=True)
     if "name" in changes:
         _ensure_name_free(session, user.tenant_id, changes["name"], exclude=vendor.id)
```

**Comment:**

> backend/src/vendor_intake/services/vendors.py:52 (issue, blocking)
> update_vendor no longer checks that the vendor is editable, so locked vendors can be modified
>
> The removed ensure_editable(vendor) call was the only guard that stops edits to vendors in non-editable statuses (anything outside DRAFT and REJECTED). Without it, a vendor in a locked status can have its name, contact email, and other fields changed, and the change is committed. The PR title 'Update vendors' does not mention removing this check. Restore the guard, or document and test the intended behavior change if it is deliberate.

Label: no

## item-c66a2e89fd

**Defect:** an invalid contact email is accepted

```diff
--- a/backend/src/vendor_intake/schemas.py
+++ b/backend/src/vendor_intake/schemas.py
@@ -55,7 +55,7 @@
 class VendorCreate(BaseModel):
     name: VendorName
     category: VendorCategory
-    contact_email: EmailStr
+    contact_email: str
 
 
 class VendorUpdate(BaseModel):
```

**Comment:**

> backend/src/vendor_intake/schemas.py:58 (issue, blocking)
> VendorCreate no longer validates contact_email, while VendorUpdate still does
>
> Changing VendorCreate.contact_email from EmailStr to str removes format validation on vendor creation. Malformed addresses will now be accepted and persisted, and the API contract silently changes (no longer 422 on bad email). The PR title 'Update schemas' does not describe this behavioral change, and the LoginRequest comment shows the codebase deliberately uses str only for login, not for vendor data. Also creates an inconsistency with VendorUpdate, which still enforces EmailStr.

Label: yes

## item-14f1cb55e4

**Defect:** limit=101 is accepted

```diff
--- a/backend/src/vendor_intake/routes/auth.py
+++ b/backend/src/vendor_intake/routes/auth.py
@@ -38,7 +38,7 @@
         SESSION_COOKIE,
         token,
         max_age=settings.jwt_ttl_minutes * 60,
-        httponly=True,
+        httponly=False,
         samesite="lax",
         secure=settings.cookie_secure,
     )
```

**Comment:**

> backend/src/vendor_intake/routes/auth.py:41 (issue, blocking)
> Session JWT cookie is now readable by JavaScript (httponly disabled)
>
> With httponly=False, any XSS on the origin can read the session token from document.cookie and steal the session. That token is usable until the JWT TTL expires, so the attacker can replay it from anywhere. The PR gives no reason for the change. If the frontend needs the token, it already gets access_token in the login response body.

Label: no

## item-8f114dbc31

**Defect:** signing out leaves the session cookie in place

```diff
--- a/backend/src/vendor_intake/repositories/vendors.py
+++ b/backend/src/vendor_intake/repositories/vendors.py
@@ -41,7 +41,7 @@
             query = query.where(Vendor.status == status)
         total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
         rows = self.session.scalars(
-            query.order_by(Vendor.created_at.desc(), Vendor.id).limit(limit).offset(offset)
+            query.order_by(Vendor.created_at.desc(), Vendor.id).limit(limit).offset(offset + 1)
         )
         return list(rows), total
```

**Comment:**

> backend/src/vendor_intake/repositories/vendors.py:44 (issue, blocking)
> Pagination offset shifted by one, skipping the first row of every page
>
> Adding +1 to offset means page 1 (offset 0) skips the newest vendor, and each subsequent page skips one item and drops the boundary row. The PR title 'Update vendors' does not describe this behavioral change, and no tests or justification accompany it.

Label: no

## item-5ec4c275bd

**Defect:** a name used by another tenant is rejected with 409

```diff
--- a/backend/src/vendor_intake/repositories/vendors.py
+++ b/backend/src/vendor_intake/repositories/vendors.py
@@ -19,7 +19,7 @@
 
     def name_taken(self, tenant_id: str, name: str, exclude_id: str | None = None) -> bool:
         query = select(Vendor.id).where(
-            Vendor.tenant_id == tenant_id, Vendor.name_key == name.lower()
+            Vendor.name_key == name.lower()
         )
         if exclude_id:
             query = query.where(Vendor.id != exclude_id)
```

**Comment:**

> backend/src/vendor_intake/repositories/vendors.py:22 (issue, blocking)
> name_taken no longer scopes the uniqueness check to the tenant
>
> The tenant_id filter was removed from the query, so name_taken now checks for a name collision across all tenants. A vendor name that exists in another tenant will be reported as taken, leaking cross-tenant existence information and blocking legitimate creates in the current tenant. The tenant_id parameter is now unused. This breaks tenant isolation.

Label: yes

## item-9566059940

**Defect:** an unknown email gets a different error code than a wrong password

```diff
--- a/backend/src/vendor_intake/services/extraction.py
+++ b/backend/src/vendor_intake/services/extraction.py
@@ -24,7 +24,7 @@
             return datetime.strptime(raw, fmt).date().isoformat(), confidence
         except ValueError:
             continue
-    return None, 0.3
+    return None, 0.9
 
 
 def _money(raw: str) -> tuple[str | None, float]:
```

**Comment:**

> backend/src/vendor_intake/services/extraction.py:27 (issue, blocking)
> Unparseable dates now report high confidence (0.9), so they skip manual review.
>
> When neither date format parses, the value is None. Reporting 0.9 confidence for that null is higher than the 0.85 for a successfully parsed MM/DD/YYYY date. `lowest_confidence` drives routing to manual review, so a document with a garbled expiry/effective date can now auto-pass. The result is an insurance certificate or contract with a null expiry date and no human review. The other parsers still return 0.3 on failure, which makes this inconsistent.

Label: no

## item-d773d4363e

**Defect:** missing sessions report code unauthorized instead of not_authenticated

```diff
--- a/backend/src/vendor_intake/schemas.py
+++ b/backend/src/vendor_intake/schemas.py
@@ -55,7 +55,7 @@
 class VendorCreate(BaseModel):
     name: VendorName
     category: VendorCategory
-    contact_email: EmailStr
+    contact_email: str
 
 
 class VendorUpdate(BaseModel):
```

**Comment:**

> backend/src/vendor_intake/schemas.py:58 (issue, blocking)
> VendorCreate.contact_email no longer validates email format
>
> Changing EmailStr to plain str removes format validation when a vendor is created. Any string is now accepted, including an empty one, and gets stored as the contact email. VendorUpdate still uses EmailStr, so create and update now disagree: a value accepted at creation would be rejected by an update. The LoginRequest comment explains why login uses a plain string, but that reasoning does not apply to vendor creation, and the PR gives no justification.

Label: no

## item-fbe16d6a89

**Defect:** a name used by another tenant is rejected with 409

```diff
--- a/backend/src/vendor_intake/services/review.py
+++ b/backend/src/vendor_intake/services/review.py
@@ -142,7 +142,7 @@
         ext.status == ExtractionStatus.APPROVED for _, ext in latest_extractions(session, vendor)
     ):
         vendor.status = VendorStatus.APPROVED
-    if vendor.status != VendorStatus.IN_REVIEW:
+    if vendor.status == VendorStatus.REJECTED:
         audit.record(
             tenant_id=user.tenant_id,
             vendor_id=vendor.id,
```

**Comment:**

> backend/src/vendor_intake/services/review.py:145 (issue, blocking)
> Vendor approval no longer writes a `vendor.approved` audit record
>
> Before this change, the vendor-level audit entry was written whenever the vendor left IN_REVIEW, which covers both APPROVED and REJECTED. The new condition only matches REJECTED. When the final extraction approval moves a vendor to APPROVED, that status change now has no audit trail. That is a compliance and audit regression, and the PR doesn't explain it.

Label: no

## item-2572facf12

**Defect:** an invalid contact email is accepted

```diff
--- a/backend/src/vendor_intake/schemas.py
+++ b/backend/src/vendor_intake/schemas.py
@@ -55,7 +55,7 @@
 class VendorCreate(BaseModel):
     name: VendorName
     category: VendorCategory
-    contact_email: EmailStr
+    contact_email: str
 
 
 class VendorUpdate(BaseModel):
```

**Comment:**

> backend/src/vendor_intake/schemas.py:58 (issue, blocking)
> VendorCreate no longer validates contact_email format while VendorUpdate still does
>
> Changing the create schema's contact_email from EmailStr to str removes server-side email validation on vendor creation. Malformed addresses will now be accepted and stored, and the PR title gives no reason for the change. The inconsistency with VendorUpdate, which still uses EmailStr, means create and update now enforce different rules. The LoginRequest comment explains a deliberate exception for login, but no equivalent justification exists here.

Label: yes

## item-e7d9de5910

**Defect:** documents can be added to a vendor in review

```diff
--- a/backend/src/vendor_intake/schemas.py
+++ b/backend/src/vendor_intake/schemas.py
@@ -55,7 +55,7 @@
 class VendorCreate(BaseModel):
     name: VendorName
     category: VendorCategory
-    contact_email: EmailStr
+    contact_email: str
 
 
 class VendorUpdate(BaseModel):
```

**Comment:**

> backend/src/vendor_intake/schemas.py:58 (issue, blocking)
> VendorCreate no longer validates contact_email, while VendorUpdate still does
>
> Changing VendorCreate.contact_email from EmailStr to str removes format validation on vendor creation. Malformed addresses will now be accepted and persisted, and the API contract silently changes (no longer 422 on bad email). The PR title 'Update schemas' does not describe this behavioral change, and the LoginRequest comment shows the codebase deliberately uses str only for login, not for vendor data. Also creates an inconsistency with VendorUpdate, which still enforces EmailStr.

Label: yes

## item-fa15557017

**Defect:** limit=101 is accepted

```diff
--- a/backend/src/vendor_intake/services/vendors.py
+++ b/backend/src/vendor_intake/services/vendors.py
@@ -59,7 +59,7 @@
         changes["contact_email"] = str(changes["contact_email"]).lower()
     for field, value in changes.items():
         setattr(vendor, field, value)
-    if changes:
+    if not changes:
         AuditRepository(session).record(
             tenant_id=user.tenant_id,
             vendor_id=vendor.id,
```

**Comment:**

> backend/src/vendor_intake/services/vendors.py:62 (issue, blocking)
> Audit condition inverted: real updates are no longer audited, and no-op updates record an empty audit entry
>
> With `if not changes`, any update that actually modifies fields skips the audit log entirely. Updates with no changes record a bogus `vendor.updated` event with `fields: []`. This breaks the audit trail for vendor edits.

Label: no

## item-ad264a6678

**Defect:** a name used by another tenant is rejected with 409

```diff
--- a/backend/src/vendor_intake/services/documents.py
+++ b/backend/src/vendor_intake/services/documents.py
@@ -41,7 +41,7 @@
     if expected is None or declared not in (expected, GENERIC_TYPE):
         allowed = ", ".join(sorted(CONTENT_TYPES))
         raise AppError(415, "unsupported_type", f"Upload a file with one of: {allowed}")
-    if size == 0:
+    if size < 0:
         raise AppError(422, "empty_file", "The uploaded file is empty")
     return expected
```

**Comment:**

> backend/src/vendor_intake/services/documents.py:44 (issue, blocking)
> Empty-file check changed from `size == 0` to `size < 0`, so empty uploads are no longer rejected.
>
> A size can never be negative for an uploaded file, so this guard is effectively dead. Zero-byte files now pass validation and get stored and sent to extraction, which the 'empty_file' error was meant to prevent. The PR title does not describe this behavior change, and no test covers it.

Label: no

## item-ee75061a58

**Defect:** failed sign-in answers 403 instead of 401

```diff
--- a/backend/src/vendor_intake/repositories/vendors.py
+++ b/backend/src/vendor_intake/repositories/vendors.py
@@ -19,7 +19,7 @@
 
     def name_taken(self, tenant_id: str, name: str, exclude_id: str | None = None) -> bool:
         query = select(Vendor.id).where(
-            Vendor.tenant_id == tenant_id, Vendor.name_key == name.lower()
+            Vendor.name_key == name.lower()
         )
         if exclude_id:
             query = query.where(Vendor.id != exclude_id)
```

**Comment:**

> backend/src/vendor_intake/repositories/vendors.py:22 (issue, blocking)
> name_taken drops the tenant filter, so uniqueness is checked across all tenants
>
> The tenant_id condition was removed from the query while the method still accepts tenant_id. Duplicate detection now sees vendors from other tenants: a tenant can be told a name is taken by another tenant's vendor (cross-tenant information leak), and legitimate same-name vendors in different tenants are rejected. This breaks tenant isolation.

Label: no

## item-30f564a60c

**Defect:** editing a vendor records no vendor.updated event

```diff
--- a/backend/src/vendor_intake/services/vendors.py
+++ b/backend/src/vendor_intake/services/vendors.py
@@ -59,7 +59,7 @@
         changes["contact_email"] = str(changes["contact_email"]).lower()
     for field, value in changes.items():
         setattr(vendor, field, value)
-    if changes:
+    if not changes:
         AuditRepository(session).record(
             tenant_id=user.tenant_id,
             vendor_id=vendor.id,
```

**Comment:**

> backend/src/vendor_intake/services/vendors.py:62 (issue, blocking)
> Audit record for vendor updates is now written only when no fields changed
>
> The condition was inverted. The vendor.updated audit entry should be recorded when a update actually changed fields. With `not changes`, real updates produce no audit trail, and a no-op update logs an entry with an empty fields list. This is a compliance-relevant regression.

Label: yes

## item-862168c446

**Defect:** audit events are listed newest first

```diff
--- a/backend/src/vendor_intake/routes/vendors.py
+++ b/backend/src/vendor_intake/routes/vendors.py
@@ -42,7 +42,7 @@
     )
 
 
-@router.post("", status_code=201)
+@router.post("", status_code=200)
 def create_vendor(
     body: VendorCreate,
     user: User = Depends(current_user),
```

**Comment:**

> backend/src/vendor_intake/routes/vendors.py:45 (issue, blocking)
> Create endpoint status code changed from 201 to 200, a breaking API contract change not described by the PR title.
>
> POST /vendors now returns 200 instead of 201 Created. Clients and API docs relying on 201 will break, and the PR title 'Update vendors' does not disclose this behavioral change. Revert unless intentional and communicated.

Label: no

## item-11bf2cb1a3

**Defect:** uploads are recorded as document.added instead of document.uploaded

```diff
--- a/backend/src/vendor_intake/services/documents.py
+++ b/backend/src/vendor_intake/services/documents.py
@@ -75,7 +75,7 @@
         tenant_id=user.tenant_id,
         vendor_id=vendor.id,
         actor_id=user.id,
-        action="document.uploaded",
+        action="document.added",
         details={"document_id": document_id, "kind": kind, "filename": document.filename},
     )
     session.commit()
```

**Comment:**

> backend/src/vendor_intake/services/documents.py:78 (issue, blocking)
> Audit action string changed from "document.uploaded" to "document.added" without migration or consumer update
>
> Audit action names are persisted and likely queried or filtered by downstream consumers, reports, or existing rows. Renaming the action silently splits historical records ("document.uploaded") from new ones ("document.added") and can break any filter or compliance report keyed on the old name. The PR title "Update documents" does not disclose this behavioral change to the audit trail.

Label: yes

## item-87361c3e50

**Defect:** a vendor in review can be edited

```diff
--- a/backend/src/vendor_intake/services/vendors.py
+++ b/backend/src/vendor_intake/services/vendors.py
@@ -50,7 +50,6 @@
 
 
 def update_vendor(session: Session, user: User, vendor: Vendor, data: VendorUpdate) -> Vendor:
-    ensure_editable(vendor)
     changes = data.model_dump(exclude_unset=True, exclude_none=True)
     if "name" in changes:
         _ensure_name_free(session, user.tenant_id, changes["name"], exclude=vendor.id)
```

**Comment:**

> backend/src/vendor_intake/services/vendors.py:53 (issue, blocking)
> update_vendor no longer checks that the vendor is editable, so locked vendors can be changed
>
> The removed ensure_editable(vendor) call was the only status guard on updates. Without it, a vendor in a non-editable status (anything outside DRAFT and REJECTED) can have its name, contact email, and other fields changed, and the change is committed. This bypasses the vendor_locked 409 rule and is likely a regression, since the PR title does not mention removing it.

Label: yes

## item-cef04c4541

**Defect:** the session cookie is readable from JavaScript

```diff
--- a/backend/src/vendor_intake/schemas.py
+++ b/backend/src/vendor_intake/schemas.py
@@ -113,7 +113,7 @@
 class ReviewDecision(BaseModel):
     decision: Literal["approve", "reject"]
     # validate_default: an omitted note must still trip the reject rule below.
-    note: str | None = Field(default=None, max_length=1000, validate_default=True)
+    note: str | None = Field(default=None, max_length=1000, validate_default=False)
 
     @field_validator("note")
     @classmethod
```

**Comment:**

> backend/src/vendor_intake/schemas.py:116 (issue, blocking)
> Setting validate_default=False lets a reject decision without a note pass validation
>
> With validate_default=False, Pydantic skips the field validator when note is omitted, so _reject_needs_note never runs for the default None. A request like {"decision": "reject"} with no note is accepted, which breaks the rule the adjacent comment describes. The comment now also contradicts the code.

Label: no

## item-c19fdcedd8

**Defect:** an unknown email gets a different error code than a wrong password

```diff
--- a/backend/src/vendor_intake/services/documents.py
+++ b/backend/src/vendor_intake/services/documents.py
@@ -34,7 +34,7 @@
 
 def validate_upload(filename: str, declared_type: str | None, size: int, max_bytes: int) -> str:
     """Returns the canonical content type, inferred from the extension."""
-    if size > max_bytes:
+    if size > max_bytes + 1:
         raise AppError(413, "file_too_large", f"Files must be {max_bytes} bytes or smaller")
     expected = CONTENT_TYPES.get(Path(filename).suffix.lower())
     declared = (declared_type or GENERIC_TYPE).split(";")[0].strip().lower()
```

**Comment:**

> backend/src/vendor_intake/services/documents.py:37 (issue, blocking)
> Off-by-one lets files one byte over the limit through
>
> With `max_bytes + 1`, a file of exactly max_bytes+1 bytes passes validation. That contradicts the error message, which says files must be max_bytes or smaller. The original check was correct.

Label: no

## item-7da0796000

**Defect:** submitting records vendor.submit instead of vendor.submitted

```diff
--- a/backend/src/vendor_intake/services/review.py
+++ b/backend/src/vendor_intake/services/review.py
@@ -64,7 +64,7 @@
     vendor.status = VendorStatus.IN_REVIEW
     vendor.submitted_at = utcnow()
     AuditRepository(session).record(
-        tenant_id=user.tenant_id, vendor_id=vendor.id, actor_id=user.id, action="vendor.submitted"
+        tenant_id=user.tenant_id, vendor_id=vendor.id, actor_id=user.id, action="vendor.submit"
     )
     session.commit()
     return vendor
```

**Comment:**

> backend/src/vendor_intake/services/review.py:67 (issue, blocking)
> Audit action renamed from `vendor.submitted` to `vendor.submit`, breaking the past-tense convention and existing consumers
>
> Every other audit action in this module uses past-tense status values: `extraction.approved`/`extraction.rejected` and `vendor.approved`/`vendor.rejected`. `vendor.submit` is the only imperative one.
>
> Renaming the string also splits the audit history. Rows already stored as `vendor.submitted` will no longer match anything that filters on the new value. Any audit-log UI, report, or test keyed on `vendor.submitted` silently stops finding new submissions.
>
> The PR gives no reason or migration for the rename.

Label: yes

## item-dc47e017d2

**Defect:** another tenant's document is readable

```diff
--- a/backend/src/vendor_intake/services/documents.py
+++ b/backend/src/vendor_intake/services/documents.py
@@ -75,7 +75,7 @@
         tenant_id=user.tenant_id,
         vendor_id=vendor.id,
         actor_id=user.id,
-        action="document.uploaded",
+        action="document.added",
         details={"document_id": document_id, "kind": kind, "filename": document.filename},
     )
     session.commit()
```

**Comment:**

> backend/src/vendor_intake/services/documents.py:78 (issue)
> Renaming the audit action splits the audit history and may break consumers
>
> Audit rows already stored with `document.uploaded` keep that name, while new uploads will be recorded as `document.added`. Anything that filters, labels, or tests on `document.uploaded` will silently stop matching new uploads. That includes audit log UIs, reports, test assertions and exports. The PR has no migration for existing rows and no matching consumer updates. The new name also breaks the past-tense convention the other events use (`document.extracted`, `document.failed`). Keep the original name, or rename it together with a data migration and consumer updates.

Label: no

## item-800318a64b

**Defect:** each page starts one row late, so the last page is short

```diff
--- a/backend/src/vendor_intake/repositories/vendors.py
+++ b/backend/src/vendor_intake/repositories/vendors.py
@@ -41,7 +41,7 @@
             query = query.where(Vendor.status == status)
         total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
         rows = self.session.scalars(
-            query.order_by(Vendor.created_at.desc(), Vendor.id).limit(limit).offset(offset)
+            query.order_by(Vendor.created_at.desc(), Vendor.id).limit(limit).offset(offset + 1)
         )
         return list(rows), total
```

**Comment:**

> backend/src/vendor_intake/repositories/vendors.py:44 (issue, blocking)
> Pagination offset shifted by one, skipping the first row of every page
>
> Adding +1 to the offset makes each page skip the first matching record and misplaces all subsequent pages. The PR title 'Update vendors' does not describe this behavior change, and no justification is given for it.

Label: yes

## item-c3a1c31457

**Defect:** documents can be added to a vendor in review

```diff
--- a/backend/src/vendor_intake/services/documents.py
+++ b/backend/src/vendor_intake/services/documents.py
@@ -57,7 +57,6 @@
     content_type: str,
     data: bytes,
 ) -> Document:
-    ensure_editable(vendor)
     document_id = new_id()
     document = Document(
         id=document_id,
```

**Comment:**

> backend/src/vendor_intake/services/documents.py:60 (issue, blocking)
> upload_document no longer checks that the vendor is editable
>
> The `ensure_editable(vendor)` guard was removed. Documents can now be uploaded to vendors that are locked, for example ones already submitted or approved. That bypasses the workflow state rule, and the uploaded file is written to storage and recorded in the audit log. `ensure_editable` is still imported, so this looks unintentional. The PR description gives no reason for the change and no test was updated.

Label: yes

## item-862512c8c3

**Defect:** the profile shows the tenant id where the tenant name belongs

```diff
--- a/backend/src/vendor_intake/routes/auth.py
+++ b/backend/src/vendor_intake/routes/auth.py
@@ -19,7 +19,7 @@
         email=user.email,
         name=user.name,
         role=user.role,
-        tenant=TenantOut.model_validate(tenant),
+        tenant=TenantOut(id=tenant.id, name=tenant.id),
     )
```

**Comment:**

> backend/src/vendor_intake/routes/auth.py:22 (issue, blocking)
> Tenant name is populated from the tenant id instead of the tenant's name
>
> The change replaces the model_validate call with a constructor that sets name=tenant.id, so the API returns the tenant's internal id as its display name. This silently changes the /auth/login and /me response payloads for every user. The original code presumably mapped the ORM tenant's name field; the fix should read tenant.name, and the unused model_validate path should be restored or the attribute corrected.

Label: yes

## item-c8e2e87c4c

**Defect:** approving the last extraction records no vendor.approved event

```diff
--- a/backend/src/vendor_intake/routes/review.py
+++ b/backend/src/vendor_intake/routes/review.py
@@ -9,7 +9,7 @@
 from vendor_intake.services import review
 
 router = APIRouter(tags=["review"])
-reviewer = require_roles(Role.REVIEWER, Role.ADMIN)
+reviewer = require_roles(Role.MEMBER, Role.REVIEWER, Role.ADMIN)
 
 
 @router.get("/review-queue")
```

**Comment:**

> backend/src/vendor_intake/routes/review.py:12 (issue, blocking)
> Ordinary members can now approve or reject extractions in the review queue.
>
> The `reviewer` dependency guards both the review queue and the decision endpoint. Adding `Role.MEMBER` lets every member of a tenant approve or reject extractions, which bypasses the reviewer/admin separation of duties. Nothing in the PR title or description explains or justifies this privilege escalation.

Label: no

## item-b20cd7dab2

**Defect:** insurance extractions have no coverage_amount field

```diff
--- a/backend/src/vendor_intake/services/review.py
+++ b/backend/src/vendor_intake/services/review.py
@@ -89,7 +89,7 @@
                 extractions=[extraction_out(ext, DocumentKind(doc.kind)) for doc, ext in pending],
             )
         )
-    return sorted(items, key=lambda i: (i.lowest_confidence, i.submitted_at or utcnow()))
+    return sorted(items, key=lambda i: (i.lowest_confidence, i.submitted_at or utcnow()), reverse=True)
 
 
 def _check_insurance_approvable(extraction: Extraction, today: date) -> None:
```

**Comment:**

> backend/src/vendor_intake/services/review.py:92 (issue, blocking)
> Adding reverse=True inverts the review queue ordering, putting highest-confidence items first
>
> The queue is meant to surface the lowest-confidence extractions first for review. With reverse=True the sort order flips for both keys, so the least certain items are now at the end of the queue. The title 'Update review' does not explain this behavior change, and no test covers the ordering.

Label: no

## item-bb981271aa

**Defect:** POST /api/vendors answers 200 instead of 201

```diff
--- a/backend/src/vendor_intake/routes/vendors.py
+++ b/backend/src/vendor_intake/routes/vendors.py
@@ -42,7 +42,7 @@
     )
 
 
-@router.post("", status_code=201)
+@router.post("", status_code=200)
 def create_vendor(
     body: VendorCreate,
     user: User = Depends(current_user),
```

**Comment:**

> backend/src/vendor_intake/routes/vendors.py:45 (issue, blocking)
> POST /vendors status code changed from 201 to 200 without justification
>
> Creating a resource now returns 200 instead of 201 Created. This is a breaking API contract change for clients that check for 201, and it is inconsistent with REST conventions. The PR title 'Update vendors' does not mention it.

Label: yes

## item-ca68bac71a

**Defect:** POST /api/vendors answers 200 instead of 201

```diff
--- a/backend/src/vendor_intake/routes/vendors.py
+++ b/backend/src/vendor_intake/routes/vendors.py
@@ -42,7 +42,7 @@
     )
 
 
-@router.post("", status_code=201)
+@router.post("", status_code=200)
 def create_vendor(
     body: VendorCreate,
     user: User = Depends(current_user),
```

**Comment:**

> backend/src/vendor_intake/routes/vendors.py:45 (issue, blocking)
> Create endpoint status code changed from 201 to 200, a breaking API contract change not described by the PR title.
>
> POST /vendors now returns 200 instead of 201 Created. Clients and API docs relying on 201 will break, and the PR title 'Update vendors' does not disclose this behavioral change. Revert unless intentional and communicated.

Label: yes

## item-5009061766

**Defect:** the profile shows the tenant id where the tenant name belongs

```diff
--- a/backend/src/vendor_intake/repositories/vendors.py
+++ b/backend/src/vendor_intake/repositories/vendors.py
@@ -19,7 +19,7 @@
 
     def name_taken(self, tenant_id: str, name: str, exclude_id: str | None = None) -> bool:
         query = select(Vendor.id).where(
-            Vendor.tenant_id == tenant_id, Vendor.name_key == name.lower()
+            Vendor.name_key == name.lower()
         )
         if exclude_id:
             query = query.where(Vendor.id != exclude_id)
```

**Comment:**

> backend/src/vendor_intake/repositories/vendors.py:22 (issue, blocking)
> name_taken no longer scopes the uniqueness check to the tenant
>
> The tenant_id filter was removed from the query, so name_taken now checks for a name collision across all tenants. A vendor name that exists in another tenant will be reported as taken, leaking cross-tenant existence information and blocking legitimate creates in the current tenant. The tenant_id parameter is now unused. This breaks tenant isolation.

Label: no

## item-d846084d16

**Defect:** the most confident vendor is listed first

```diff
--- a/backend/src/vendor_intake/services/review.py
+++ b/backend/src/vendor_intake/services/review.py
@@ -89,7 +89,7 @@
                 extractions=[extraction_out(ext, DocumentKind(doc.kind)) for doc, ext in pending],
             )
         )
-    return sorted(items, key=lambda i: (i.lowest_confidence, i.submitted_at or utcnow()))
+    return sorted(items, key=lambda i: (i.lowest_confidence, i.submitted_at or utcnow()), reverse=True)
 
 
 def _check_insurance_approvable(extraction: Extraction, today: date) -> None:
```

**Comment:**

> backend/src/vendor_intake/services/review.py:92 (issue, blocking)
> Review queue now puts highest-confidence, newest submissions first
>
> Adding `reverse=True` flips both parts of the sort key. Before this change, the queue showed the lowest-confidence extractions first, which are the ones that most need a human look, and broke ties by oldest submission (FIFO). Now the riskiest extractions sink to the bottom and older vendors wait behind newer ones. The `or utcnow()` fallback was meant to push vendors without a submission time to the end; reversed, it moves them to the top. The PR gives no rationale. If only one part of the order was meant to change, negate that component instead of reversing the whole tuple.

Label: yes

## item-98e95b31d7

**Defect:** the profile shows the tenant id where the tenant name belongs

```diff
--- a/backend/src/vendor_intake/routes/auth.py
+++ b/backend/src/vendor_intake/routes/auth.py
@@ -19,7 +19,7 @@
         email=user.email,
         name=user.name,
         role=user.role,
-        tenant=TenantOut.model_validate(tenant),
+        tenant=TenantOut(id=tenant.id, name=tenant.id),
     )
```

**Comment:**

> backend/src/vendor_intake/routes/auth.py:22 (issue, blocking)
> Tenant name is populated with the tenant id
>
> `TenantOut.name` is set from `tenant.id` instead of `tenant.name`. Every login and `/me` response will show the tenant id where its display name should be. This is a user-visible regression from the previous `model_validate`, which mapped `name` correctly.

Label: yes

## item-05477b932c

**Defect:** the most confident vendor is listed first

```diff
--- a/backend/src/vendor_intake/services/review.py
+++ b/backend/src/vendor_intake/services/review.py
@@ -89,7 +89,7 @@
                 extractions=[extraction_out(ext, DocumentKind(doc.kind)) for doc, ext in pending],
             )
         )
-    return sorted(items, key=lambda i: (i.lowest_confidence, i.submitted_at or utcnow()))
+    return sorted(items, key=lambda i: (i.lowest_confidence, i.submitted_at or utcnow()), reverse=True)
 
 
 def _check_insurance_approvable(extraction: Extraction, today: date) -> None:
```

**Comment:**

> backend/src/vendor_intake/services/review.py:92 (issue, blocking)
> Adding reverse=True inverts the review queue ordering, putting highest-confidence items first
>
> The queue is meant to surface the lowest-confidence extractions first for review. With reverse=True the sort order flips for both keys, so the least certain items are now at the end of the queue. The title 'Update review' does not explain this behavior change, and no test covers the ordering.

Label: yes

## item-c77ee8e1fe

**Defect:** another tenant's vendor (and its audit trail) is readable

```diff
--- a/backend/src/vendor_intake/repositories/vendors.py
+++ b/backend/src/vendor_intake/repositories/vendors.py
@@ -14,7 +14,7 @@
 
     def get(self, vendor_id: str, tenant_id: str) -> Vendor | None:
         return self.session.scalar(
-            select(Vendor).where(Vendor.id == vendor_id, Vendor.tenant_id == tenant_id)
+            select(Vendor).where(Vendor.id == vendor_id)
         )
 
     def name_taken(self, tenant_id: str, name: str, exclude_id: str | None = None) -> bool:
```

**Comment:**

> backend/src/vendor_intake/repositories/vendors.py:17 (issue, blocking)
> get() no longer filters by tenant_id, enabling cross-tenant vendor access
>
> Removing the tenant_id predicate means any caller can fetch a vendor belonging to another tenant by ID. This is a tenant isolation breach. The tenant_id parameter is now unused, which also signals the filter was dropped by mistake. Restore the tenant condition.

Label: yes

## item-6a84a57379

**Defect:** an insurance certificate that expired yesterday can be approved

```diff
--- a/backend/src/vendor_intake/services/review.py
+++ b/backend/src/vendor_intake/services/review.py
@@ -96,7 +96,7 @@
     expiry = field_date(extraction.fields, "expiry_date")
     if expiry is None:
         raise AppError(409, "insurance_expiry_missing", "Confirm the expiry date before approving")
-    if expiry < today:
+    if expiry > today:
         raise AppError(409, "insurance_expired", f"The insurance certificate expired on {expiry}")
```

**Comment:**

> backend/src/vendor_intake/services/review.py:99 (issue, blocking)
> Inverted expiry comparison rejects valid certificates and accepts expired ones
>
> The check raises insurance_expired when expiry is after today, so an unexpired certificate is blocked and an expired one passes approval. The original `expiry < today` was correct; this flip breaks the insurance approval gate entirely.

Label: yes

## item-500c5b7592

**Defect:** a name used by another tenant is rejected with 409

```diff
--- a/backend/src/vendor_intake/services/vendors.py
+++ b/backend/src/vendor_intake/services/vendors.py
@@ -50,7 +50,6 @@
 
 
 def update_vendor(session: Session, user: User, vendor: Vendor, data: VendorUpdate) -> Vendor:
-    ensure_editable(vendor)
     changes = data.model_dump(exclude_unset=True, exclude_none=True)
     if "name" in changes:
         _ensure_name_free(session, user.tenant_id, changes["name"], exclude=vendor.id)
```

**Comment:**

> backend/src/vendor_intake/services/vendors.py:52 (issue, blocking)
> Removed ensure_editable lets locked (submitted/approved) vendors be edited
>
> The `ensure_editable(vendor)` guard was the only check stopping updates to vendors that are not in DRAFT or REJECTED status. Without it, a vendor that has already been submitted or approved can have its name or contact email changed after review. That defeats the approval workflow. The PR gives no reason for the change and adds no replacement check, and `ensure_editable` and `EDITABLE_STATUSES` are still defined in this file.

Label: yes

## item-9c5dc58e7f

**Defect:** an insurance certificate that expired yesterday can be approved

```diff
--- a/backend/src/vendor_intake/routes/auth.py
+++ b/backend/src/vendor_intake/routes/auth.py
@@ -32,7 +32,7 @@
 ) -> LoginResponse:
     user = UserRepository(session).by_email(str(body.email))
     if not check_credentials(user, body.password) or user is None:
-        raise AppError(401, "invalid_credentials", "Email or password is incorrect")
+        raise AppError(403, "invalid_credentials", "Email or password is incorrect")
     token = issue_token(user, settings.jwt_secret, settings.jwt_ttl_minutes)
     response.set_cookie(
         SESSION_COOKIE,
```

**Comment:**

> backend/src/vendor_intake/routes/auth.py:35 (issue, blocking)
> Failed login now returns 403 instead of 401, which breaks the API contract.
>
> A bad email or password is an authentication failure, so 401 is the correct status. 403 means an authenticated caller lacks permission. Clients and frontend interceptors that treat 401 as "redirect to login / show bad credentials" will now misbehave, and any tests asserting 401 will break. The PR gives no reason for the change.

Label: yes

## item-9908c5a94c

**Defect:** an invalid contact email is accepted

```diff
--- a/backend/src/vendor_intake/routes/auth.py
+++ b/backend/src/vendor_intake/routes/auth.py
@@ -32,7 +32,7 @@
 ) -> LoginResponse:
     user = UserRepository(session).by_email(str(body.email))
     if not check_credentials(user, body.password) or user is None:
-        raise AppError(401, "invalid_credentials", "Email or password is incorrect")
+        raise AppError(403, "invalid_credentials", "Email or password is incorrect")
     token = issue_token(user, settings.jwt_secret, settings.jwt_ttl_minutes)
     response.set_cookie(
         SESSION_COOKIE,
```

**Comment:**

> backend/src/vendor_intake/routes/auth.py:35 (issue, blocking)
> Failed login now returns 403 instead of 401, which breaks the API contract
>
> Wrong credentials are an authentication failure, and the correct status for that is 401. A 403 means the caller is authenticated but not allowed. Clients and frontend interceptors that check for 401 to show the login form or clear the session will stop working. Any tests that expect 401 on a bad login will also fail. Nothing in the PR explains why the change was made.

Label: no

## item-4b0a0b6a24

**Defect:** members can open the review queue

```diff
--- a/backend/src/vendor_intake/routes/auth.py
+++ b/backend/src/vendor_intake/routes/auth.py
@@ -19,7 +19,7 @@
         email=user.email,
         name=user.name,
         role=user.role,
-        tenant=TenantOut.model_validate(tenant),
+        tenant=TenantOut(id=tenant.id, name=tenant.id),
     )
```

**Comment:**

> backend/src/vendor_intake/routes/auth.py:22 (issue, blocking)
> Tenant name is populated from the tenant id, and the schema-based mapping was dropped
>
> The change replaces TenantOut.model_validate(tenant) with a hand-built TenantOut that sets name=tenant.id. Any tenant name is discarded and the id is returned in its place, so /me and login responses report incorrect tenant data. The PR title ('Update auth') does not describe this behavior change, and no test covers it.

Label: no

## item-f3f5199478

**Defect:** an empty upload is accepted

```diff
--- a/backend/src/vendor_intake/services/vendors.py
+++ b/backend/src/vendor_intake/services/vendors.py
@@ -36,7 +36,7 @@
     vendor = Vendor(
         tenant_id=user.tenant_id,
         name=data.name,
-        name_key=data.name.lower(),
+        name_key=data.name,
         category=data.category,
         contact_email=str(data.contact_email).lower(),
         created_by=user.id,
```

**Comment:**

> backend/src/vendor_intake/services/vendors.py:39 (issue, blocking)
> create_vendor stores name_key without lowercasing, unlike update_vendor
>
> update_vendor sets vendor.name_key = changes["name"].lower(), so name_key is meant to be a case-insensitive key. This change makes create store the mixed-case name. Vendors created here will not match a lowercase lookup, so the duplicate-name check can miss them and allow case-variant duplicates. Existing rows created before this change are also inconsistent with new ones.

Label: no

## item-f58dda0342

**Defect:** a name used by another tenant is rejected with 409

```diff
--- a/backend/src/vendor_intake/repositories/vendors.py
+++ b/backend/src/vendor_intake/repositories/vendors.py
@@ -41,7 +41,7 @@
             query = query.where(Vendor.status == status)
         total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
         rows = self.session.scalars(
-            query.order_by(Vendor.created_at.desc(), Vendor.id).limit(limit).offset(offset)
+            query.order_by(Vendor.created_at.desc(), Vendor.id).limit(limit).offset(offset + 1)
         )
         return list(rows), total
```

**Comment:**

> backend/src/vendor_intake/repositories/vendors.py:44 (issue, blocking)
> Off-by-one offset skips the first vendor on every page
>
> Using `offset + 1` drops one row per page. The first page never returns the newest vendor, and each page boundary loses a row. The caller-supplied offset already gives the correct start index, so adding 1 corrupts pagination, and `total` will disagree with the rows that can actually be reached.

Label: no

## item-1de343c109

**Defect:** failed sign-in answers 403 instead of 401

```diff
--- a/backend/src/vendor_intake/services/vendors.py
+++ b/backend/src/vendor_intake/services/vendors.py
@@ -50,7 +50,6 @@
 
 
 def update_vendor(session: Session, user: User, vendor: Vendor, data: VendorUpdate) -> Vendor:
-    ensure_editable(vendor)
     changes = data.model_dump(exclude_unset=True, exclude_none=True)
     if "name" in changes:
         _ensure_name_free(session, user.tenant_id, changes["name"], exclude=vendor.id)
```

**Comment:**

> backend/src/vendor_intake/services/vendors.py:52 (issue, blocking)
> update_vendor no longer enforces the editable-status lock, so submitted/approved vendors can be modified
>
> Removing ensure_editable(vendor) lets callers change the name, contact email, category, and other fields of vendors that are already submitted or approved. Only DRAFT and REJECTED vendors are supposed to be editable, which is why EDITABLE_STATUSES and ensure_editable still exist. As written, an approved vendor's details could be changed after review with no re-approval. The PR doesn't say why the guard was dropped, and nothing replaces it.

Label: no

## item-b685fcb061

**Defect:** approving the last extraction records no vendor.approved event

```diff
--- a/backend/src/vendor_intake/routes/vendors.py
+++ b/backend/src/vendor_intake/routes/vendors.py
@@ -42,7 +42,7 @@
     )
 
 
-@router.post("", status_code=201)
+@router.post("", status_code=200)
 def create_vendor(
     body: VendorCreate,
     user: User = Depends(current_user),
```

**Comment:**

> backend/src/vendor_intake/routes/vendors.py:45 (issue, blocking)
> Create endpoint status code changed from 201 to 200, breaking the API contract
>
> POST /vendors creates a resource, so 201 Created is the correct response. Changing it to 200 is a breaking change for clients and tests that assert on 201 (e.g., frontend success checks or integration tests). The PR gives no justification for the change.

Label: no

## item-725d707863

**Defect:** audit events are listed newest first

```diff
--- a/backend/src/vendor_intake/services/documents.py
+++ b/backend/src/vendor_intake/services/documents.py
@@ -34,7 +34,7 @@
 
 def validate_upload(filename: str, declared_type: str | None, size: int, max_bytes: int) -> str:
     """Returns the canonical content type, inferred from the extension."""
-    if size > max_bytes:
+    if size >= max_bytes:
         raise AppError(413, "file_too_large", f"Files must be {max_bytes} bytes or smaller")
     expected = CONTENT_TYPES.get(Path(filename).suffix.lower())
     declared = (declared_type or GENERIC_TYPE).split(";")[0].strip().lower()
```

**Comment:**

> backend/src/vendor_intake/services/documents.py:37 (issue, blocking)
> Off-by-one: files of exactly max_bytes are now rejected, contradicting the error message
>
> The error message says files must be `max_bytes` bytes or smaller. With `>=`, a file of exactly `max_bytes` is rejected even though it satisfies that limit. This is an unexplained behavior regression at the boundary, and no test covers it.

Label: no

## item-f071877485

**Defect:** submitting records vendor.submit instead of vendor.submitted

```diff
--- a/backend/src/vendor_intake/services/review.py
+++ b/backend/src/vendor_intake/services/review.py
@@ -64,7 +64,7 @@
     vendor.status = VendorStatus.IN_REVIEW
     vendor.submitted_at = utcnow()
     AuditRepository(session).record(
-        tenant_id=user.tenant_id, vendor_id=vendor.id, actor_id=user.id, action="vendor.submitted"
+        tenant_id=user.tenant_id, vendor_id=vendor.id, actor_id=user.id, action="vendor.submit"
     )
     session.commit()
     return vendor
```

**Comment:**

> backend/src/vendor_intake/services/review.py:67 (issue, blocking)
> Audit action renamed from "vendor.submitted" to "vendor.submit", breaking existing audit history and past-tense convention
>
> Existing audit rows store "vendor.submitted", so any query or report filtering on that action will miss all prior submissions. The other vendor audit actions are past tense (f"vendor.{vendor.status}" yields "vendor.approved"/"vendor.rejected"), so "vendor.submit" also breaks that naming pattern. Nothing in this diff explains the rename, and the title "Update review" doesn't either.

Label: yes
