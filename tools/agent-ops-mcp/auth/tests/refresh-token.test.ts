/**
 * Tests for Graph API token refresh — STORY-016 v2.
 *
 * Tests the token manager that handles OAuth2 access token lifecycle
 * for Microsoft Graph API (Teams messaging).
 *
 * Token storage: ~/.agent-ops/graph-token.json
 * OAuth2 endpoint: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
 *
 * Tests:
 *   T1: getAccessToken returns cached token when not expired
 *   T2: getAccessToken refreshes when token is expired (mock /oauth2/v2.0/token)
 *   T3: getAccessToken throws clear error when refresh fails and no device code available
 *   T4: token file is written after successful refresh
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// These imports DO NOT EXIST yet — tests will fail at import (RED).
import {
  GraphTokenManager,
  type TokenStore,
} from "../../auth/token-manager.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TEST_TENANT = "1060148b-e4f2-4e64-880e-b8b05958e6fe";
const TEST_CLIENT_ID = "dc0cba0b-f12d-40da-88f0-adcda94075be";
const TEST_TOKEN_PATH = "/tmp/test-graph-token.json";

const VALID_TOKEN: TokenStore = {
  access_token: "eyJ-valid-access-token",
  refresh_token: "eyJ-valid-refresh-token",
  expires_at: Math.floor(Date.now() / 1000) + 3600, // 1 hour from now
};

const EXPIRED_TOKEN: TokenStore = {
  access_token: "eyJ-expired-access-token",
  refresh_token: "eyJ-valid-refresh-token",
  expires_at: Math.floor(Date.now() / 1000) - 300, // Expired 5 min ago
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

let tokenManager: GraphTokenManager;

beforeEach(() => {
  tokenManager = new GraphTokenManager({
    tenantId: TEST_TENANT,
    clientId: TEST_CLIENT_ID,
    tokenPath: TEST_TOKEN_PATH,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// T1: cached token when not expired
// ---------------------------------------------------------------------------

describe("getAccessToken — cache", () => {
  it("T1: returns cached token when not expired", async () => {
    // Mock file read to return a valid (non-expired) token
    vi.spyOn(tokenManager, "loadTokenFile").mockResolvedValue(VALID_TOKEN);

    const token = await tokenManager.getAccessToken();

    expect(token).toBe(VALID_TOKEN.access_token);
    // Should NOT call the OAuth endpoint
    expect(globalThis.fetch).not.toHaveBeenCalled?.();
  });
});

// ---------------------------------------------------------------------------
// T2: refresh when expired
// ---------------------------------------------------------------------------

describe("getAccessToken — refresh", () => {
  it("T2: refreshes when token is expired", async () => {
    vi.spyOn(tokenManager, "loadTokenFile").mockResolvedValue(EXPIRED_TOKEN);
    vi.spyOn(tokenManager, "saveTokenFile").mockResolvedValue(undefined);

    const newTokenResponse = {
      access_token: "eyJ-new-access-token",
      refresh_token: "eyJ-new-refresh-token",
      expires_in: 3600,
    };

    // Mock the OAuth2 token endpoint
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(newTokenResponse),
    });

    const token = await tokenManager.getAccessToken();

    expect(token).toBe("eyJ-new-access-token");

    // Verify OAuth2 endpoint was called with correct params
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining(`${TEST_TENANT}/oauth2/v2.0/token`),
      expect.objectContaining({ method: "POST" }),
    );

    // Verify refresh_token was sent in the body
    const callArgs = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const body = callArgs[1]?.body as string;
    expect(body).toContain("refresh_token");
    expect(body).toContain(EXPIRED_TOKEN.refresh_token);
  });
});

// ---------------------------------------------------------------------------
// T3: clear error when refresh fails
// ---------------------------------------------------------------------------

describe("getAccessToken — error handling", () => {
  it("T3: throws clear error when refresh fails and no device code available", async () => {
    vi.spyOn(tokenManager, "loadTokenFile").mockResolvedValue(EXPIRED_TOKEN);

    // OAuth2 endpoint returns error
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: () =>
        Promise.resolve({
          error: "invalid_grant",
          error_description: "Refresh token has expired",
        }),
    });

    await expect(tokenManager.getAccessToken()).rejects.toThrow(
      /refresh.*fail|expired|invalid_grant|re-authenticate/i,
    );
  });
});

// ---------------------------------------------------------------------------
// T4: token file written after refresh
// ---------------------------------------------------------------------------

describe("getAccessToken — persistence", () => {
  it("T4: token file is written after successful refresh", async () => {
    vi.spyOn(tokenManager, "loadTokenFile").mockResolvedValue(EXPIRED_TOKEN);
    const saveSpy = vi.spyOn(tokenManager, "saveTokenFile").mockResolvedValue(undefined);

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          access_token: "eyJ-refreshed-token",
          refresh_token: "eyJ-new-refresh",
          expires_in: 3600,
        }),
    });

    await tokenManager.getAccessToken();

    expect(saveSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        access_token: "eyJ-refreshed-token",
        refresh_token: "eyJ-new-refresh",
      }),
    );
  });
});
