import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { loginRequest } from '../auth/msalConfig';
import { clearApiKey } from '../api/client';

export function useAuth() {
  const { instance, accounts } = useMsal();
  const isAuthenticated = useIsAuthenticated();

  const login = async () => {
    await instance.loginPopup(loginRequest);
  };

  const logout = async () => {
    clearApiKey();
    await instance.logoutPopup().catch(() => {
      window.location.href = '/login';
    });
  };

  const account = accounts[0] ?? null;

  return {
    isAuthenticated,
    login,
    logout,
    account,
    displayName: account?.name ?? null,
    email: account?.username ?? null,
  };
}
