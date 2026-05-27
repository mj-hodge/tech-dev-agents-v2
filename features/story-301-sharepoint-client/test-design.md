# STORY-301: SharePoint Client — Test Design (Phase 7)

**Date:** 2026-04-15
**Status:** GREEN (19/19 passing)

## Test File

`tests/test_sharepoint_client.py`

## Test Summary

| Category | Count | Status |
|----------|-------|--------|
| Credential loading | 4 | GREEN |
| Token acquisition | 4 | GREEN |
| Search | 2 | GREEN |
| Read (download + extract) | 3 | GREEN |
| Folder operations | 3 | GREEN |
| Content extraction | 2 | GREEN |
| Date field contract | 1 | GREEN |
| **Total unit tests** | **19** | **GREEN** |
| Integration (optional) | 1 | Skipped (no credentials) |

## Test Categories

### 1. Credential Loading (4 tests)
- Key Vault loaded first when available
- Env var fallback when Key Vault fails
- File fallback when Key Vault and env var absent
- CredentialLoadError when all sources fail

### 2. Token Acquisition (4 tests)
- Successful client_credentials token acquisition
- Token cached within expiry window (single HTTP call)
- Expired token triggers new request
- Auth failure raises GraphAPIError

### 3. Search (2 tests)
- Search returns items with created/modified dates
- Empty result set handled

### 4. Read (3 tests)
- Read returns document with dates and extracted text
- 404 raises GraphAPIError
- Unsupported format returns extraction_error (dates still present)

### 5. Folder Operations (3 tests)
- list_folder returns items with dates
- Distinguishes files vs folders
- search_folders returns folder items with dates

### 6. Content Extraction (2 tests)
- Plain text extraction
- Unsupported format returns None

### 7. Date Field Contract (1 test)
- All search results have both date fields (stale-source detection)

### 8. Integration (1 test, optional)
- Real Graph search with date field assertion
- Skipped when credentials unavailable

## Key Design Decisions

- All Graph HTTP calls are mocked (no network in unit tests)
- Date field presence is asserted in EVERY test category that returns items
- Integration test is marked `@pytest.mark.integration` and auto-skips
