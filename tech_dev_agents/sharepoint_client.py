"""SharePoint Client Helper for Autonomous Agents (STORY-301).

Provides read-only access to SharePoint document libraries via Microsoft Graph API
using application (client_credentials) authentication with the
``tech-dev-agents-knowledgebase`` service principal.

Key capabilities:
- search(): keyword search across SharePoint sites
- read(): download + extract content from a document
- search_folders(): find folders by name
- list_folder(): enumerate items in a folder

Every response includes ``created_date_time`` and ``last_modified_date_time``
for stale-source detection (durable contract — asserted in tests).

Credential loading priority:
1. Azure Key Vault ``kv-tech-dev-agents-dev`` (secret ``knowledgebase-sp-credentials``)
2. Environment variable ``KNOWLEDGEBASE_SP_CREDENTIALS``
3. File ``~/.agent-ops/knowledgebase-sp-secret.json``
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"
_TOKEN_BUFFER_SECONDS = 300  # Refresh 5 min before expiry (~55 min lifetime)

_KEYVAULT_NAME = "kv-tech-dev-agents-dev"
_KEYVAULT_SECRET_NAME = "knowledgebase-sp-credentials"
_CRED_ENV_VAR = "KNOWLEDGEBASE_SP_CREDENTIALS"
_CRED_FILE_PATH = Path("~/.agent-ops/knowledgebase-sp-secret.json")

# MIME type families we can extract text from
_TEXT_MIMES = {"text/plain", "text/markdown", "text/csv", "text/html"}
_DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
_PPTX_MIMES = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
}
_XLSX_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}
_PDF_MIMES = {"application/pdf"}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CredentialLoadError(Exception):
    """Raised when credentials cannot be loaded from any source."""


class GraphAPIError(Exception):
    """Raised on Microsoft Graph API errors."""

    def __init__(self, message: str, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SharePointItem:
    """A file or folder item from SharePoint."""

    id: str
    name: str
    web_url: str
    created_date_time: str
    last_modified_date_time: str
    size: int
    site_id: str | None = None
    drive_id: str | None = None
    is_file: bool = True
    is_folder: bool = False
    mime_type: str | None = None
    child_count: int | None = None


@dataclass(frozen=True)
class SharePointFolder:
    """A folder in SharePoint."""

    id: str
    name: str
    web_url: str
    created_date_time: str
    last_modified_date_time: str
    site_id: str | None = None
    drive_id: str | None = None
    child_count: int = 0


@dataclass(frozen=True)
class SharePointDocument:
    """A downloaded SharePoint document with optional extracted text."""

    id: str
    name: str
    web_url: str
    created_date_time: str
    last_modified_date_time: str
    size: int
    mime_type: str | None = None
    body_text: str | None = None
    extraction_error: str | None = None
    site_id: str | None = None
    drive_id: str | None = None


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------


def _load_from_keyvault() -> dict[str, str]:
    """Load credentials from Azure Key Vault using DefaultAzureCredential."""
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    credential = DefaultAzureCredential()
    vault_url = f"https://{_KEYVAULT_NAME}.vault.azure.net"
    client = SecretClient(vault_url=vault_url, credential=credential)
    secret = client.get_secret(_KEYVAULT_SECRET_NAME)
    return json.loads(secret.value)


def load_credentials() -> dict[str, str]:
    """Load SharePoint SPN credentials with 3-tier priority.

    Returns:
        Dict with keys: ``appId``, ``password``, ``tenant``.

    Raises:
        CredentialLoadError: When all credential sources fail.
    """
    # 1. Try Key Vault
    try:
        creds = _load_from_keyvault()
        logger.info("Loaded credentials from Key Vault (%s/%s)", _KEYVAULT_NAME, _KEYVAULT_SECRET_NAME)
        return creds
    except Exception as exc:
        logger.debug("Key Vault credential load failed: %s", exc)

    # 2. Try environment variable
    env_val = os.environ.get(_CRED_ENV_VAR)
    if env_val:
        try:
            creds = json.loads(env_val)
            logger.info("Loaded credentials from env var %s", _CRED_ENV_VAR)
            return creds
        except json.JSONDecodeError as exc:
            logger.warning("Invalid JSON in %s: %s", _CRED_ENV_VAR, exc)

    # 3. Try file
    cred_path = _CRED_FILE_PATH.expanduser()
    if cred_path.exists():
        try:
            creds = json.loads(cred_path.read_text())
            logger.info("Loaded credentials from %s", cred_path)
            return creds
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load credentials from %s: %s", cred_path, exc)

    raise CredentialLoadError(
        "Could not load SharePoint credentials from Key Vault, "
        f"env var {_CRED_ENV_VAR}, or file {_CRED_FILE_PATH}"
    )


# ---------------------------------------------------------------------------
# SharePointClient
# ---------------------------------------------------------------------------


class SharePointClient:
    """Read-only SharePoint client using Microsoft Graph API.

    Args:
        app_id: Azure AD application (client) ID.
        client_secret: Client secret value.
        tenant_id: Azure AD tenant ID.
    """

    def __init__(self, app_id: str, client_secret: str, tenant_id: str) -> None:
        self._app_id = app_id
        self._client_secret = client_secret
        self._tenant_id = tenant_id
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    # -- Token management --------------------------------------------------

    def _acquire_token(self) -> str:
        """Acquire or return cached OAuth2 access token."""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        token_url = _TOKEN_URL_TEMPLATE.format(tenant=self._tenant_id)
        try:
            resp = requests.post(
                token_url,
                data={
                    "client_id": self._app_id,
                    "client_secret": self._client_secret,
                    "scope": _DEFAULT_SCOPE,
                    "grant_type": "client_credentials",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise GraphAPIError(
                f"Token acquisition failed: {exc}",
                status_code=getattr(getattr(exc, "response", None), "status_code", None),
            ) from exc

        data = resp.json()
        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._token_expires_at = time.time() + expires_in - _TOKEN_BUFFER_SECONDS
        logger.info("Acquired Graph token (expires in %ds)", expires_in)
        return self._access_token

    def _headers(self) -> dict[str, str]:
        """Build authorized request headers."""
        return {
            "Authorization": f"Bearer {self._acquire_token()}",
            "Content-Type": "application/json",
        }

    # -- Search ------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        entity_types: list[str] | None = None,
        size: int = 25,
    ) -> list[SharePointItem]:
        """Search SharePoint for documents matching a keyword query.

        Args:
            query: Search keywords.
            entity_types: Graph search entity types (default: ``["driveItem"]``).
            size: Max results to return.

        Returns:
            List of SharePointItem with date fields always populated.
        """
        if entity_types is None:
            entity_types = ["driveItem"]

        payload = {
            "requests": [
                {
                    "entityTypes": entity_types,
                    "query": {"queryString": query},
                    "size": size,
                }
            ]
        }

        resp = requests.post(
            f"{_GRAPH_BASE}/search/query",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        results: list[SharePointItem] = []
        for result_set in data.get("value", []):
            for container in result_set.get("hitsContainers", []):
                for hit in container.get("hits", []):
                    resource = hit.get("resource", {})
                    parent_ref = resource.get("parentReference", {})
                    results.append(
                        SharePointItem(
                            id=resource.get("id", ""),
                            name=resource.get("name", ""),
                            web_url=resource.get("webUrl", ""),
                            created_date_time=resource.get("createdDateTime", ""),
                            last_modified_date_time=resource.get("lastModifiedDateTime", ""),
                            size=resource.get("size", 0),
                            site_id=parent_ref.get("siteId"),
                            drive_id=parent_ref.get("driveId"),
                        )
                    )

        return results

    # -- Read (download + extract) -----------------------------------------

    def read(
        self,
        *,
        site_id: str,
        drive_id: str,
        item_id: str,
    ) -> SharePointDocument:
        """Download a document and extract its text content.

        Args:
            site_id: SharePoint site ID.
            drive_id: Drive ID containing the item.
            item_id: Drive item ID.

        Returns:
            SharePointDocument with metadata, dates, and optional extracted text.

        Raises:
            GraphAPIError: On Graph API errors (e.g., 404).
        """
        # Get item metadata
        item_url = f"{_GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/items/{item_id}"
        try:
            meta_resp = requests.get(item_url, headers=self._headers(), timeout=30)
            meta_resp.raise_for_status()
        except Exception as exc:
            raise GraphAPIError(
                f"Failed to get item metadata: {exc}",
                status_code=getattr(getattr(exc, "response", None), "status_code", None),
            ) from exc

        meta = meta_resp.json()
        name = meta.get("name", "")
        mime_type = meta.get("file", {}).get("mimeType", "")
        download_url = meta.get("@microsoft.graph.downloadUrl", "")

        # Download content
        body_text: str | None = None
        extraction_error: str | None = None

        if download_url:
            try:
                content_resp = requests.get(download_url, timeout=60)
                content_resp.raise_for_status()
                raw_bytes = content_resp.content

                try:
                    extracted = self._extract_content(raw_bytes, name, mime_type)
                    if extracted is not None:
                        body_text = extracted
                    else:
                        extraction_error = f"Unsupported format: {mime_type or name}"
                except Exception as exc:
                    extraction_error = f"Extraction failed for {name}: {exc}"
                    logger.warning(extraction_error)
            except Exception as exc:
                extraction_error = f"Download failed: {exc}"
                logger.warning(extraction_error)

        return SharePointDocument(
            id=meta.get("id", ""),
            name=name,
            web_url=meta.get("webUrl", ""),
            created_date_time=meta.get("createdDateTime", ""),
            last_modified_date_time=meta.get("lastModifiedDateTime", ""),
            size=meta.get("size", 0),
            mime_type=mime_type,
            body_text=body_text,
            extraction_error=extraction_error,
            site_id=site_id,
            drive_id=drive_id,
        )

    # -- Folder operations -------------------------------------------------

    def list_folder(
        self,
        *,
        site_id: str,
        drive_id: str,
        folder_id: str,
    ) -> list[SharePointItem]:
        """List items in a SharePoint folder.

        Args:
            site_id: SharePoint site ID.
            drive_id: Drive ID.
            folder_id: Folder item ID (or "root" for drive root).

        Returns:
            List of SharePointItem with date fields always populated.
        """
        url = f"{_GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/items/{folder_id}/children"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()

        items: list[SharePointItem] = []
        for child in data.get("value", []):
            is_folder = "folder" in child
            is_file = "file" in child
            parent_ref = child.get("parentReference", {})
            items.append(
                SharePointItem(
                    id=child.get("id", ""),
                    name=child.get("name", ""),
                    web_url=child.get("webUrl", ""),
                    created_date_time=child.get("createdDateTime", ""),
                    last_modified_date_time=child.get("lastModifiedDateTime", ""),
                    size=child.get("size", 0),
                    site_id=parent_ref.get("siteId", site_id),
                    drive_id=parent_ref.get("driveId", drive_id),
                    is_file=is_file,
                    is_folder=is_folder,
                    mime_type=child.get("file", {}).get("mimeType") if is_file else None,
                    child_count=child.get("folder", {}).get("childCount") if is_folder else None,
                )
            )

        return items

    def search_folders(
        self,
        *,
        site_id: str,
        drive_id: str,
        query: str,
    ) -> list[SharePointFolder]:
        """Search for folders by name within a drive.

        Args:
            site_id: SharePoint site ID.
            drive_id: Drive ID.
            query: Folder name search query.

        Returns:
            List of SharePointFolder with date fields always populated.
        """
        url = f"{_GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root/search(q='{query}')"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()

        folders: list[SharePointFolder] = []
        for item in data.get("value", []):
            if "folder" not in item:
                continue
            parent_ref = item.get("parentReference", {})
            folders.append(
                SharePointFolder(
                    id=item.get("id", ""),
                    name=item.get("name", ""),
                    web_url=item.get("webUrl", ""),
                    created_date_time=item.get("createdDateTime", ""),
                    last_modified_date_time=item.get("lastModifiedDateTime", ""),
                    site_id=parent_ref.get("siteId", site_id),
                    drive_id=parent_ref.get("driveId", drive_id),
                    child_count=item.get("folder", {}).get("childCount", 0),
                )
            )

        return folders

    # -- Content extraction ------------------------------------------------

    def _extract_content(self, raw_bytes: bytes, name: str, mime_type: str) -> str | None:
        """Extract text from document bytes based on format.

        Returns:
            Extracted text string, or None if format is unsupported.
        """
        # Plain text family
        if mime_type in _TEXT_MIMES or name.endswith((".txt", ".md", ".csv")):
            return raw_bytes.decode("utf-8", errors="replace")

        # DOCX
        if mime_type in _DOCX_MIMES or name.endswith(".docx"):
            return self._extract_docx(raw_bytes)

        # PPTX
        if mime_type in _PPTX_MIMES or name.endswith(".pptx"):
            return self._extract_pptx(raw_bytes)

        # XLSX
        if mime_type in _XLSX_MIMES or name.endswith(".xlsx"):
            return self._extract_xlsx(raw_bytes)

        # PDF
        if mime_type in _PDF_MIMES or name.endswith(".pdf"):
            return self._extract_pdf(raw_bytes)

        return None  # Unsupported format

    @staticmethod
    def _extract_docx(raw_bytes: bytes) -> str:
        """Extract text from DOCX bytes."""
        try:
            from docx import Document

            doc = Document(io.BytesIO(raw_bytes))
            return "\n".join(para.text for para in doc.paragraphs if para.text)
        except ImportError:
            raise ImportError("python-docx is required for DOCX extraction: pip install python-docx")

    @staticmethod
    def _extract_pptx(raw_bytes: bytes) -> str:
        """Extract text from PPTX bytes."""
        try:
            from pptx import Presentation

            prs = Presentation(io.BytesIO(raw_bytes))
            text_parts: list[str] = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            if para.text.strip():
                                text_parts.append(para.text)
            return "\n".join(text_parts)
        except ImportError:
            raise ImportError("python-pptx is required for PPTX extraction: pip install python-pptx")

    @staticmethod
    def _extract_xlsx(raw_bytes: bytes) -> str:
        """Extract text from XLSX bytes."""
        try:
            from openpyxl import load_workbook

            wb = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
            text_parts: list[str] = []
            for sheet in wb.sheetnames:
                ws = wb[sheet]
                text_parts.append(f"--- Sheet: {sheet} ---")
                for row in ws.iter_rows(values_only=True):
                    row_text = "\t".join(str(cell) if cell is not None else "" for cell in row)
                    if row_text.strip():
                        text_parts.append(row_text)
            wb.close()
            return "\n".join(text_parts)
        except ImportError:
            raise ImportError("openpyxl is required for XLSX extraction: pip install openpyxl")

    @staticmethod
    def _extract_pdf(raw_bytes: bytes) -> str:
        """Extract text from PDF bytes."""
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw_bytes))
            text_parts: list[str] = []
            for page in reader.pages:
                text = page.extract_text()
                if text and text.strip():
                    text_parts.append(text)
            return "\n".join(text_parts)
        except ImportError:
            raise ImportError("pypdf is required for PDF extraction: pip install pypdf")


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------


def _cli_search() -> None:
    """CLI: sharepoint-search — Search SharePoint for documents."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="sharepoint-search",
        description="Search SharePoint document libraries via Microsoft Graph.",
    )
    parser.add_argument("query", help="Search keywords")
    parser.add_argument("--max-results", type=int, default=25, help="Maximum results (default: 25)")
    parser.add_argument("--entity-types", nargs="+", default=["driveItem"], help="Graph entity types")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    args = parser.parse_args()

    creds = load_credentials()
    client = SharePointClient(
        app_id=creds["appId"],
        client_secret=creds["password"],
        tenant_id=creds["tenant"],
    )

    results = client.search(args.query, entity_types=args.entity_types, size=args.max_results)

    if args.json_output:
        import dataclasses

        print(json.dumps([dataclasses.asdict(r) for r in results], indent=2))
    else:
        for item in results:
            print(f"  {item.name}")
            print(f"    URL: {item.web_url}")
            print(f"    Created: {item.created_date_time}")
            print(f"    Modified: {item.last_modified_date_time}")
            print(f"    Size: {item.size}")
            print()


def _cli_read() -> None:
    """CLI: sharepoint-read — Download and read a SharePoint document."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="sharepoint-read",
        description="Read a SharePoint document via Microsoft Graph.",
    )
    parser.add_argument("--site-id", required=True, help="SharePoint site ID")
    parser.add_argument("--drive-id", required=True, help="Drive ID")
    parser.add_argument("--item-id", required=True, help="Item ID")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    args = parser.parse_args()

    creds = load_credentials()
    client = SharePointClient(
        app_id=creds["appId"],
        client_secret=creds["password"],
        tenant_id=creds["tenant"],
    )

    doc = client.read(site_id=args.site_id, drive_id=args.drive_id, item_id=args.item_id)

    if args.json_output:
        import dataclasses

        print(json.dumps(dataclasses.asdict(doc), indent=2))
    else:
        print(f"Document: {doc.name}")
        print(f"URL: {doc.web_url}")
        print(f"Created: {doc.created_date_time}")
        print(f"Modified: {doc.last_modified_date_time}")
        print(f"Size: {doc.size}")
        print(f"Type: {doc.mime_type}")
        if doc.body_text:
            print(f"\n--- Content ---\n{doc.body_text[:2000]}")
        if doc.extraction_error:
            print(f"\nExtraction error: {doc.extraction_error}")


def _cli_folder() -> None:
    """CLI: sharepoint-folder — List or search SharePoint folders."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="sharepoint-folder",
        description="List or search SharePoint folders via Microsoft Graph.",
    )
    parser.add_argument("--site-id", required=True, help="SharePoint site ID")
    parser.add_argument("--drive-id", required=True, help="Drive ID")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List folder contents")
    list_parser.add_argument("--folder-id", default="root", help="Folder ID (default: root)")

    search_parser = sub.add_parser("search", help="Search for folders")
    search_parser.add_argument("query", help="Search query")

    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    args = parser.parse_args()

    creds = load_credentials()
    client = SharePointClient(
        app_id=creds["appId"],
        client_secret=creds["password"],
        tenant_id=creds["tenant"],
    )

    if args.command == "list":
        items = client.list_folder(site_id=args.site_id, drive_id=args.drive_id, folder_id=args.folder_id)
        if args.json_output:
            import dataclasses

            print(json.dumps([dataclasses.asdict(i) for i in items], indent=2))
        else:
            for item in items:
                kind = "📁" if item.is_folder else "📄"
                print(f"  {kind} {item.name}")
                print(f"    Created: {item.created_date_time}")
                print(f"    Modified: {item.last_modified_date_time}")
                print()

    elif args.command == "search":
        folders = client.search_folders(site_id=args.site_id, drive_id=args.drive_id, query=args.query)
        if args.json_output:
            import dataclasses

            print(json.dumps([dataclasses.asdict(f) for f in folders], indent=2))
        else:
            for folder in folders:
                print(f"  📁 {folder.name} ({folder.child_count} items)")
                print(f"    Created: {folder.created_date_time}")
                print(f"    Modified: {folder.last_modified_date_time}")
                print()
