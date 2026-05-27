/**
 * API client — uses MSAL ID token for Entra ID SSO.
 * Falls back to API key for MCP tools.
 */

import type { IPublicClientApplication, AccountInfo } from '@azure/msal-browser';

const API_KEY_STORAGE = 'ops_api_key';

export function getApiKey(): string | null {
  return localStorage.getItem(API_KEY_STORAGE);
}

export function setApiKey(key: string): void {
  localStorage.setItem(API_KEY_STORAGE, key);
}

export function clearApiKey(): void {
  localStorage.removeItem(API_KEY_STORAGE);
}

// MSAL instance reference (set once at startup)
let msalInstance: IPublicClientApplication | null = null;

export function setMsalInstance(instance: IPublicClientApplication): void {
  msalInstance = instance;
}

export function getMsalInstance(): IPublicClientApplication | null {
  return msalInstance;
}

/**
 * Get the active MSAL account, if any.
 */
function getAccount(): AccountInfo | null {
  if (!msalInstance) return null;
  return msalInstance.getActiveAccount() ?? msalInstance.getAllAccounts()[0] ?? null;
}

/**
 * Check if the user is authenticated (MSAL or API key).
 */
export function isAuthenticated(): boolean {
  return !!getAccount() || !!getApiKey();
}

/**
 * Get an ID token from the MSAL cache for backend auth.
 * Uses idToken (aud = our client ID), NOT accessToken (aud = graph.microsoft.com).
 */
async function getIdToken(): Promise<string | null> {
  if (!msalInstance) return null;
  const account = getAccount();
  if (!account) return null;

  try {
    const response = await msalInstance.acquireTokenSilent({
      scopes: ['openid', 'profile', 'email'],
      account,
    });
    return response.idToken || null;
  } catch {
    return null;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  };

  // Try MSAL ID token first
  const idToken = await getIdToken();
  if (idToken) {
    headers['Authorization'] = `Bearer ${idToken}`;
  } else {
    // Fall back to API key (MCP tools)
    const apiKey = getApiKey();
    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }
  }

  const response = await fetch(path, { ...options, headers });

  if (response.status === 401) {
    // Only redirect if the user was explicitly using API-key auth.
    // When MSAL is configured (even if token acquisition failed), do NOT
    // redirect — the React AuthGuard handles navigation, avoiding
    // infinite redirect loops between /login and the MSAL popup.
    const apiKey = getApiKey();
    if (apiKey && !msalInstance?.getActiveAccount()) {
      clearApiKey();
      window.location.href = '/login';
    }
    throw new Error('Unauthorized');
  }

  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  return response.json();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

// --- STORY-738: Needs-info Q&A API functions ---

import type { QuestionResponse, AnswerResponse } from '../types/api';

/** Fetch question for a needs_info story */
export async function getQuestion(storyId: string, repo?: string): Promise<QuestionResponse> {
  const q = repo ? `?repo=${encodeURIComponent(repo)}` : '';
  return api.get<QuestionResponse>(`/api/dispatch/${storyId}/question${q}`);
}

/** Submit answer and resume the story */
export async function submitAnswer(
  storyId: string,
  repo: string | undefined,
  answer: string,
  operator: string = 'mark',
): Promise<AnswerResponse> {
  const q = repo ? `?repo=${encodeURIComponent(repo)}` : '';
  return api.post<AnswerResponse>(`/api/dispatch/${storyId}/answer${q}`, {
    answer,
    operator,
  });
}
