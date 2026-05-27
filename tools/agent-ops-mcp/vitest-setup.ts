/**
 * Vitest global setup — ensures globalThis.fetch is always a spy
 * so .toHaveBeenCalled() assertions work even when tests don't mock fetch.
 */
import { vi, beforeEach } from "vitest";

const _originalFetch = globalThis.fetch;

beforeEach(() => {
  // Re-wrap if not already a mock (e.g. after restoreAllMocks)
  if (!vi.isMockFunction(globalThis.fetch)) {
    globalThis.fetch = vi.fn().mockImplementation(_originalFetch) as typeof fetch;
  }
});
