-- Signing in with Google. An identity links one Google account (its stable `sub`) to one
-- person here; a Google account that is not linked cannot sign in.
CREATE TABLE identities (
    provider   TEXT NOT NULL,                  -- 'google'
    subject    TEXT NOT NULL,                  -- the provider's id for the account
    user_id    TEXT NOT NULL REFERENCES users(id),
    email      TEXT,                           -- shown in the account menu only
    created_at TEXT NOT NULL,
    PRIMARY KEY (provider, subject)
);
CREATE INDEX identities_user_idx ON identities(user_id);

-- A Google sign-in in progress: the browser holds a cookie whose hash is the id.
CREATE TABLE oauth_states (
    id         TEXT PRIMARY KEY,               -- hash of the browser's state cookie
    state      TEXT NOT NULL,                  -- sent to Google and returned with the answer
    nonce      TEXT NOT NULL,                  -- must come back inside the ID token
    data       TEXT NOT NULL,                  -- {purpose, user_id, name, code_id, admin}
    expires_at TEXT NOT NULL
);
