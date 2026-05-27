# Feature Spec — STORY-019: Ops Console Dashboard Frontend

## 1. Overview

A React 18 SPA that provides a visual dashboard for monitoring and managing autonomous dev agents. Built with Vite + TypeScript, served from FastAPI via `StaticFiles`. Consumes the ops console API (`/api/*`) with TanStack Query for stale-while-revalidate polling.

**Scope:** 9 acceptance criteria, ~10 components, ~5 hooks, 1 API client module, 1 type definition module.

---

## 2. Architecture

### 2.1 Directory Structure

```
frontend/
├── index.html                    # Vite entry point
├── package.json
├── tsconfig.json
├── vite.config.ts                # Proxy /api → backend in dev mode
├── tailwind.config.js
├── postcss.config.js
├── src/
│   ├── main.tsx                  # App entry: QueryClientProvider + BrowserRouter
│   ├── App.tsx                   # Route definitions + AuthGuard
│   ├── index.css                 # @tailwind base/components/utilities
│   ├── api/
│   │   └── client.ts            # fetch wrapper with X-API-Key injection
│   ├── hooks/
│   │   ├── useAuth.ts           # Auth state: login, logout, isAuthenticated
│   │   ├── useFleet.ts          # GET /api/fleet — TanStack Query
│   │   ├── useAgents.ts         # GET /api/agents — TanStack Query
│   │   ├── useAgent.ts          # GET /api/agents/{name} — TanStack Query
│   │   ├── useAlerts.ts         # GET /api/alerts — TanStack Query
│   │   └── useAgentActions.ts   # POST restart/pause — TanStack Mutation
│   ├── components/
│   │   ├── LoginPage.tsx         # API key input + validation
│   │   ├── DashboardLayout.tsx   # Shell: header, nav, alert banner, content area
│   │   ├── FleetOverviewBar.tsx  # KPI cards: spend, agents, stories, health
│   │   ├── AgentGrid.tsx         # Grid of AgentCard components
│   │   ├── AgentCard.tsx         # Single agent card with status badge
│   │   ├── AgentDetailView.tsx   # Detail page: cost chart, timeline, actions
│   │   ├── AgentContextPanel.tsx # Teams deep link + blocker badge
│   │   ├── CostChart.tsx         # Recharts AreaChart for 30-day cost history
│   │   ├── ActivityTimeline.tsx  # Chronological activity list
│   │   ├── AlertBanner.tsx       # Top bar with active alert count
│   │   ├── AlertHistoryPanel.tsx # Filterable alert list
│   │   └── StatusBadge.tsx       # Colored status indicator
│   └── types/
│       └── api.ts               # TypeScript interfaces for all API responses
└── dist/                         # Build output (gitignored, served by FastAPI)
```

### 2.2 Routing

| Path | Component | Auth Required |
|------|-----------|---------------|
| `/login` | `LoginPage` | No |
| `/` | `DashboardLayout` → `FleetOverviewBar` + `AgentGrid` + `AlertBanner` | Yes |
| `/agents/:name` | `DashboardLayout` → `AgentDetailView` | Yes |
| `/alerts` | `DashboardLayout` → `AlertHistoryPanel` | Yes |

`App.tsx` wraps authenticated routes in an `AuthGuard` that redirects to `/login` if no API key is in `localStorage`.

### 2.3 Data Flow

```
localStorage("ops_api_key")
       │
       ▼
  api/client.ts ──── X-API-Key header injection
       │
       ▼
  hooks/useFleet.ts ─── useQuery({ queryKey: ["fleet"], queryFn, staleTime: 30_000 })
  hooks/useAgents.ts ── useQuery({ queryKey: ["agents"], queryFn, staleTime: 30_000 })
  hooks/useAgent.ts ─── useQuery({ queryKey: ["agent", name], queryFn, staleTime: 30_000 })
  hooks/useAlerts.ts ── useQuery({ queryKey: ["alerts"], queryFn, staleTime: 30_000 })
       │
       ▼
  Components render from query data, show loading/error states
```

**Mutation flow (restart/pause):**
```
useAgentActions.ts
  useMutation({
    mutationFn: (action) => client.post(`/api/agents/${name}/${action}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] })
  })
```

---

## 3. Component Specifications

### 3.1 LoginPage

**Purpose:** API key authentication entry point.

**Behavior:**
1. Single input field (type=password) + "Connect" button
2. On submit: call `GET /api/health` with `X-API-Key: <input>`
3. On 200: store key in `localStorage("ops_api_key")`, navigate to `/`
4. On 401/network error: show inline error ("Invalid API key" or "Connection failed")
5. Auto-focus input on mount

**Layout:** Centered card on a dark background. Logo/title "Ops Console" above the form.

### 3.2 DashboardLayout

**Purpose:** App shell for all authenticated pages.

**Structure:**
```
┌─────────────────────────────────────────────────┐
│ Header: "Ops Console"  [AlertBanner]  [Logout]  │
├─────────────────────────────────────────────────┤
│ FleetOverviewBar                                │
├─────────────────────────────────────────────────┤
│ {children} — AgentGrid or AgentDetailView       │
└─────────────────────────────────────────────────┘
```

**Behavior:**
- Logout button clears `localStorage("ops_api_key")` and navigates to `/login`
- AlertBanner is always visible in the header area
- FleetOverviewBar is shown on all dashboard pages

### 3.3 FleetOverviewBar

**Purpose:** Four KPI cards showing fleet-wide metrics.

**Data source:** `useFleet()` → `GET /api/fleet`

**Cards:**
| Card | Field | Format | Icon |
|------|-------|--------|------|
| Total Spend | `total_spend` | `$X,XXX.XX` | 💰 |
| Active Agents | `active_agents` / total | `N / M` | 🤖 |
| Active Stories | `active_stories` | `N` | 📋 |
| Health Score | `health_score` | `XX%` with color (green ≥80, yellow ≥50, red <50) | ❤️ |

**Layout:** Horizontal flex, 4 equal cards, responsive wrap at narrow viewports.

### 3.4 AgentCard

**Purpose:** Summary card for a single agent in the grid.

**Data source:** Single agent object from `useAgents()` array.

**Fields displayed:**
- Agent name (bold, top)
- StatusBadge (colored dot + label)
- Current story (truncated to 40 chars)
- Current phase (e.g., "Phase 8 — Implementation")
- Cost today (`$X.XX`)
- Last activity (relative time: "2m ago", "1h ago")

**Behavior:**
- Click navigates to `/agents/:name`
- Hover: subtle shadow elevation
- Status colors: green=active, yellow=idle, red=error, gray=offline

### 3.5 AgentGrid

**Purpose:** Responsive grid of AgentCards.

**Data source:** `useAgents()` → `GET /api/agents`

**Layout:** CSS Grid, `grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))`, gap 1rem.

**States:**
- Loading: skeleton cards (3 placeholders with pulse animation)
- Error: error message with retry button
- Empty: "No agents registered" message

### 3.6 AgentDetailView

**Purpose:** Full detail page for a single agent.

**Data source:** `useAgent(name)` → `GET /api/agents/{name}`

**Layout:**
```
┌──────────────────────────────────────────────────┐
│ ← Back to Dashboard    Agent: {name}   [Status]  │
├──────────────────────┬───────────────────────────┤
│ CostChart            │ AgentContextPanel         │
│ (30-day AreaChart)   │ (Teams link, blocker)     │
├──────────────────────┴───────────────────────────┤
│ Action buttons: [Restart] [Pause]                │
├──────────────────────────────────────────────────┤
│ ActivityTimeline                                 │
│ (chronological list of recent activities)        │
└──────────────────────────────────────────────────┘
```

**Restart/Pause buttons:**
- Restart: `POST /api/agents/{name}/restart` via `useAgentActions`
- Pause: `POST /api/agents/{name}/pause` via `useAgentActions`
- Confirm dialog before executing (window.confirm)
- Disabled while mutation is pending
- On success: invalidate agent + agents queries, show success toast

### 3.7 CostChart

**Purpose:** 30-day cost history as a Recharts AreaChart.

**Data source:** `cost_history[]` from agent detail response.

**Recharts config:**
```tsx
<ResponsiveContainer width="100%" height={300}>
  <AreaChart data={costHistory}>
    <defs>
      <linearGradient id="costGradient" x1="0" y1="0" x2="0" y2="1">
        <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
        <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
      </linearGradient>
    </defs>
    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
    <XAxis dataKey="date" stroke="#9ca3af" />
    <YAxis stroke="#9ca3af" tickFormatter={(v) => `$${v}`} />
    <Tooltip formatter={(v) => [`$${v}`, "Cost"]} />
    <Area type="monotone" dataKey="cost" stroke="#3b82f6" fill="url(#costGradient)" />
  </AreaChart>
</ResponsiveContainer>
```

### 3.8 AgentContextPanel

**Purpose:** Teams deep link and blocker status for an agent.

**Data source:** `context` field from agent detail response.

**Fields:**
- Teams conversation link (opens in new tab)
- Blocker badge:
  - Red badge: message starts with "Blocked:"
  - Yellow badge: message starts with "Decision needed:"
  - No badge otherwise

### 3.9 ActivityTimeline

**Purpose:** Chronological list of recent agent activities.

**Data source:** `activity_timeline[]` from agent detail response.

**Each entry:**
- Timestamp (relative + absolute on hover)
- Activity type (icon: commit, phase transition, message, error)
- Description text

**Layout:** Vertical list with left border timeline indicator.

### 3.10 AlertBanner

**Purpose:** Persistent header element showing active alert count.

**Data source:** `useAlerts()` → `GET /api/alerts`

**Behavior:**
- Shows count: "⚠ N active alerts" with yellow/red background
- Clickable: navigates to `/alerts`
- Hidden when `active_count === 0`

### 3.11 AlertHistoryPanel

**Purpose:** Full alert list with filtering.

**Data source:** `useAlerts()` → `GET /api/alerts`

**Filters (client-side):**
- By agent (dropdown)
- By type (dropdown: anomaly, threshold, error)
- By date range (last 24h, 7d, 30d)

**Each alert row:**
- Timestamp
- Agent name (link to agent detail)
- Type badge
- Message

### 3.12 StatusBadge

**Purpose:** Reusable colored status indicator.

**Props:** `status: "active" | "idle" | "error" | "offline"`

**Rendering:**
| Status | Color | Tailwind Classes |
|--------|-------|-----------------|
| active | green | `bg-green-500` dot + `text-green-400` label |
| idle | yellow | `bg-yellow-500` dot + `text-yellow-400` label |
| error | red | `bg-red-500` dot + `text-red-400` label |
| offline | gray | `bg-gray-500` dot + `text-gray-400` label |

---

## 4. TypeScript Interfaces

```typescript
// types/api.ts

export interface FleetOverview {
  total_spend: number;
  active_agents: number;
  total_agents: number;
  active_stories: number;
  health_score: number;
}

export interface AgentSummary {
  name: string;
  status: "active" | "idle" | "error" | "offline";
  current_story: string | null;
  phase: string | null;
  cost_today: number;
  last_activity: string; // ISO 8601
}

export interface CostHistoryEntry {
  date: string; // YYYY-MM-DD
  cost: number;
}

export interface ActivityEntry {
  timestamp: string; // ISO 8601
  type: "commit" | "phase" | "message" | "error" | "restart";
  description: string;
}

export interface AgentContext {
  teams_link: string | null;
  blocker_status: string | null; // "Blocked: ...", "Decision needed: ...", or null
}

export interface AgentDetail {
  name: string;
  status: "active" | "idle" | "error" | "offline";
  current_story: string | null;
  phase: string | null;
  cost_today: number;
  last_activity: string;
  cost_history: CostHistoryEntry[];
  activity_timeline: ActivityEntry[];
  context: AgentContext;
}

export interface AlertItem {
  id: string;
  agent: string;
  type: "anomaly" | "threshold" | "error";
  message: string;
  timestamp: string; // ISO 8601
  resolved: boolean;
}

export interface AlertsResponse {
  active_count: number;
  items: AlertItem[];
}

export interface ActionResponse {
  success: boolean;
  message: string;
}
```

---

## 5. API Client

```typescript
// api/client.ts

const API_KEY_STORAGE = "ops_api_key";

function getApiKey(): string | null {
  return localStorage.getItem(API_KEY_STORAGE);
}

export function setApiKey(key: string): void {
  localStorage.setItem(API_KEY_STORAGE, key);
}

export function clearApiKey(): void {
  localStorage.removeItem(API_KEY_STORAGE);
}

export function isAuthenticated(): boolean {
  return !!getApiKey();
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const apiKey = getApiKey();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(apiKey ? { "X-API-Key": apiKey } : {}),
    ...(options.headers as Record<string, string> || {}),
  };

  const response = await fetch(path, { ...options, headers });

  if (response.status === 401) {
    clearApiKey();
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }

  return response.json();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
};
```

---

## 6. TanStack Query Configuration

```typescript
// main.tsx
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,           // 30 seconds
      refetchOnWindowFocus: true,   // Refresh when tab regains focus
      retry: 2,                     // Retry twice on failure
      refetchInterval: 30_000,      // Poll every 30s (active polling)
    },
  },
});
```

All hooks use the shared `queryClient`. Mutations (`useAgentActions`) invalidate related queries on success to ensure immediate UI refresh after restart/pause.

---

## 7. FastAPI Integration

Add to the existing FastAPI app (or create `ops/console_mount.py`):

```python
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"

def mount_frontend(app: FastAPI) -> None:
    """Mount the ops console SPA frontend.
    
    MUST be called AFTER all /api/* routes are registered.
    """
    if not FRONTEND_DIR.exists():
        return  # No build yet; skip mount
    
    # Serve static assets (JS, CSS, images)
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="static-assets")
    
    # SPA catch-all: serve index.html for all non-API routes
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Don't catch /api/* routes (they should already be registered above)
        file_path = FRONTEND_DIR / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(FRONTEND_DIR / "index.html"))
```

**Critical constraint:** The `mount_frontend()` call MUST come after all `/api/*` route registrations. FastAPI uses first-match routing, so the catch-all would shadow API routes if registered first.

---

## 8. Vite Configuration

```typescript
// vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",  // FastAPI dev server
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
```

In development, Vite proxies `/api/*` to the FastAPI backend. In production, FastAPI serves both the API and the built frontend.

---

## 9. Responsive Design

**Breakpoints:**
- `≥1024px` (lg): Full grid layout, side-by-side panels in detail view
- `<1024px`: Stacked layout, single column

**Tailwind approach:**
- Mobile-first defaults
- `lg:` prefix for desktop-specific layouts
- `grid-cols-1 lg:grid-cols-2` for detail view panels
- `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3` for agent card grid

---

## 10. Error Handling

| Scenario | Behavior |
|----------|----------|
| API key invalid (401) | Clear localStorage, redirect to `/login` |
| Network error | Show error state in component with "Retry" button |
| API returns unexpected shape | Nullish coalescing on all fields; show "—" for missing data |
| Mutation failure (restart/pause) | Show error toast, keep button enabled for retry |
| Empty agent list | Show "No agents registered" placeholder |
| Empty alerts | Hide AlertBanner entirely |

---

## 11. Acceptance Criteria Mapping

| AC | Component(s) | Hook(s) | Verified By |
|----|-------------|---------|-------------|
| AC-1 | LoginPage | useAuth | Login flow test |
| AC-2 | FleetOverviewBar | useFleet | KPI rendering test |
| AC-3 | AgentGrid, AgentCard, StatusBadge | useAgents | Grid rendering + badge color test |
| AC-4 | AgentDetailView, CostChart, ActivityTimeline | useAgent, useAgentActions | Detail view + chart + action test |
| AC-5 | AlertBanner, AlertHistoryPanel | useAlerts | Alert rendering + filter test |
| AC-6 | AgentContextPanel | useAgent | Teams link + blocker badge test |
| AC-7 | All hooks | — | staleTime config assertion |
| AC-8 | FastAPI mount | — | Build + serve test |
| AC-9 | All components | — | Viewport width assertion |

---

## 12. Dependencies (npm)

| Package | Version | Purpose |
|---------|---------|---------|
| react | ^18.3 | UI framework |
| react-dom | ^18.3 | React DOM renderer |
| react-router-dom | ^6.28 | Client-side routing |
| @tanstack/react-query | ^5.62 | Server state management |
| recharts | ^2.15 | Chart components |
| typescript | ^5.7 | Type safety |
| @vitejs/plugin-react | ^4.3 | Vite React plugin |
| vite | ^6.0 | Build toolchain |
| tailwindcss | ^3.4 | Utility CSS |
| postcss | ^8.4 | CSS processing |
| autoprefixer | ^10.4 | Vendor prefixes |
| @types/react | ^18.3 | React type definitions |
| @types/react-dom | ^18.3 | React DOM type definitions |
