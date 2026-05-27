import '@testing-library/jest-dom';

// Polyfill ResizeObserver for jsdom (required by recharts ResponsiveContainer)
(globalThis as Record<string, unknown>).ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};
