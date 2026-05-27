# Populate Registry From Azure

Use these commands to gather canonical values and update `hermes-registry.json`.

## Inputs

```bash
export APP_NAME="ca-hermes-agent-dev"
export RESOURCE_GROUP="rg-hermes-dev"
```

## Runtime metadata

```bash
az containerapp show -n "$APP_NAME" -g "$RESOURCE_GROUP" \
  --query '{subscription:id,resourceGroup:resourceGroup,location:location,image:properties.template.containers[0].image,envName:properties.managedEnvironmentId,fqdn:properties.configuration.ingress.fqdn,identity:identity.principalId}' -o json
```

## Log workspace link

```bash
az containerapp env show \
  --name "<aca-env-name>" \
  --resource-group "$RESOURCE_GROUP" \
  --query '{logWorkspaceCustomerId:properties.appLogsConfiguration.logAnalyticsConfiguration.customerId,destination:properties.appLogsConfiguration.destination}' -o json
```

## Key Vault secret names (references only)

```bash
az keyvault secret list --vault-name "<keyvault-name>" --query '[].name' -o tsv
```

## Update checklist

1. Update `updated_at_utc`.
2. Fill runtime fields from `az containerapp show`.
3. Fill logs workspace information.
4. Ensure all secret refs exist in `secret-catalog.json`.
5. Add a line to `changes.md` with date, actor, and scope.
6. Run `python3 scripts/validate_bot_registry.py`.
