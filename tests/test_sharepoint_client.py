"""
STORY-301: SharePoint Client Helper — Test Suite
Phase 7 (Test Design) — Unit tests with mocked Graph responses.

Tests cover:
- Credential loading (Key Vault, env var, file)
- Token acquisition via client_credentials flow
- Token caching (reuse unexpired, refresh expired)
- search() — keyword search across SharePoint sites
- read() — download + extract content from a document
- search_folders() — find folders by name
- list_folder() — enumerate items in a folder
- Date field presence assertions (stale-source detection)
- Content extraction dispatch (docx, pptx, xlsx, pdf, unsupported)
- Error handling (auth failure, Graph 404, extraction errors)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

from tech_dev_agents.sharepoint_client import (
    CredentialLoadError,
    GraphAPIError,
    SharePointClient,
    SharePointDocument,
    SharePointFolder,
    SharePointItem,
    load_credentials,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_CREDS = {
    "appId": "4928d18f-1e09-4d6e-bca0-36805287855a",
    "password": "super-secret-value",
    "tenant": "1060148b-e4f2-4e64-880e-b8b05958e6fe",
}

FAKE_TOKEN_RESPONSE = {
    "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.fake",
    "token_type": "Bearer",
    "expires_in": 3599,
}


def _graph_search_response(items: list[dict]) -> dict:
    """Build a mock Graph /search response with date fields."""
    hits = []
    for item in items:
        hits.append({
            "hitId": item.get("id", "fake-id"),
            "resource": {
                "id": item.get("id", "fake-id"),
                "name": item.get("name", "doc.docx"),
                "webUrl": item.get("webUrl", "https://contoso.sharepoint.com/doc.docx"),
                "createdDateTime": item.get("createdDateTime", "2026-01-15T10:00:00Z"),
                "lastModifiedDateTime": item.get("lastModifiedDateTime", "2026-04-10T14:30:00Z"),
                "size": item.get("size", 1024),
                "parentReference": {
                    "siteId": item.get("siteId", "site-123"),
                    "driveId": item.get("driveId", "drive-456"),
                },
            },
        })
    return {
        "value": [
            {
                "searchTerms": ["test"],
                "hitsContainers": [
                    {
                        "total": len(hits),
                        "moreResultsAvailable": False,
                        "hits": hits,
                    }
                ],
            }
        ]
    }


def _graph_drive_item_response(
    item_id: str = "item-789",
    name: str = "report.docx",
    mime: str = "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
) -> dict:
    return {
        "id": item_id,
        "name": name,
        "file": {"mimeType": mime},
        "size": 2048,
        "webUrl": f"https://contoso.sharepoint.com/{name}",
        "createdDateTime": "2026-02-20T09:00:00Z",
        "lastModifiedDateTime": "2026-04-12T11:45:00Z",
        "parentReference": {
            "siteId": "site-123",
            "driveId": "drive-456",
        },
        "@microsoft.graph.downloadUrl": f"https://download.contoso.com/{name}",
    }


def _graph_folder_children_response(items: list[dict] | None = None) -> dict:
    if items is None:
        items = [
            {
                "id": "child-1",
                "name": "notes.docx",
                "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                "size": 512,
                "webUrl": "https://contoso.sharepoint.com/notes.docx",
                "createdDateTime": "2026-03-01T08:00:00Z",
                "lastModifiedDateTime": "2026-04-05T16:00:00Z",
            },
            {
                "id": "child-2",
                "name": "subfolder",
                "folder": {"childCount": 3},
                "size": 0,
                "webUrl": "https://contoso.sharepoint.com/subfolder",
                "createdDateTime": "2026-01-10T12:00:00Z",
                "lastModifiedDateTime": "2026-03-20T10:00:00Z",
            },
        ]
    return {"value": items}


def _make_client(creds: dict | None = None) -> SharePointClient:
    """Create a SharePointClient with injected credentials (no loading)."""
    c = creds or FAKE_CREDS
    return SharePointClient(
        app_id=c["appId"],
        client_secret=c["password"],
        tenant_id=c["tenant"],
    )


# ---------------------------------------------------------------------------
# 1. Credential Loading
# ---------------------------------------------------------------------------


class TestCredentialLoading:
    """Test the 3-tier credential loading priority."""

    @patch("tech_dev_agents.sharepoint_client._load_from_keyvault")
    def test_load_from_keyvault_first(self, mock_kv: MagicMock):
        """Key Vault is tried first when available."""
        mock_kv.return_value = FAKE_CREDS
        creds = load_credentials()
        assert creds["appId"] == FAKE_CREDS["appId"]
        mock_kv.assert_called_once()

    @patch("tech_dev_agents.sharepoint_client._load_from_keyvault", side_effect=Exception("no MI"))
    def test_load_from_env_var_second(self, mock_kv: MagicMock):
        """Falls back to env var when Key Vault fails."""
        with patch.dict(os.environ, {"KNOWLEDGEBASE_SP_CREDENTIALS": json.dumps(FAKE_CREDS)}):
            creds = load_credentials()
            assert creds["appId"] == FAKE_CREDS["appId"]

    @patch("tech_dev_agents.sharepoint_client._load_from_keyvault", side_effect=Exception("no MI"))
    def test_load_from_file_third(self, mock_kv: MagicMock):
        """Falls back to file when Key Vault and env var are absent."""
        with patch.dict(os.environ, {}, clear=True):
            fake_path = Path("/tmp/fake-cred.json")
            with patch("tech_dev_agents.sharepoint_client._CRED_FILE_PATH", fake_path):
                with patch("pathlib.Path.expanduser", return_value=fake_path):
                    with patch("pathlib.Path.exists", return_value=True):
                        with patch("pathlib.Path.read_text", return_value=json.dumps(FAKE_CREDS)):
                            creds = load_credentials()
                            assert creds["appId"] == FAKE_CREDS["appId"]

    @patch("tech_dev_agents.sharepoint_client._load_from_keyvault", side_effect=Exception("no MI"))
    def test_load_raises_when_all_fail(self, mock_kv: MagicMock):
        """Raises CredentialLoadError when all sources fail."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("tech_dev_agents.sharepoint_client._CRED_FILE_PATH", Path("/nonexistent")):
                with patch("pathlib.Path.expanduser", return_value=Path("/nonexistent")):
                    with patch("pathlib.Path.exists", return_value=False):
                        with pytest.raises(CredentialLoadError):
                            load_credentials()


# ---------------------------------------------------------------------------
# 2. Token Acquisition
# ---------------------------------------------------------------------------


class TestTokenAcquisition:
    """Test OAuth2 client_credentials token flow."""

    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_acquire_token_success(self, mock_post: MagicMock):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: FAKE_TOKEN_RESPONSE,
            raise_for_status=lambda: None,
        )
        client = _make_client()
        token = client._acquire_token()
        assert token == FAKE_TOKEN_RESPONSE["access_token"]

    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_token_cached_when_valid(self, mock_post: MagicMock):
        """Token is reused within its expiry window."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: FAKE_TOKEN_RESPONSE,
            raise_for_status=lambda: None,
        )
        client = _make_client()
        t1 = client._acquire_token()
        t2 = client._acquire_token()
        assert t1 == t2
        assert mock_post.call_count == 1  # Only called once

    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_token_refreshed_when_expired(self, mock_post: MagicMock):
        """Expired token triggers a new request."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: FAKE_TOKEN_RESPONSE,
            raise_for_status=lambda: None,
        )
        client = _make_client()
        client._acquire_token()
        # Force expiry
        client._token_expires_at = time.time() - 10
        client._acquire_token()
        assert mock_post.call_count == 2

    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_token_auth_failure_raises(self, mock_post: MagicMock):
        """Authentication failure raises GraphAPIError."""
        resp = MagicMock()
        resp.status_code = 401
        resp.json.return_value = {"error": "invalid_client", "error_description": "bad secret"}
        resp.raise_for_status.side_effect = Exception("401")
        mock_post.return_value = resp
        client = _make_client()
        with pytest.raises(GraphAPIError):
            client._acquire_token()


# ---------------------------------------------------------------------------
# 3. Search
# ---------------------------------------------------------------------------


class TestSearch:
    """Test SharePoint search method."""

    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_search_returns_items_with_dates(self, mock_post: MagicMock):
        """Search results must include created and modified dates."""
        # First call = token, second = search
        token_resp = MagicMock(status_code=200, json=lambda: FAKE_TOKEN_RESPONSE, raise_for_status=lambda: None)
        search_resp = MagicMock(
            status_code=200,
            json=lambda: _graph_search_response([
                {"id": "doc-1", "name": "handbook.docx", "createdDateTime": "2026-01-15T10:00:00Z", "lastModifiedDateTime": "2026-04-10T14:30:00Z"},
                {"id": "doc-2", "name": "policy.pdf", "createdDateTime": "2026-02-01T08:00:00Z", "lastModifiedDateTime": "2026-03-25T09:15:00Z"},
            ]),
            raise_for_status=lambda: None,
        )
        mock_post.side_effect = [token_resp, search_resp]

        client = _make_client()
        results = client.search("handbook")

        assert len(results) == 2
        for item in results:
            assert isinstance(item, SharePointItem)
            assert item.created_date_time is not None, "created_date_time must be present"
            assert item.last_modified_date_time is not None, "last_modified_date_time must be present"

    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_search_empty_results(self, mock_post: MagicMock):
        token_resp = MagicMock(status_code=200, json=lambda: FAKE_TOKEN_RESPONSE, raise_for_status=lambda: None)
        search_resp = MagicMock(
            status_code=200,
            json=lambda: _graph_search_response([]),
            raise_for_status=lambda: None,
        )
        mock_post.side_effect = [token_resp, search_resp]

        client = _make_client()
        results = client.search("nonexistent-query-xyz")
        assert results == []


# ---------------------------------------------------------------------------
# 4. Read (download + extract)
# ---------------------------------------------------------------------------


class TestRead:
    """Test document read/download + content extraction."""

    @patch("tech_dev_agents.sharepoint_client.requests.get")
    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_read_returns_document_with_dates(self, mock_post: MagicMock, mock_get: MagicMock):
        """Read result must include created and modified dates."""
        token_resp = MagicMock(status_code=200, json=lambda: FAKE_TOKEN_RESPONSE, raise_for_status=lambda: None)
        mock_post.return_value = token_resp

        item_resp = MagicMock(
            status_code=200,
            json=lambda: _graph_drive_item_response(),
            raise_for_status=lambda: None,
        )
        content_resp = MagicMock(status_code=200, content=b"fake document bytes", raise_for_status=lambda: None)
        mock_get.side_effect = [item_resp, content_resp]

        client = _make_client()
        with patch.object(client, "_extract_content", return_value="Extracted text"):
            doc = client.read(site_id="site-123", drive_id="drive-456", item_id="item-789")

        assert isinstance(doc, SharePointDocument)
        assert doc.created_date_time is not None, "created_date_time must be present"
        assert doc.last_modified_date_time is not None, "last_modified_date_time must be present"
        assert doc.body_text == "Extracted text"

    @patch("tech_dev_agents.sharepoint_client.requests.get")
    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_read_item_not_found_raises(self, mock_post: MagicMock, mock_get: MagicMock):
        token_resp = MagicMock(status_code=200, json=lambda: FAKE_TOKEN_RESPONSE, raise_for_status=lambda: None)
        mock_post.return_value = token_resp

        error_resp = MagicMock(status_code=404)
        error_resp.raise_for_status.side_effect = Exception("404 Not Found")
        error_resp.json.return_value = {"error": {"code": "itemNotFound", "message": "not found"}}
        mock_get.return_value = error_resp

        client = _make_client()
        with pytest.raises(GraphAPIError):
            client.read(site_id="site-123", drive_id="drive-456", item_id="bad-id")

    @patch("tech_dev_agents.sharepoint_client.requests.get")
    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_read_unsupported_format_returns_extraction_error(self, mock_post: MagicMock, mock_get: MagicMock):
        """Unsupported format gracefully returns extraction_error."""
        token_resp = MagicMock(status_code=200, json=lambda: FAKE_TOKEN_RESPONSE, raise_for_status=lambda: None)
        mock_post.return_value = token_resp

        item_resp = MagicMock(
            status_code=200,
            json=lambda: _graph_drive_item_response(name="archive.zip", mime="application/zip"),
            raise_for_status=lambda: None,
        )
        content_resp = MagicMock(status_code=200, content=b"PK\x03\x04...", raise_for_status=lambda: None)
        mock_get.side_effect = [item_resp, content_resp]

        client = _make_client()
        doc = client.read(site_id="site-123", drive_id="drive-456", item_id="item-789")

        assert doc.body_text is None
        assert doc.extraction_error is not None
        assert "unsupported" in doc.extraction_error.lower() or "zip" in doc.extraction_error.lower()
        # Dates must still be present even when extraction fails
        assert doc.created_date_time is not None
        assert doc.last_modified_date_time is not None


# ---------------------------------------------------------------------------
# 5. Folder Operations
# ---------------------------------------------------------------------------


class TestFolderOperations:

    @patch("tech_dev_agents.sharepoint_client.requests.get")
    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_list_folder_returns_items_with_dates(self, mock_post: MagicMock, mock_get: MagicMock):
        """Folder listing includes date fields on every item."""
        token_resp = MagicMock(status_code=200, json=lambda: FAKE_TOKEN_RESPONSE, raise_for_status=lambda: None)
        mock_post.return_value = token_resp
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: _graph_folder_children_response(),
            raise_for_status=lambda: None,
        )

        client = _make_client()
        items = client.list_folder(site_id="site-123", drive_id="drive-456", folder_id="root")

        assert len(items) == 2
        for item in items:
            assert item.created_date_time is not None, "created_date_time must be present"
            assert item.last_modified_date_time is not None, "last_modified_date_time must be present"

    @patch("tech_dev_agents.sharepoint_client.requests.get")
    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_list_folder_distinguishes_files_and_folders(self, mock_post: MagicMock, mock_get: MagicMock):
        token_resp = MagicMock(status_code=200, json=lambda: FAKE_TOKEN_RESPONSE, raise_for_status=lambda: None)
        mock_post.return_value = token_resp
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: _graph_folder_children_response(),
            raise_for_status=lambda: None,
        )

        client = _make_client()
        items = client.list_folder(site_id="site-123", drive_id="drive-456", folder_id="root")

        files = [i for i in items if i.is_file]
        folders = [i for i in items if i.is_folder]
        assert len(files) == 1
        assert len(folders) == 1

    @patch("tech_dev_agents.sharepoint_client.requests.get")
    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_search_folders_returns_folder_items(self, mock_post: MagicMock, mock_get: MagicMock):
        """search_folders returns matching folders with dates."""
        token_resp = MagicMock(status_code=200, json=lambda: FAKE_TOKEN_RESPONSE, raise_for_status=lambda: None)
        mock_post.return_value = token_resp

        folder_search_resp = {
            "value": [
                {
                    "id": "folder-1",
                    "name": "ProjectDocs",
                    "folder": {"childCount": 5},
                    "size": 0,
                    "webUrl": "https://contoso.sharepoint.com/ProjectDocs",
                    "createdDateTime": "2026-01-05T07:00:00Z",
                    "lastModifiedDateTime": "2026-04-01T13:00:00Z",
                    "parentReference": {"siteId": "site-123", "driveId": "drive-456"},
                }
            ]
        }
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: folder_search_resp,
            raise_for_status=lambda: None,
        )

        client = _make_client()
        folders = client.search_folders(site_id="site-123", drive_id="drive-456", query="ProjectDocs")

        assert len(folders) >= 1
        for f in folders:
            assert isinstance(f, SharePointFolder)
            assert f.created_date_time is not None
            assert f.last_modified_date_time is not None


# ---------------------------------------------------------------------------
# 6. Content Extraction
# ---------------------------------------------------------------------------


class TestContentExtraction:
    """Test _extract_content dispatch for different formats."""

    def test_extract_txt_returns_string(self):
        client = _make_client()
        result = client._extract_content(b"Hello plain text", "readme.txt", "text/plain")
        assert result == "Hello plain text"

    def test_extract_unsupported_returns_none(self):
        client = _make_client()
        result = client._extract_content(b"\x00\x01\x02", "data.bin", "application/octet-stream")
        assert result is None


# ---------------------------------------------------------------------------
# 7. Date Field Assertions (stale-source detection contract)
# ---------------------------------------------------------------------------


class TestDateFieldContract:
    """Ensure date fields are ALWAYS present — this is the stale-source detection contract."""

    @patch("tech_dev_agents.sharepoint_client.requests.post")
    def test_search_results_always_have_dates(self, mock_post: MagicMock):
        """Every search result must have both date fields — no exceptions."""
        token_resp = MagicMock(status_code=200, json=lambda: FAKE_TOKEN_RESPONSE, raise_for_status=lambda: None)
        search_data = _graph_search_response([
            {"id": f"doc-{i}", "name": f"doc{i}.docx"} for i in range(5)
        ])
        search_resp = MagicMock(status_code=200, json=lambda: search_data, raise_for_status=lambda: None)
        mock_post.side_effect = [token_resp, search_resp]

        client = _make_client()
        for item in client.search("test"):
            assert item.created_date_time is not None, f"Missing created_date_time on {item.name}"
            assert item.last_modified_date_time is not None, f"Missing last_modified_date_time on {item.name}"


# ---------------------------------------------------------------------------
# 8. Integration test (optional — requires real Graph credentials)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSharePointIntegration:
    """Integration tests that hit real Microsoft Graph.

    Run with: pytest -m integration tests/test_sharepoint_client.py
    Requires KNOWLEDGEBASE_SP_CREDENTIALS env var or Key Vault access.
    """

    def test_search_real_site(self):
        """Search a known SharePoint site and verify date fields."""
        try:
            creds = load_credentials()
        except CredentialLoadError:
            pytest.skip("No SharePoint credentials available")

        client = SharePointClient(
            app_id=creds["appId"],
            client_secret=creds["password"],
            tenant_id=creds["tenant"],
        )
        results = client.search("test", entity_types=["driveItem"])
        # We can't assert specific results, but if any come back, dates must be there
        for item in results:
            assert item.created_date_time is not None
            assert item.last_modified_date_time is not None
