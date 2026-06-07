-- =============================================================================
-- Snowflake S3 External Stage Setup
-- Run AFTER schema-setup.sql and AFTER creating the Snowflake storage
-- integration IAM role in AWS (infra/aws/snowflake-s3-role.json)
-- Replace placeholders before running
-- =============================================================================

USE ROLE ACCOUNTADMIN;
USE DATABASE YOUR_DATABASE;
USE SCHEMA RAW;

-- -----------------------------------------------------------------------------
-- Step 1: Create the storage integration
-- Snowflake assumes the IAM role to access S3
-- -----------------------------------------------------------------------------

CREATE STORAGE INTEGRATION IF NOT EXISTS s3_cybertronics_integration
  TYPE                      = EXTERNAL_STAGE
  STORAGE_PROVIDER          = 'S3'
  ENABLED                   = TRUE
  STORAGE_AWS_ROLE_ARN      = 'arn:aws:iam::YOUR_AWS_ACCOUNT_ID:role/snowflake-s3-integration-role'
  STORAGE_ALLOWED_LOCATIONS = ('s3://YOUR_BUCKET_NAME/');

-- -----------------------------------------------------------------------------
-- Step 2: Get the AWS values Snowflake generated
-- Copy STORAGE_AWS_IAM_USER_ARN and STORAGE_AWS_EXTERNAL_ID from this output
-- and paste them into infra/aws/snowflake-s3-role.json trust policy, then
-- update the IAM role in AWS
-- -----------------------------------------------------------------------------

DESC INTEGRATION s3_cybertronics_integration;

-- -----------------------------------------------------------------------------
-- Step 3: Grant the integration to AGENT_LOADER role
-- -----------------------------------------------------------------------------

USE ROLE SYSADMIN;
GRANT USAGE ON INTEGRATION s3_cybertronics_integration TO ROLE AGENT_LOADER;

-- -----------------------------------------------------------------------------
-- Step 4: Create external stages (one per data area)
-- -----------------------------------------------------------------------------

USE ROLE AGENT_LOADER;
USE SCHEMA RAW;

-- Generic CSV stage for raw file loads
CREATE STAGE IF NOT EXISTS s3_raw_csv
  URL                 = 's3://YOUR_BUCKET_NAME/raw/'
  STORAGE_INTEGRATION = s3_cybertronics_integration
  FILE_FORMAT         = (
    TYPE                        = 'CSV'
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    SKIP_HEADER                 = 1
    NULL_IF                     = ('NULL', 'null', '')
    EMPTY_FIELD_AS_NULL         = TRUE
    DATE_FORMAT                 = 'AUTO'
    TIMESTAMP_FORMAT            = 'AUTO'
  )
  COMMENT = 'External stage for raw CSV files from S3 data lake';

-- Parquet stage for columnar files
CREATE STAGE IF NOT EXISTS s3_raw_parquet
  URL                 = 's3://YOUR_BUCKET_NAME/raw/'
  STORAGE_INTEGRATION = s3_cybertronics_integration
  FILE_FORMAT         = (
    TYPE = 'PARQUET'
  )
  COMMENT = 'External stage for raw Parquet files from S3 data lake';

-- Verify stages
SHOW STAGES IN SCHEMA RAW;

-- -----------------------------------------------------------------------------
-- Step 5: Test the stage (run after populating S3 with a test file)
-- -----------------------------------------------------------------------------

-- LIST @s3_raw_csv;
-- SELECT $1, $2, $3 FROM @s3_raw_csv/test.csv LIMIT 10;
