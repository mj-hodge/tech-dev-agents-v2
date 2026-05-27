# Foundry Auth Runbook — Morris Service Principal

> **STORY-771** — Credential renewal procedure for the Azure AD SP used by Morris's M365/Teams bridge and Foundry endpoint auth.

## Credential Details

| Field | Value |
|-------|-------|
| Type | Azure AD App Registration — Client Secret |
| Env Vars | `OPS_AZURE_TENANT_ID`, `OPS_AZURE_CLIENT_ID`, `OPS_AZURE_CLIENT_SECRET` |
| Storage | Azure Key Vault (production pattern) |
| Env File | `/home/hermes/.hermes/.env` |
| Consumers | Morris M365 bridge, `foundry_pace_check.py`, `check_foundry_auth.py` |

## Rotation Procedure (Mark-Executed)

> **Only Mark can execute steps 1-4** — requires Azure portal access with Owner/Contributor role on the App Registration.

1. **Azure Portal → App Registrations → [Morris App] → Certificates & secrets**
2. **Add new client secret:**
   - Description: `morris-foundry-sp-YYYY-MM` (use current year-month)
   - Expiry: 6 months (180 days) — quarterly rotation preferred, 6-month max
3. **Copy the new secret value** (only visible at creation time)
4. **Update Azure Key Vault:**
   - Navigate to Key Vault → Secrets
   - Update `OPS-AZURE-CLIENT-SECRET` with the new value
5. **Update Morris VM env file:**
   ```bash
   ssh hermes@morris-vm
   # Edit /home/hermes/.hermes/.env
   # Replace OPS_AZURE_CLIENT_SECRET=<old> with new value
   ```
6. **Restart affected services** on Morris's VM (cron jobs pick up env on next run)
7. **Verify** by running the health check:
   ```bash
   python3 deployment/morris/scripts/check_foundry_auth.py
   # Expected: [OK] Credential 'morris-foundry-sp-YYYY-MM' valid for N days
   ```
8. **Revoke the old secret** in Azure Portal (Certificates & secrets → Delete old entry)

## Rotation Cadence

- **Recommended:** Quarterly (every 90 days)
- **Maximum:** 6 months (180 days)
- **Alerting:** `check_foundry_auth.py` runs daily via cron, alerts at 7 days before expiry

## Monitoring

- Health check: `deployment/morris/scripts/check_foundry_auth.py`
- Alert channel: Teams DM to Mark
- Threshold: WARN at ≤7 days, CRIT at expired/missing

## Troubleshooting

| Symptom | Check |
|---------|-------|
| HTTP 500 "Failed to authenticate to backend endpoint" | SP secret expired → rotate per above |
| `check_foundry_auth.py` returns CRIT "No credentials found" | App registration has no secrets → add one |
| Graph API 401 when running health check | The health-check SP itself needs `Application.Read.All` Graph permission |
| Teams alert not sending | Verify `m365` CLI is authenticated on Morris VM |
