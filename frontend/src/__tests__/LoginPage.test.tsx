import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LoginPage } from '../components/LoginPage';

// --- MSAL mocks ----------------------------------------------------------------

const mockLoginPopup = vi.fn();
const mockSetActiveAccount = vi.fn();
const mockGetActiveAccount = vi.fn();

vi.mock('@azure/msal-react', () => ({
  MsalProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useMsal: () => ({
    instance: {
      loginPopup: mockLoginPopup,
      setActiveAccount: mockSetActiveAccount,
      getActiveAccount: mockGetActiveAccount,
    },
    accounts: mockGetActiveAccount() ? [mockGetActiveAccount()] : [],
    inProgress: 'none',
  }),
  useIsAuthenticated: () => !!mockGetActiveAccount(),
}));

vi.mock('@azure/msal-browser', () => ({
  PublicClientApplication: vi.fn(),
}));

vi.mock('../auth/msalConfig', () => ({
  msalConfig: {
    auth: {
      clientId: 'test-client-id',
      authority: 'https://login.microsoftonline.com/test-tenant',
      redirectUri: 'http://localhost:5173/auth/callback',
    },
    cache: { cacheLocation: 'localStorage' },
  },
  loginRequest: { scopes: ['openid', 'profile', 'email'] },
}));

// --- helpers ----------------------------------------------------------------

function renderLoginPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// ---------------------------------------------------------------------------

describe('LoginPage — Entra ID SSO', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetActiveAccount.mockReturnValue(null);
  });

  it('renders the page title "Ops Console"', () => {
    renderLoginPage();
    expect(screen.getByText(/ops console/i)).toBeInTheDocument();
  });

  it('renders a "Sign in with Microsoft" button', () => {
    renderLoginPage();
    expect(screen.getByRole('button', { name: /sign in with microsoft/i })).toBeInTheDocument();
  });

  it('triggers MSAL loginPopup on button click', async () => {
    mockLoginPopup.mockResolvedValueOnce({ account: { name: 'Test' } });
    renderLoginPage();

    fireEvent.click(screen.getByRole('button', { name: /sign in with microsoft/i }));

    await waitFor(() => {
      expect(mockLoginPopup).toHaveBeenCalled();
    });
  });

  it('does not render a password input field', () => {
    renderLoginPage();
    const passwordInput = document.querySelector('input[type="password"]');
    expect(passwordInput).toBeNull();
  });
});
