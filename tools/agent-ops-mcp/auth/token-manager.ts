/**
 * Graph API OAuth2 token manager — STORY-016 v2.
 *
 * Manages the lifecycle of Microsoft Graph API access tokens:
 * - Loads cached tokens from ~/.agent-ops/graph-token.json
 * - Returns cached token when not expired
 * - Refreshes via OAuth2 /token endpoint when expired
 * - Persists refreshed tokens to disk
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TokenStore {
  access_token: string;
  refresh_token: string;
  expires_at: number; // Unix timestamp (seconds)
}

export interface TokenManagerOptions {
  tenantId: string;
  clientId: string;
  tokenPath: string;
}

// ---------------------------------------------------------------------------
// GraphTokenManager
// ---------------------------------------------------------------------------

export class GraphTokenManager {
  private tenantId: string;
  private clientId: string;
  private tokenPath: string;

  constructor(opts: TokenManagerOptions) {
    this.tenantId = opts.tenantId;
    this.clientId = opts.clientId;
    this.tokenPath = opts.tokenPath;
  }

  /**
   * Get a valid access token.
   * Returns cached token if not expired, otherwise refreshes.
   */
  async getAccessToken(): Promise<string> {
    const store = await this.loadTokenFile();

    // Check if token is still valid (with 60s buffer)
    const now = Math.floor(Date.now() / 1000);
    if (store.expires_at > now + 60) {
      return store.access_token;
    }

    // Token expired — refresh
    return this.refreshToken(store);
  }

  /**
   * Load token store from disk.
   * Override in tests to provide mock data.
   */
  async loadTokenFile(): Promise<TokenStore> {
    const data = await readFile(this.tokenPath, "utf-8");
    return JSON.parse(data) as TokenStore;
  }

  /**
   * Save token store to disk.
   * Override in tests to capture writes.
   */
  async saveTokenFile(store: TokenStore): Promise<void> {
    await mkdir(dirname(this.tokenPath), { recursive: true });
    await writeFile(this.tokenPath, JSON.stringify(store, null, 2), "utf-8");
  }

  // -----------------------------------------------------------------------
  // Private
  // -----------------------------------------------------------------------

  private async refreshToken(currentStore: TokenStore): Promise<string> {
    const tokenUrl = `https://login.microsoftonline.com/${this.tenantId}/oauth2/v2.0/token`;

    const body = new URLSearchParams({
      client_id: this.clientId,
      grant_type: "refresh_token",
      refresh_token: currentStore.refresh_token,
      scope: "https://graph.microsoft.com/.default offline_access",
    });

    const resp = await fetch(tokenUrl, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });

    if (!resp.ok) {
      const errorData = await resp.json().catch(() => ({}));
      const desc =
        (errorData as Record<string, string>).error_description ??
        (errorData as Record<string, string>).error ??
        "unknown error";
      throw new Error(
        `Token refresh failed (${resp.status}): ${desc}. ` +
          "Re-authenticate with: graph-token.sh",
      );
    }

    const data = (await resp.json()) as {
      access_token: string;
      refresh_token: string;
      expires_in: number;
    };

    const newStore: TokenStore = {
      access_token: data.access_token,
      refresh_token: data.refresh_token,
      expires_at: Math.floor(Date.now() / 1000) + data.expires_in,
    };

    await this.saveTokenFile(newStore);

    return newStore.access_token;
  }
}
