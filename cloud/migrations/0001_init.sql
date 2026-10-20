-- TokenLedger cloud ledger (Cloudflare D1). Times are ISO 8601 UTC strings. Secrets
-- (session cookies, device tokens, one-time codes, device codes) are stored as SHA-256
-- hashes only.

-- A person. Their machines share one ledger; nobody else can read it.
CREATE TABLE users (
    id         TEXT PRIMARY KEY,               -- random; also the WebAuthn user handle
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    admin      INTEGER NOT NULL DEFAULT 0,     -- may invite others
    timezone   TEXT,                           -- the ledger's day timezone, set by the first machine
    grace_days INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE passkeys (
    id           TEXT PRIMARY KEY,             -- credential id (base64url)
    user_id      TEXT NOT NULL REFERENCES users(id),
    public_key   BLOB NOT NULL,
    counter      INTEGER NOT NULL,
    transports   TEXT,
    created_at   TEXT NOT NULL,
    last_used_at TEXT
);
CREATE INDEX passkeys_user_idx ON passkeys(user_id);

CREATE TABLE sessions (
    id           TEXT PRIMARY KEY,             -- hash of the cookie value
    user_id      TEXT NOT NULL REFERENCES users(id),
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);

-- A WebAuthn ceremony in progress.
CREATE TABLE challenges (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('register', 'login')),
    challenge  TEXT NOT NULL,
    data       TEXT,                           -- register: {user_id, name, new_user, code_id, admin}
    expires_at TEXT NOT NULL
);

-- One-time codes: the first account (setup), an invitation, adding a passkey to an
-- account that lost its own (recover).
CREATE TABLE codes (
    id         TEXT PRIMARY KEY,               -- hash of the code
    kind       TEXT NOT NULL CHECK (kind IN ('setup', 'invite', 'recover')),
    user_id    TEXT,                           -- recover: the account; invite: who invited
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT
);

-- A machine signed in to a person's ledger. One active device per machine id.
CREATE TABLE devices (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    token        TEXT NOT NULL UNIQUE,         -- hash of the device token
    machine      TEXT NOT NULL,
    label        TEXT NOT NULL,
    os           TEXT NOT NULL,
    instance     TEXT NOT NULL,                -- the ledger on that machine
    host         TEXT NOT NULL,                -- hash of the computer's own id ('' if none)
    app_version  TEXT,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT,
    revoked_at   TEXT
);
CREATE UNIQUE INDEX devices_machine_idx ON devices(user_id, machine) WHERE revoked_at IS NULL;

-- A machine asking to be signed in (`tokenledger sync login`), until it is approved.
CREATE TABLE links (
    id         TEXT PRIMARY KEY,               -- hash of the device code
    user_code  TEXT NOT NULL UNIQUE,
    request    TEXT NOT NULL,                  -- JSON: machine, instance, host, app_version, timezone, grace_days
    origin     TEXT,                           -- where the request came from, e.g. "London, GB"
    status     TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied')),
    user_id    TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- The latest snapshot of each machine, and the latest dashboard of each person.
CREATE TABLE snapshots (
    user_id    TEXT NOT NULL,
    machine    TEXT NOT NULL,
    device_id  TEXT NOT NULL,
    label      TEXT NOT NULL,
    os         TEXT NOT NULL,
    instance   TEXT NOT NULL,
    taken_at   TEXT NOT NULL,
    size       INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, machine)
);

CREATE TABLE dashboards (
    user_id      TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    generated_by TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- The bytes behind snapshots (kind 'snapshot', name = machine) and dashboards (kind
-- 'view', name = range), split into parts below D1's row size limit.
CREATE TABLE blobs (
    user_id TEXT NOT NULL,
    kind    TEXT NOT NULL,
    name    TEXT NOT NULL,
    part    INTEGER NOT NULL,
    data    BLOB NOT NULL,
    PRIMARY KEY (user_id, kind, name, part)
);
