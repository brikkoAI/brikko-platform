/* eslint-disable */
// AUTO-GENERATED. Не редактируй вручную.
// См. scripts/generate-types.ts (TD-041).

export interface paths {
    "/v1/auth/csrf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Mint a fresh CSRF double-submit token
         * @description Returns a 64-char URL-safe token in the JSON body and writes the matching ``vlt_csrf`` cookie. The SPA echoes the token via the ``X-CSRF-Token`` header on every mutating cookie-auth request; the server compares it to the cookie value (constant time).
         */
        get: operations["csrf_bootstrap_v1_auth_csrf_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/signup": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create user account
         * @description Creates a User + primary Account. Sends a verification email (plaintext token only in the email; hash stored in DB). The account cannot ``login`` until the email is verified. No cookies are issued here — ``/login`` is a separate explicit step.
         */
        post: operations["signup_v1_auth_signup_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/verify-email": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Verify email and credit welcome bonus
         * @description Verifies the user's email using the token sent by ``/signup``. On success the welcome 200 ₽ credit is granted **once** per email (anti-abuse: ``welcome_credits_log`` PK is the email hash, not the user_id, so delete-and-resignup with the same email cannot re-grant).
         */
        post: operations["verify_email_v1_auth_verify_email_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Authenticate and issue session cookies
         * @description Verifies email/password and issues ``vlt_access`` (15 min) + ``vlt_refresh`` (30 days) HTTP-only cookies, plus a fresh ``vlt_csrf`` cookie + ``csrf_token`` in the body. Subsequent SPA calls authenticate via these cookies.
         */
        post: operations["login_v1_auth_login_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Revoke session cookies and refresh JTI
         * @description Clears all session cookies (``vlt_access``, ``vlt_refresh``, ``vlt_csrf``) and revokes the active refresh JTI in Redis so a stolen token cannot be reused.
         */
        post: operations["logout_v1_auth_logout_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/refresh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Rotate access + refresh tokens
         * @description Validates the current ``vlt_refresh`` cookie's JTI in Redis, revokes it, and issues a new pair (access + refresh). Returns a fresh ``csrf_token`` because the SPA's in-memory copy is rotated.
         */
        post: operations["refresh_v1_auth_refresh_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/forgot-password": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Request a password reset link
         * @description Always returns 200 to avoid email enumeration: success and wrong-email both look identical to a probing attacker. If the email maps to a real verified user, a single-use, time-limited reset token is emailed (HMAC-signed, hash-stored).
         */
        post: operations["forgot_password_v1_auth_forgot_password_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/reset-password": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Set a new password using a reset token
         * @description Consumes the single-use token issued by ``/forgot-password``. On success the user's password is replaced and **all** active refresh JTIs are revoked (any other-device sessions are killed).
         */
        post: operations["reset_password_v1_auth_reset_password_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/change-password": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Change password (requires current password)
         * @description Active session required. Verifies ``old_password`` then atomically swaps the hash and revokes all refresh JTIs (signs out other devices).
         */
        post: operations["change_password_v1_auth_change_password_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/account": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Account
         * @description Return the caller's profile + account snapshot.
         */
        get: operations["get_account_v1_account_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/account/profile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update Profile
         * @description Update human-friendly name and / or email.
         *
         *     Email change semantics:
         *     1. Lower-cased + collision-checked against the global ``users.email`` UNIQUE
         *        constraint. We do an explicit pre-check rather than relying on the DB
         *        error so we can return a clean ``email_taken`` body.
         *     2. ``email_verified=False`` until the new address is re-verified.
         *     3. Old verification token (if any) is wiped — new one is in-flight.
         *     4. New verification email dispatched best-effort.
         *     5. **All active refresh tokens are revoked** — the old email is the login
         *        channel. If it was compromised (or simply no longer accessible), the
         *        attacker who could read the inbox should not keep the SPA session on
         *        another device. Force re-login on every device, including this one
         *        (so cookies are also cleared on the response).
         */
        patch: operations["update_profile_v1_account_profile_patch"];
        trace?: never;
    };
    "/v1/account/settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update Settings
         * @description Update ``prompt_logging_enabled`` and / or the ``notifications`` blob.
         */
        patch: operations["update_settings_v1_account_settings_patch"];
        trace?: never;
    };
    "/v1/account/seats": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Seats
         * @description List every seat in the active account, including the owner.
         *
         *     The owner doesn't necessarily have an explicit Seat row — we synthesise
         *     one from ``Account.owner_id`` so the response always reflects the full
         *     membership of the workspace. Sorted: owner first, then by joined date.
         */
        get: operations["list_seats_v1_account_seats_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/account/seats/invite": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create Invite
         * @description Send an invite email and create a pending ``EmailInvite`` row.
         *
         *     Idempotency: if an unaccepted invite for the same (account, email) pair
         *     already exists and isn't expired, we re-use it (and re-mint the token so
         *     the user always gets a working link). This is what the SPA expects when
         *     the user hits "Invite" twice in a row.
         */
        post: operations["create_invite_v1_account_seats_invite_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/account/seats/accept": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Accept Invite
         * @description Accept a pending invite. Two flows:
         *
         *     A) **User exists** (logged in or not — doesn't matter): create a Seat in
         *        the inviter's account, mark the invite consumed. No cookies are
         *        issued — the caller goes through /login on their own.
         *
         *     B) **User is new**: create User + primary Account, atomically grant the
         *        200 ₽ welcome bonus, create a Seat in the inviter's account, then
         *        autologin (Set-Cookie). The inviter does NOT pay for the bonus — it
         *        comes out of the same marketing budget as /signup.
         *
         *     The endpoint is anonymous so it cannot use ``require_session`` — instead
         *     we rate-limit by client IP to prevent token-guessing brute force, and
         *     every invalid token returns a flat 400 with the same body so an
         *     attacker can't tell "expired" from "wrong" (timing is the only side
         *     channel; the work is symmetric).
         */
        post: operations["accept_invite_v1_account_seats_accept_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/account/seats/{user_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * Remove Seat
         * @description Remove a member from the active account.
         *
         *     Permission rules (see module docstring for the full matrix):
         *
         *     * Owner can remove any seat *except* themselves (use the V2 transfer-of-
         *       ownership endpoint to hand off + leave).
         *     * Admin can remove a member (not another admin, not the owner).
         *     * Member can never remove anyone.
         *
         *     Removal is a **physical delete** of the Seat row — no soft-delete column
         *     on this table. The user keeps their own primary account untouched; we're
         *     only cutting their access to *this* workspace. They can be re-invited
         *     later without DB clutter from a dangling row.
         */
        delete: operations["remove_seat_v1_account_seats__user_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/account/invites": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Invites
         * @description Pending invites (not accepted, not expired). Owner & admin only.
         */
        get: operations["list_invites_v1_account_invites_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/account/invites/{invite_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * Revoke Invite
         * @description Hard-delete a pending invite. Owner & admin only.
         *
         *     A hard delete is fine because EmailInvite rows have no downstream FKs
         *     (they're consumed at /accept which marks ``accepted_at``). Re-inviting
         *     after revoke is a clean fresh row, no zombie state.
         */
        delete: operations["revoke_invite_v1_account_invites__invite_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/keys": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List API keys for the active account
         * @description Returns active and revoked keys (sorted by creation time, newest first). Plaintext is **never** returned here — only ``prefix`` (first 14 chars). Frontend filters revoked client-side.
         */
        get: operations["list_keys_v1_keys_get"];
        put?: never;
        /**
         * Create a new API key
         * @description Generates a fresh ``sk-vlt-...`` key. The plaintext is shown **only once** in the response — it's not recoverable later. Optional ``expires_in_days`` (30/90/180/365) sets a hard expiry; omit for the legacy never-expires behaviour.
         */
        post: operations["create_key_v1_keys_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/keys/{key_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * Revoke an API key (soft delete)
         * @description Sets ``status=REVOKED`` + ``revoked_at=now()``. Bearer auth Redis cache is purged immediately so the next chat call 401s in <1s. The row is NOT hard-deleted — it stays for audit/forensics.
         */
        delete: operations["revoke_key_v1_keys__key_id__delete"];
        options?: never;
        head?: never;
        /**
         * Rename an API key
         * @description Updates only the ``name``. ``scope`` is intentionally immutable — to change permissions, create a new key and revoke the old one.
         */
        patch: operations["update_key_v1_keys__key_id__patch"];
        trace?: never;
    };
    "/v1/chat/completions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create chat completion (OpenAI-compatible)
         * @description OpenAI-compatible chat completions endpoint. Supports both non-streaming (JSON response) and streaming (Server-Sent Events) modes via the ``stream`` flag.
         *
         *     **Auth**: Bearer ``sk-vlt-...`` (cookie-session not accepted on this endpoint by design — chat is M2M only).
         *
         *     **Routing**: pass ``model='auto:cheap'`` / ``'auto:smart'`` / ``'auto:fast'`` to delegate model choice to the smart router, or pin a specific model id from ``GET /v1/models``.
         *
         *     **Billing**: cost is computed from provider-reported tokens × catalogue pricing × 1.15 markup, debited atomically via a pre-flight hold + post-flight commit (TD-029).
         */
        post: operations["chat_completions_v1_chat_completions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List available models (OpenAI-compatible)
         * @description OpenAI-shape ``{object: 'list', data: [...]}`` with the Brikko extension ``pricing`` (kopecks per 1k tokens, input/cached/output). Standard OpenAI clients ignore unknown fields, so the endpoint is drop-in compatible with the Python/JS SDKs.
         */
        get: operations["list_models_endpoint_v1_models_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/models/{model_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get a single model by id
         * @description Returns the OpenAI-style model object for ``model_id`` (e.g. ``gpt-5.4-mini``). 404 if the model isn't in the catalog.
         */
        get: operations["get_model_endpoint_v1_models__model_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/usage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Aggregate usage and cost over a time window
         * @description Returns request count, token totals (input/output/cached), and billed cost for the active account.
         *
         *     **Date semantics** (TD-042): ``from`` is inclusive at 00:00:00 UTC, ``to`` is **inclusive end-of-day** at 23:59:59.999999 UTC for date-only inputs (``YYYY-MM-DD``). Full ISO timestamps are honoured exactly. Defaults: ``from`` = today 00:00 UTC, ``to`` = now.
         *
         *     **Field naming**: this response uses ``tokens_in``/``tokens_out``/``cost_kop`` to match the SPA contract in ``apps/web/src/lib/types.ts::UsageTotals``.
         */
        get: operations["usage_endpoint_v1_usage_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/billing/balance": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get current account balance and tariff
         * @description Returns the live balance from Postgres (no caching), in both kopecks (canonical) and rubles (display). Auth: Bearer **or** session cookie.
         */
        get: operations["get_balance_v1_billing_balance_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/billing/transactions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List recent transactions
         * @description Returns ledger rows (charges, top-ups, refunds, autorefills) sorted newest-first. Optional ``from``/``to`` ISO-datetime filters and ``limit`` (1..500, default 50). Date semantics: ``to`` is **inclusive** end-of-day at the day granularity (TD-042).
         */
        get: operations["list_transactions_v1_billing_transactions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/billing/receipts/{transaction_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Receipt */
        get: operations["get_receipt_v1_billing_receipts__transaction_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/billing/topup": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Initiate a balance top-up via ЮKassa
         * @description Creates a ЮKassa payment and returns a redirect URL the SPA opens in a new tab. Money is **not** credited here — credit happens in the ``/yookassa/webhook`` callback after the user completes payment. Idempotency: same caller request body within 5 min returns the cached payment to avoid double-charging.
         */
        post: operations["create_topup_v1_billing_topup_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/billing/yookassa/webhook": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Yookassa Webhook
         * @description Accept ЮKassa server notifications.
         *
         *     Returns 401 on bad signature, 400 on malformed body, 500 on
         *     recoverable processing failures (so ЮKassa retries), 200 on success
         *     or idempotent replay.
         *
         *     Idempotency is enforced through both:
         *     * ``transactions.ref_id`` UNIQUE — so credit/refund cannot run twice.
         *     * ``processed_webhooks.payment_id`` PK with status='processed' — fast
         *       short-circuit for replays without taking the account row lock.
         */
        post: operations["yookassa_webhook_v1_billing_yookassa_webhook_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/billing/autorefill": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Enable Autorefill */
        post: operations["enable_autorefill_v1_billing_autorefill_post"];
        /** Disable Autorefill */
        delete: operations["disable_autorefill_v1_billing_autorefill_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/account/telegram-link": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Issue a one-time TG-link token
         * @description Mints a 5-minute token the user pastes into `/link <token>` in @VoltariBot. The bot sets the user's `telegram_chat_id` once consumed. Tokens are single-use and stored in Redis with TTL — no DB row, no cleanup needed.
         */
        post: operations["issue_telegram_link_v1_account_telegram_link_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AcceptInviteRequest */
        AcceptInviteRequest: {
            /** Token */
            token: string;
        };
        /** AcceptInviteResponse */
        AcceptInviteResponse: {
            /** User Id */
            user_id: string;
            /** Account Id */
            account_id: string;
            /** Autologin */
            autologin: boolean;
        };
        /**
         * AccountResponse
         * @description Snapshot returned by GET / PATCH endpoints.
         *
         *     ``prompt_logging_enabled`` mirrors ``Account.store_prompts`` — the API uses
         *     the human-readable name, the DB the historical column. CEO 29.04 decided
         *     against a column rename (no migration cost, no broken tests).
         */
        AccountResponse: {
            /** User Id */
            user_id: string;
            /** Email */
            email: string;
            /** Account Id */
            account_id: string;
            /** Name */
            name: string;
            /** Tariff */
            tariff: string;
            /** Balance Kopecks */
            balance_kopecks: number;
            /** Prompt Logging Enabled */
            prompt_logging_enabled: boolean;
            /** Notifications */
            notifications: {
                [key: string]: unknown;
            };
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Email Verified */
            email_verified: boolean;
        };
        /** AutorefillRequest */
        AutorefillRequest: {
            /** Payment Method Id */
            payment_method_id: string;
            /** Threshold Kopecks */
            threshold_kopecks: number;
            /** Topup Kopecks */
            topup_kopecks: number;
        };
        /** BalanceResponse */
        BalanceResponse: {
            /**
             * Account Id
             * Format: uuid
             */
            account_id: string;
            /** Balance Kopecks */
            balance_kopecks: number;
            /** Balance Rub */
            balance_rub: number;
            /** Tariff */
            tariff: string;
        };
        /** ChangePasswordRequest */
        ChangePasswordRequest: {
            /** Old Password */
            old_password: string;
            /** New Password */
            new_password: string;
        };
        /** ChatCompletionRequestBody */
        ChatCompletionRequestBody: {
            /** Model */
            model: string;
            /** Messages */
            messages: components["schemas"]["ChatMessage"][];
            /** Temperature */
            temperature?: number | null;
            /** Top P */
            top_p?: number | null;
            /** Max Tokens */
            max_tokens?: number | null;
            /**
             * Stream
             * @default false
             */
            stream: boolean;
            /** Stop */
            stop?: string[] | string | null;
            /** Response Format */
            response_format?: {
                [key: string]: unknown;
            } | null;
            /** Tools */
            tools?: {
                [key: string]: unknown;
            }[] | null;
            /** Tool Choice */
            tool_choice?: {
                [key: string]: unknown;
            } | string | null;
            /** Seed */
            seed?: number | null;
            /** User */
            user?: string | null;
            /** Presence Penalty */
            presence_penalty?: number | null;
            /** Frequency Penalty */
            frequency_penalty?: number | null;
            /** Logprobs */
            logprobs?: boolean | null;
            /** Top Logprobs */
            top_logprobs?: number | null;
            /** Stream Options */
            stream_options?: {
                [key: string]: unknown;
            } | null;
            /** Reasoning Effort */
            reasoning_effort?: ("low" | "medium" | "high") | null;
            /**
             * Failover
             * @default true
             */
            failover: boolean;
            /** Exclude Providers */
            exclude_providers?: string[] | null;
            /** Pii Protect */
            pii_protect?: boolean | null;
        };
        /** ChatMessage */
        ChatMessage: {
            /**
             * Role
             * @enum {string}
             */
            role: "system" | "user" | "assistant" | "tool" | "developer";
            /** Content */
            content?: string | {
                [key: string]: unknown;
            }[] | null;
            /** Name */
            name?: string | null;
            /** Tool Call Id */
            tool_call_id?: string | null;
            /** Tool Calls */
            tool_calls?: {
                [key: string]: unknown;
            }[] | null;
        };
        /**
         * CreateInviteRequest
         * @description Body of POST /v1/account/seats/invite.
         *
         *     ``role`` is constrained to admin|member at the API layer. ``owner`` is
         *     rejected explicitly with 400 rather than silently 422'd by the regex
         *     pattern, so the SPA can show a clear error.
         */
        CreateInviteRequest: {
            /**
             * Email
             * Format: email
             */
            email: string;
            /** Role */
            role: string;
        };
        /** CreateInviteResponse */
        CreateInviteResponse: {
            /** Invite Id */
            invite_id: string;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
        };
        /**
         * CreateKeyRequest
         * @description Body of POST /v1/keys.
         *
         *     ``scope`` exposes the API-friendly literals ``read|write|all``. We map
         *     ``"all"`` onto ``ApiKeyScope.WRITE`` server-side because the underlying
         *     enum has only two states; "all" is reserved for V2 when admin / billing
         *     scopes are added.
         *
         *     TD-009: ``expires_in_days`` is an opt-in expiry preset (30, 90, 180,
         *     365). Omitting the field keeps the legacy "never expires" behaviour
         *     so existing SDK callers don't break.
         */
        CreateKeyRequest: {
            /** Name */
            name: string;
            /**
             * Scope
             * @default write
             */
            scope: string;
            /** Expires In Days */
            expires_in_days?: (30 | 90 | 180 | 365) | null;
        };
        /**
         * CreateKeyResponse
         * @description One-shot reveal of the plaintext. Never log this body.
         */
        CreateKeyResponse: {
            /** Id */
            id: string;
            /** Name */
            name: string;
            /** Full Key */
            full_key: string;
            /** Prefix */
            prefix: string;
            /** Scope */
            scope: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Expires At */
            expires_at?: string | null;
        };
        /** ForgotPasswordRequest */
        ForgotPasswordRequest: {
            /**
             * Email
             * Format: email
             */
            email: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** InviteItem */
        InviteItem: {
            /** Invite Id */
            invite_id: string;
            /** Email */
            email: string;
            /** Role */
            role: string;
            /**
             * Invited At
             * Format: date-time
             */
            invited_at: string;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
        };
        /**
         * KeyListItem
         * @description Compact key descriptor — never includes plaintext.
         */
        KeyListItem: {
            /** Id */
            id: string;
            /** Name */
            name: string;
            /** Prefix */
            prefix: string;
            /** Scope */
            scope: string;
            /** Status */
            status: string;
            /** Last Used At */
            last_used_at?: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Revoked At */
            revoked_at?: string | null;
            /** Expires At */
            expires_at?: string | null;
        };
        /** LoginRequest */
        LoginRequest: {
            /**
             * Email
             * Format: email
             */
            email: string;
            /** Password */
            password: string;
        };
        /** ReceiptResponse */
        ReceiptResponse: {
            /**
             * Transaction Id
             * Format: uuid
             */
            transaction_id: string;
            /** Receipt Id */
            receipt_id: string;
            /** Receipt Url */
            receipt_url: string;
            /**
             * Issuer
             * @enum {string}
             */
            issuer: "yookassa" | "lknpd";
        };
        /** ResetPasswordRequest */
        ResetPasswordRequest: {
            /** Token */
            token: string;
            /** New Password */
            new_password: string;
        };
        /** SeatItem */
        SeatItem: {
            /** User Id */
            user_id: string;
            /** Email */
            email: string;
            /** Role */
            role: string;
            /**
             * Joined At
             * Format: date-time
             */
            joined_at: string;
        };
        /** SignupRequest */
        SignupRequest: {
            /**
             * Email
             * Format: email
             */
            email: string;
            /** Password */
            password: string;
        };
        /** TelegramLinkResponse */
        TelegramLinkResponse: {
            /** Token */
            token: string;
            /** Ttl Seconds */
            ttl_seconds: number;
            /** Bot Username */
            bot_username: string;
            /** Deep Link */
            deep_link: string;
        };
        /** TopupRequest */
        TopupRequest: {
            /** Amount Rub */
            amount_rub: number;
            /** Return Url */
            return_url?: string | null;
            /**
             * Save Payment Method
             * @default false
             */
            save_payment_method: boolean;
            /** Receipt Email */
            receipt_email?: string | null;
            /** Receipt Phone */
            receipt_phone?: string | null;
        };
        /** TopupResponse */
        TopupResponse: {
            /** Payment Id */
            payment_id: string;
            /** Confirmation Url */
            confirmation_url: string;
            /** Amount Kopecks */
            amount_kopecks: number;
        };
        /** TransactionItem */
        TransactionItem: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Type */
            type: string;
            /** Amount Kopecks */
            amount_kopecks: number;
            /** Ref Id */
            ref_id: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Meta */
            meta: {
                [key: string]: unknown;
            } | null;
        };
        /** TransactionsResponse */
        TransactionsResponse: {
            /** Items */
            items: components["schemas"]["TransactionItem"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /**
         * UpdateKeyRequest
         * @description PATCH body — only ``name`` is honoured.
         *
         *     ``scope`` would be silently dropped by Pydantic (extra="ignore" default).
         *     We explicitly reject it with 400 so the SPA fails fast instead of the user
         *     thinking the change went through.
         */
        UpdateKeyRequest: {
            /** Name */
            name: string;
        };
        /**
         * UpdateProfileRequest
         * @description Profile patch — both fields optional. Pydantic v2 distinguishes
         *     ``None`` (explicit clear, currently unsupported) from missing (no-op)
         *     via ``model_fields_set``.
         */
        UpdateProfileRequest: {
            /** Name */
            name?: string | null;
            /** Email */
            email?: string | null;
        };
        /**
         * UpdateSettingsRequest
         * @description Settings patch. Both keys are optional; only the supplied ones change.
         *
         *     ``notifications`` is a free-form JSONB blob — we don't constrain the shape
         *     here so the SPA can ship new toggles without a server-side schema bump.
         */
        UpdateSettingsRequest: {
            /** Prompt Logging Enabled */
            prompt_logging_enabled?: boolean | null;
            /** Notifications */
            notifications?: {
                [key: string]: unknown;
            } | null;
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** VerifyEmailRequest */
        VerifyEmailRequest: {
            /** Token */
            token: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    csrf_bootstrap_v1_auth_csrf_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
    signup_v1_auth_signup_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SignupRequest"];
            };
        };
        responses: {
            /** @description User created; verification email queued. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Email already in use. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Per-IP signup rate limit exceeded. */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    verify_email_v1_auth_verify_email_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["VerifyEmailRequest"];
            };
        };
        responses: {
            /** @description Email verified; bonus credited if first time. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Token is invalid, expired, or already used. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    login_v1_auth_login_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoginRequest"];
            };
        };
        responses: {
            /** @description Login OK; cookies set, csrf_token in body. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Wrong email or password. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Email not verified yet. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Per-IP login rate limit exceeded. */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    logout_v1_auth_logout_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                vlt_refresh?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    refresh_v1_auth_refresh_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: {
                vlt_refresh?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Tokens rotated; new cookies set. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Refresh cookie missing, expired, or revoked. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    forgot_password_v1_auth_forgot_password_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ForgotPasswordRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reset_password_v1_auth_reset_password_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ResetPasswordRequest"];
            };
        };
        responses: {
            /** @description Password updated; sessions revoked. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Token invalid, expired, or already used. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    change_password_v1_auth_change_password_post: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChangePasswordRequest"];
            };
        };
        responses: {
            /** @description Password changed; all refresh sessions revoked. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Old password incorrect or session invalid. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Missing CSRF double-submit pair. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_account_v1_account_get: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AccountResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_profile_v1_account_profile_patch: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UpdateProfileRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AccountResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_settings_v1_account_settings_patch: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UpdateSettingsRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AccountResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_seats_v1_account_seats_get: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SeatItem"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_invite_v1_account_seats_invite_post: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateInviteRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreateInviteResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    accept_invite_v1_account_seats_accept_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AcceptInviteRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AcceptInviteResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    remove_seat_v1_account_seats__user_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path: {
                user_id: string;
            };
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_invites_v1_account_invites_get: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InviteItem"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    revoke_invite_v1_account_invites__invite_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path: {
                invite_id: string;
            };
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_keys_v1_keys_get: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description List of keys (possibly empty). */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KeyListItem"][];
                };
            };
            /** @description Session cookie missing or expired. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_key_v1_keys_post: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateKeyRequest"];
            };
        };
        responses: {
            /** @description Key created; plaintext returned **once**. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreateKeyResponse"];
                };
            };
            /** @description Session cookie missing or expired. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Tariff per-key limit reached. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    revoke_key_v1_keys__key_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path: {
                key_id: string;
            };
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Key revoked. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Key not found, already revoked, or other account. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_key_v1_keys__key_id__patch: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path: {
                key_id: string;
            };
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UpdateKeyRequest"];
            };
        };
        responses: {
            /** @description Key renamed. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KeyListItem"];
                };
            };
            /** @description Body contained ``scope`` (forbidden field). */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Key not found or belongs to another account. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    chat_completions_v1_chat_completions_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChatCompletionRequestBody"];
            };
        };
        responses: {
            /** @description Successful completion (or SSE stream when stream=true). */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Missing or invalid Bearer token. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Insufficient balance — top up via /v1/billing/topup. */
            402: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Per-account chat rate limit exceeded. */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description All providers in the failover chain failed. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    list_models_endpoint_v1_models_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Catalog of models the caller can route to. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Bearer token missing or invalid. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_model_endpoint_v1_models__model_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                model_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Model description. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Bearer token missing or invalid. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Unknown model id. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    usage_endpoint_v1_usage_get: {
        parameters: {
            query?: {
                from?: string | null;
                to?: string | null;
            };
            header?: {
                authorization?: string | null;
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_balance_v1_billing_balance_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BalanceResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_transactions_v1_billing_transactions_get: {
        parameters: {
            query?: {
                from?: string | null;
                to?: string | null;
                limit?: number;
            };
            header?: {
                authorization?: string | null;
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TransactionsResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_receipt_v1_billing_receipts__transaction_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "X-CSRF-Token"?: string | null;
            };
            path: {
                transaction_id: string;
            };
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReceiptResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_topup_v1_billing_topup_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TopupRequest"];
            };
        };
        responses: {
            /** @description Payment created; ``confirmation_url`` returned. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TopupResponse"];
                };
            };
            /** @description Auth missing. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Tariff doesn't allow top-up (e.g. fixed plan). */
            402: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description ЮKassa error during payment creation. */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    yookassa_webhook_v1_billing_yookassa_webhook_post: {
        parameters: {
            query?: never;
            header?: {
                "Content-HMAC"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    enable_autorefill_v1_billing_autorefill_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AutorefillRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    disable_autorefill_v1_billing_autorefill_delete: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    issue_telegram_link_v1_account_telegram_link_post: {
        parameters: {
            query?: never;
            header?: {
                "X-CSRF-Token"?: string | null;
            };
            path?: never;
            cookie?: {
                vlt_access?: string | null;
                vlt_csrf?: string | null;
            };
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TelegramLinkResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
