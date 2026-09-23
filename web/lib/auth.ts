// Auth boundary for the web client — every API call goes through `authHeaders()`
// below rather than each call site deciding for itself how to identify the user.
//
// NEXT_PUBLIC_AUTH_MODE=local (default, for local dev against a local API):
// requests carry X-Dev-User-Id / X-Workspace-Id, matching the API's own
// AUTH_MODE=local — this is NOT production authentication, just a deterministic
// developer identity, the same way the API's LocalAuthProvider works.
//
// NEXT_PUBLIC_AUTH_MODE=oidc: requests must carry a real
// `Authorization: Bearer <token>` header from a signed-in session. This repo
// does not yet implement a Cognito Hosted UI sign-in flow (see README's
// "Cognito / OIDC" section) — getAccessToken() below is the extension point a
// real login integration fills in; nothing else in this file needs to change
// once it does.

export function isLocalAuthMode(): boolean {
  return (process.env.NEXT_PUBLIC_AUTH_MODE ?? "local") !== "oidc";
}

// Placeholder: a real Cognito Hosted UI / OIDC integration would store the
// session (e.g. in an httpOnly cookie set by a server route) and this would
// read it back — never a production app's access token from localStorage.
export function getAccessToken(): string | null {
  return null;
}

export function authHeaders(): Record<string, string> {
  if (isLocalAuthMode()) {
    const headers: Record<string, string> = {};
    const devUserId = process.env.NEXT_PUBLIC_DEV_USER_ID;
    const devWorkspaceId = process.env.NEXT_PUBLIC_DEV_WORKSPACE_ID;
    if (devUserId) headers["X-Dev-User-Id"] = devUserId;
    if (devWorkspaceId) headers["X-Workspace-Id"] = devWorkspaceId;
    return headers;
  }
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
