# Dan Bot — Cost Tracking

## Azure OpenAI (gpt5chat)

Model calls go through Azure AI Foundry. Track usage in the Azure portal:

- **Token metrics**: Azure portal → AI Services resource → Monitoring → Metrics → "Tokens Processed"
- **Cost analysis**: Azure portal → Cost Management → Cost analysis → filter by resource group `rg-tech-dev-agents-dev`
- **Budget alerts**: Azure portal → Cost Management → Cost alerts → create a budget scoped to `rg-tech-dev-agents-dev`

Direct link (subscription-scoped):
`https://portal.azure.com/#@gorillacommerce.co/resource/subscriptions/d0f0feff-78ef-4425-9d51-07f5e0f0bcba/costanalysis`

## Claude Code (Anthropic)

Dan uses a `team` subscription authenticated as `tech-agent-dan@gorillacommerce.co`.

- **Usage dashboard**: https://console.anthropic.com/settings/billing
- Log in as `tech-agent-dan@gorillacommerce.co` to see Claude usage

## Notes

- Hermes itself has no built-in billing console — costs come from the upstream API providers
- Azure OpenAI charges per token (prompt + completion)
- Claude Code charges per token under the team plan
- The deployment model is `gpt-5-chat-2025-10-03` aliased as `gpt5chat`
