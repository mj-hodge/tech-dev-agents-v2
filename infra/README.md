# Infrastructure Setup

One-time setup steps to wire agents to AWS, Snowflake, dbt Cloud, and Airflow.
Run these in order before starting any agent work.

## Prerequisites

- AWS account with IAM access (us-east-1)
- Snowflake account with ACCOUNTADMIN or SYSADMIN access
- dbt Cloud project and account
- Self-hosted Airflow instance with API enabled

## Setup Order

1. **AWS** — create IAM user + policy, create S3 bucket, create Snowflake storage integration role
2. **Snowflake** — run schema DDL, create storage integration, create stage
3. **AWS Secrets Manager** — populate all secrets
4. **dbt Cloud** — connect to Snowflake, create service token
5. **Airflow** — enable REST API, create agent user

---

## Step 1 — AWS IAM

Create an IAM user named `cybertronics-agents`:

```bash
aws iam create-user --user-name cybertronics-agents
aws iam put-user-policy \
  --user-name cybertronics-agents \
  --policy-name CyberTronicsAgentsPolicy \
  --policy-document file://infra/aws/iam-policy.json
aws iam create-access-key --user-name cybertronics-agents
# Save the AccessKeyId and SecretAccessKey — set them as env vars on the agent machine:
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# AWS_DEFAULT_REGION=us-east-1
```

## Step 2 — S3 Bucket

```bash
aws s3api create-bucket \
  --bucket YOUR_BUCKET_NAME \
  --region us-east-1
aws s3api put-bucket-versioning \
  --bucket YOUR_BUCKET_NAME \
  --versioning-configuration Status=Enabled
```

Replace `YOUR_BUCKET_NAME` in `config/services.yaml` and all secret values.

## Step 3 — Snowflake Schemas & Roles

Run `infra/snowflake/schema-setup.sql` as SYSADMIN against your database.
Then run `infra/snowflake/stage-setup.sql` to create the S3 external stage.

The stage setup requires the Snowflake IAM role in `infra/aws/snowflake-s3-role.json`.
Follow the comments in `stage-setup.sql` for the trust policy handshake.

## Step 4 — Populate AWS Secrets Manager

```bash
# Snowflake — generate RSA key pair first:
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out snowflake_private_key.pem
openssl rsa -in snowflake_private_key.pem -pubout -out snowflake_public_key.pem
# Register public key on dbt_user in Snowflake:
# ALTER USER dbt_user SET RSA_PUBLIC_KEY='<contents of snowflake_public_key.pem minus header/footer>';

aws secretsmanager create-secret \
  --name /cybertronics/snowflake \
  --region us-east-1 \
  --secret-string '{
    "account": "YOUR_ACCOUNT.snowflakecomputing.com",
    "user": "dbt_user",
    "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
    "warehouse": "YOUR_WAREHOUSE",
    "database": "YOUR_DATABASE",
    "role": "AGENT_TRANSFORMER"
  }'

aws secretsmanager create-secret \
  --name /cybertronics/dbt \
  --region us-east-1 \
  --secret-string '{
    "service_token": "dbt_YOUR_TOKEN",
    "account_id": "YOUR_ACCOUNT_ID",
    "project_id": "YOUR_PROJECT_ID",
    "environment_id": "YOUR_ENVIRONMENT_ID"
  }'

aws secretsmanager create-secret \
  --name /cybertronics/airflow \
  --region us-east-1 \
  --secret-string '{
    "url": "http://YOUR_AIRFLOW_HOST:8080",
    "username": "YOUR_AIRFLOW_USER",
    "password": "YOUR_AIRFLOW_PASSWORD"
  }'

aws secretsmanager create-secret \
  --name /cybertronics/aws \
  --region us-east-1 \
  --secret-string '{
    "bucket_name": "YOUR_BUCKET_NAME",
    "region": "us-east-1"
  }'
```

## Step 5 — dbt Cloud

1. Go to dbt Cloud → Account Settings → Service Tokens → create token with `Member` role on your project
2. Note your Account ID (URL: `cloud.getdbt.com/accounts/ACCOUNT_ID/`)
3. Note your Project ID and the Job IDs you want agents to trigger
4. Update `/cybertronics/dbt` secret with these values

## Step 6 — Airflow REST API

```bash
# In airflow.cfg or environment:
# [api]
# auth_backends = airflow.api.auth.backend.basic_auth

# Create an Airflow user for agents:
airflow users create \
  --username agent_user \
  --password YOUR_PASSWORD \
  --role Viewer \     # or Op if Neo needs to trigger DAGs
  --firstname Agent \
  --lastname Smith \
  --email agent@cybertronics.local
```

## Step 7 — Verify

```bash
python -c "from tools.secrets import get_secret; print(get_secret('/cybertronics/aws'))"
python -c "from tools.snowflake_client import get_connection; c = get_connection(); print('Snowflake OK')"
python -c "from tools.dbt_client import DbtCloudClient; c = DbtCloudClient(); print('dbt Cloud OK')"
python -c "from tools.airflow_client import AirflowClient; c = AirflowClient(); print(c.get_health())"
```
