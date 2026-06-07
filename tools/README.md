# Agent Tool Clients

Python clients for every external service the agents interact with.
All credentials are pulled from AWS Secrets Manager at runtime — nothing is hardcoded.

## Prerequisites

Set these environment variables on the agent machine before running anything:

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
```

## Modules

| Module | Service | Secret Path |
|--------|---------|-------------|
| `secrets.py` | AWS Secrets Manager | *(root — no secret needed)* |
| `s3_client.py` | AWS S3 | `/cybertronics/aws` |
| `snowflake_client.py` | Snowflake | `/cybertronics/snowflake` |
| `dbt_client.py` | dbt Cloud REST API | `/cybertronics/dbt` |
| `airflow_client.py` | Airflow REST API v1 | `/cybertronics/airflow` |

## Quick Examples

```python
# Pull any secret
from tools.secrets import get_secret
creds = get_secret('/cybertronics/snowflake')

# Upload a file to S3
from tools.s3_client import upload_file
uri = upload_file('local/data.csv', 'raw/orders/2026/06/06/data.csv')

# Run a SQL query in Snowflake
from tools.snowflake_client import execute, ROLE_TRANSFORMER
rows = execute("SELECT COUNT(*) FROM RAW.my_table", role=ROLE_TRANSFORMER)

# Load S3 files into a Snowflake raw table
from tools.snowflake_client import copy_from_stage
result = copy_from_stage(
    table='RAW.orders',
    stage='@RAW.s3_raw_csv/raw/orders/2026/06/06/',
)
print(result)  # {'files_loaded': 3, 'rows_loaded': 15000, 'errors': []}

# Trigger a dbt Cloud job and wait for it
from tools.dbt_client import DbtCloudClient
dbt = DbtCloudClient()
run_id, status = dbt.trigger_and_wait(job_id=12345, cause="Neo: STORY-042")
print(status)  # 'success' or 'error'

# Trigger an Airflow DAG and wait
from tools.airflow_client import AirflowClient
airflow = AirflowClient()
run_id, state = airflow.trigger_and_wait(
    dag_id='s3_to_snowflake_ingestion',
    conf={'source': 'orders', 'date': '2026-06-06'}
)
print(state)  # 'success' or 'failed'
```

## Data Flow

```
S3 raw/          →  Snowflake RAW schema  →  dbt STAGING  →  INTERMEDIATE  →  MARTS
s3_client.py        snowflake_client.py       dbt_client.py triggers dbt Cloud job
upload_file()       copy_from_stage()         trigger_and_wait(job_id)
```

Airflow orchestrates the full sequence. Neo writes DAGs; Skynet monitors them.

## Agent Access Matrix

| Agent | s3_client | snowflake_client | dbt_client | airflow_client |
|-------|-----------|-----------------|------------|----------------|
| The Architect | read (schema discovery) | read (metadata) | read (project info) | read (DAG list) |
| Neo | read + write | TRANSFORMER role | trigger + wait | trigger + wait |
| Morpheus | — | — | — | — |
| Agent Smith | read | ANALYST role | read (test results) | read (run status) |
| Skynet | — | — | — | read + health check |
