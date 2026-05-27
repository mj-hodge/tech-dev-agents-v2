/**
 * MSAL configuration for Entra ID SSO.
 *
 * App Registration Client ID: 746105b5-1e11-4e75-9fd3-a27a355229fb
 * Tenant: gorillacommerce.co (1060148b-e4f2-4e64-880e-b8b05958e6fe)
 */

import type { Configuration, PopupRequest } from '@azure/msal-browser';

const TENANT_ID = '1060148b-e4f2-4e64-880e-b8b05958e6fe';
const CLIENT_ID = '746105b5-1e11-4e75-9fd3-a27a355229fb';

export const msalConfig: Configuration = {
  auth: {
    clientId: CLIENT_ID,
    authority: `https://login.microsoftonline.com/${TENANT_ID}`,
  },
  cache: {
    cacheLocation: 'localStorage',
    storeAuthStateInCookie: false,
  },
};

export const loginRequest: PopupRequest = {
  scopes: ['openid', 'profile', 'email'],
};
