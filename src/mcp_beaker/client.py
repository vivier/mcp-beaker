"""Beaker API client wrapping XML-RPC and REST endpoints.

Handles cookie-based session management and three auth strategies:
  1. Password via XML-RPC ``auth.login_password()``
  2. Kerberos via native GSSAPI/SPNEGO (``kerberos_backend="http"``, default)
  3. Kerberos via ``bkr`` CLI subprocesses (``kerberos_backend="bkr"``)
"""

from __future__ import annotations

import asyncio
import base64
import http.client
import logging
import ssl
import xmlrpc.client
from typing import Any
from urllib.parse import urlparse

import httpx

from mcp_beaker.config import BeakerConfig
from mcp_beaker.exceptions import (
    BeakerAuthenticationError,
    BeakerConfigError,
    BeakerConnectionError,
    BeakerNotFoundError,
    BeakerXMLRPCError,
)
from mcp_beaker.utils import bkr_cli

try:
    import gssapi

    _HAS_GSSAPI = True
except ImportError:
    gssapi = None  # type: ignore[assignment]
    _HAS_GSSAPI = False

logger = logging.getLogger("mcp-beaker")


# ---------------------------------------------------------------------------
# XML-RPC cookie transport
# ---------------------------------------------------------------------------


class CookieTransport(xmlrpc.client.SafeTransport):
    """XML-RPC transport that persists cookies across calls.

    Beaker uses HTTP cookies to track authenticated sessions, so cookies
    returned by ``auth.login_password()`` must be forwarded on subsequent
    calls.
    """

    def __init__(self, ssl_context: ssl.SSLContext | None = None) -> None:
        super().__init__(context=ssl_context)
        self._cookies: list[str] = []

    def send_headers(self, connection: Any, headers: Any) -> None:
        if self._cookies:
            connection.putheader("Cookie", "; ".join(self._cookies))
        super().send_headers(connection, headers)

    def parse_response(self, response: Any) -> Any:
        for header in response.msg.get_all("Set-Cookie") or []:
            cookie = header.split(";", 1)[0]
            if cookie and cookie not in self._cookies:
                self._cookies.append(cookie)
        return super().parse_response(response)


# ---------------------------------------------------------------------------
# Beaker client
# ---------------------------------------------------------------------------


class BeakerClient:
    """High-level client for the Beaker XML-RPC and REST APIs.

    Instantiated once per server lifespan and shared across tool calls
    via dependency injection.
    """

    def __init__(self, config: BeakerConfig) -> None:
        if not config.url:
            raise BeakerConfigError(
                "No Beaker URL configured. Set the BEAKER_URL environment "
                "variable or pass --beaker-url."
            )
        self.config = config
        self._ssl_context = config.make_ssl_context()
        self._proxy: xmlrpc.client.ServerProxy | None = None
        self._cookie_transport: CookieTransport | None = None
        self._authenticated = False
        self._session_cookie: str | None = None

    # -- XML-RPC ------------------------------------------------------------

    def _new_proxy(self) -> xmlrpc.client.ServerProxy:
        """Create a fresh XML-RPC proxy (new TCP connection)."""
        self._cookie_transport = CookieTransport(ssl_context=self._ssl_context)
        proxy = xmlrpc.client.ServerProxy(
            self.config.rpc_url, transport=self._cookie_transport,
        )
        self._proxy = proxy
        return proxy

    def _get_proxy(self) -> xmlrpc.client.ServerProxy:
        if self._proxy is None:
            return self._new_proxy()
        return self._proxy

    def _ensure_password_auth(self) -> None:
        """Authenticate with username/password if not already done."""
        if self._authenticated:
            return
        proxy = self._get_proxy()
        if not self.config.username or not self.config.password:
            raise BeakerAuthenticationError(
                "Password auth requires BEAKER_USERNAME and BEAKER_PASSWORD environment variables."
            )
        try:
            proxy.auth.login_password(self.config.username, self.config.password)
        except xmlrpc.client.Fault as exc:
            raise BeakerAuthenticationError(f"Beaker login failed: {exc.faultString}") from exc
        except Exception as exc:
            raise BeakerConnectionError(
                f"Could not connect to Beaker at {self.config.url}: {exc}"
            ) from exc
        self._authenticated = True

    def _ensure_spnego_auth(self) -> None:
        """Authenticate via HTTP Negotiate/SPNEGO, storing the session cookie.

        Requires the ``gssapi`` package and a valid Kerberos ticket (``kinit``).
        The Beaker server returns a ``beaker_auth_token`` cookie on ``GET /login``
        with a valid Negotiate header; that cookie is injected into the XML-RPC
        transport so subsequent calls are authenticated.
        """
        if self._authenticated:
            return
        if not _HAS_GSSAPI:
            raise BeakerAuthenticationError(
                "Kerberos auth via SPNEGO requires the 'gssapi' package. "
                "Install it with: pip install gssapi  "
                "(or: pip install mcp-beaker[kerberos])"
            )

        host = urlparse(self.config.url).hostname
        service_name = gssapi.Name(
            f"HTTP@{host}",
            name_type=gssapi.NameType.hostbased_service,
        )
        ctx = gssapi.SecurityContext(name=service_name, usage="initiate")
        token = base64.b64encode(ctx.step()).decode("ascii")

        verify = self._get_verify()
        response = httpx.get(
            f"{self.config.url}/login",
            headers={"Authorization": f"Negotiate {token}"},
            follow_redirects=True,
            verify=verify,
        )
        cookie = response.cookies.get("beaker_auth_token")
        if not cookie:
            raise BeakerAuthenticationError(
                "SPNEGO negotiation completed but Beaker did not return a session cookie. "
                f"HTTP {response.status_code}: {response.text[:200]}"
            )

        self._get_proxy()
        self._cookie_transport._cookies.append(f"beaker_auth_token={cookie}")
        self._session_cookie = cookie
        self._authenticated = True
        logger.info("SPNEGO authentication successful for %s", host)

    def _reauth(self) -> None:
        """Re-authenticate using the configured method after a connection reset."""
        if self.config.auth_method == "password":
            self._ensure_password_auth()
        elif (
            self.config.auth_method == "kerberos"
            and self.config.kerberos_backend == "http"
        ):
            self._ensure_spnego_auth()

    async def call_xmlrpc(self, method: str, *args: Any) -> Any:
        """Call a Beaker XML-RPC method, handling auth and threading.

        Automatically retries once with a fresh proxy if the connection
        is stale (keep-alive timeout on the server side).
        """
        proxy = self._get_proxy()

        def _call(p: xmlrpc.client.ServerProxy) -> Any:
            obj: Any = p
            for part in method.split("."):
                obj = getattr(obj, part)
            return obj(*args)

        try:
            return await asyncio.to_thread(_call, proxy)
        except xmlrpc.client.Fault as exc:
            raise BeakerXMLRPCError(exc.faultCode, exc.faultString) from exc
        except (ConnectionError, OSError, http.client.HTTPException) as exc:
            logger.debug("XML-RPC connection failed (%s), retrying with fresh proxy", exc)
            proxy = self._new_proxy()
            if self._authenticated:
                self._authenticated = False
                self._reauth()
            try:
                return await asyncio.to_thread(_call, proxy)
            except xmlrpc.client.Fault as exc2:
                raise BeakerXMLRPCError(exc2.faultCode, exc2.faultString) from exc2
            except Exception as exc2:
                raise BeakerConnectionError(
                    f"Could not connect to Beaker at {self.config.url}: {exc2}"
                ) from exc2
        except Exception as exc:
            err_msg = str(exc)
            if err_msg in ("Idle", "Request-sent", "") or "RemoteDisconnected" in err_msg:
                logger.debug("Stale XML-RPC connection (%s), retrying with fresh proxy", exc)
                proxy = self._new_proxy()
                if self._authenticated:
                    self._authenticated = False
                    self._reauth()
                try:
                    return await asyncio.to_thread(_call, proxy)
                except xmlrpc.client.Fault as exc2:
                    raise BeakerXMLRPCError(exc2.faultCode, exc2.faultString) from exc2
                except Exception as exc2:
                    raise BeakerConnectionError(
                        f"Could not connect to Beaker at {self.config.url}: {exc2}"
                    ) from exc2
            raise BeakerConnectionError(
                f"Could not connect to Beaker at {self.config.url}: {exc}"
            ) from exc

    async def call_xmlrpc_authenticated(self, method: str, *args: Any) -> Any:
        """Call an XML-RPC method that requires authentication.

        Password auth uses XML-RPC ``login_password``.  Kerberos with
        ``kerberos_backend="http"`` uses native SPNEGO; callers using the
        ``bkr`` CLI path bypass this method entirely.
        """
        if self.config.auth_method == "password":
            await asyncio.to_thread(self._ensure_password_auth)
        elif (
            self.config.auth_method == "kerberos"
            and self.config.kerberos_backend == "http"
        ):
            await asyncio.to_thread(self._ensure_spnego_auth)
        return await self.call_xmlrpc(method, *args)

    # -- REST / HTTP --------------------------------------------------------

    def _get_verify(self) -> bool | ssl.SSLContext:
        """Build the SSL verification parameter for httpx.

        Re-uses the same SSL context that the XML-RPC transport uses.
        Falls back to ``ssl_verify`` bool when no custom context exists.
        """
        if self._ssl_context is not None:
            return self._ssl_context
        return self.config.ssl_verify

    async def rest_get(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        """Make an authenticated GET request to the Beaker REST API."""
        url = f"{self.config.url}{path}"
        verify = self._get_verify()
        cookies: dict[str, str] = {}
        session_cookie = getattr(self, "_session_cookie", None)
        if session_cookie:
            cookies["beaker_auth_token"] = session_cookie
        async with httpx.AsyncClient(
            verify=verify, follow_redirects=True, cookies=cookies,
        ) as client:
            try:
                response = await client.get(url, headers=headers, params=params, timeout=timeout)
                if response.url and "login" in str(response.url):
                    raise BeakerAuthenticationError(
                        f"Beaker redirected to login for {path}. "
                        "This endpoint requires authentication."
                    )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 404:
                    raise BeakerNotFoundError(f"Not found: {path}") from exc
                if status in (401, 403):
                    raise BeakerAuthenticationError(
                        f"Authentication failed for {path} (HTTP {status})"
                    ) from exc
                raise
            except httpx.ConnectError as exc:
                raise BeakerConnectionError(
                    f"Could not connect to Beaker at {self.config.url}: {exc}"
                ) from exc

    async def rest_get_json(
        self,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """GET a JSON response from the Beaker REST API."""
        headers = kwargs.pop("headers", {})
        headers["Accept"] = "application/json"
        response = await self.rest_get(path, headers=headers, **kwargs)
        return response.json()

    async def rest_get_text(
        self,
        path: str,
        **kwargs: Any,
    ) -> str:
        """GET a text response from the Beaker REST API."""
        response = await self.rest_get(path, **kwargs)
        return response.text

    async def _ensure_rest_auth(self) -> None:
        """Ensure the session cookie is set for REST calls that need auth."""
        if self.config.auth_method == "password":
            await asyncio.to_thread(self._ensure_password_auth)
        elif (
            self.config.auth_method == "kerberos"
            and self.config.kerberos_backend == "http"
        ):
            await asyncio.to_thread(self._ensure_spnego_auth)

    async def rest_post_json(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> httpx.Response:
        """Make an authenticated POST request to the Beaker REST API."""
        await self._ensure_rest_auth()
        url = f"{self.config.url}{path}"
        verify = self._get_verify()
        cookies: dict[str, str] = {}
        session_cookie = getattr(self, "_session_cookie", None)
        if session_cookie:
            cookies["beaker_auth_token"] = session_cookie
        async with httpx.AsyncClient(
            verify=verify, follow_redirects=True, cookies=cookies,
        ) as client:
            try:
                response = await client.post(
                    url,
                    json=json_body,
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    timeout=timeout,
                )
                if response.url and "login" in str(response.url):
                    raise BeakerAuthenticationError(
                        f"Beaker redirected to login for {path}. "
                        "This endpoint requires authentication."
                    )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 404:
                    raise BeakerNotFoundError(f"Not found: {path}") from exc
                if status in (401, 403):
                    raise BeakerAuthenticationError(
                        f"Authentication failed for {path} (HTTP {status})"
                    ) from exc
                raise
            except httpx.ConnectError as exc:
                raise BeakerConnectionError(
                    f"Could not connect to Beaker at {self.config.url}: {exc}"
                ) from exc

    async def rest_patch_json(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> httpx.Response:
        """Make an authenticated PATCH request to the Beaker REST API."""
        await self._ensure_rest_auth()
        url = f"{self.config.url}{path}"
        verify = self._get_verify()
        cookies: dict[str, str] = {}
        session_cookie = getattr(self, "_session_cookie", None)
        if session_cookie:
            cookies["beaker_auth_token"] = session_cookie
        async with httpx.AsyncClient(
            verify=verify, follow_redirects=True, cookies=cookies,
        ) as client:
            try:
                response = await client.patch(
                    url,
                    json=json_body,
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    timeout=timeout,
                )
                if response.url and "login" in str(response.url):
                    raise BeakerAuthenticationError(
                        f"Beaker redirected to login for {path}. "
                        "This endpoint requires authentication."
                    )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 404:
                    raise BeakerNotFoundError(f"Not found: {path}") from exc
                if status in (401, 403):
                    raise BeakerAuthenticationError(
                        f"Authentication failed for {path} (HTTP {status})"
                    ) from exc
                raise
            except httpx.ConnectError as exc:
                raise BeakerConnectionError(
                    f"Could not connect to Beaker at {self.config.url}: {exc}"
                ) from exc

    @property
    def _use_bkr(self) -> bool:
        """Whether to route authenticated calls through ``bkr`` CLI subprocesses.

        Controlled by ``config.kerberos_backend``:
          - ``"http"`` → native SPNEGO (requires ``gssapi``)
          - ``"bkr"``  → ``bkr`` CLI subprocesses (requires ``bkr`` on PATH)
        """
        if self.config.auth_method != "kerberos":
            return False
        if self.config.kerberos_backend == "bkr":
            if not bkr_cli.is_bkr_available():
                raise BeakerAuthenticationError(
                    "Kerberos backend 'bkr' selected but the 'bkr' CLI is not "
                    "on PATH. Install it with: yum install beaker-client"
                )
            return True
        if not _HAS_GSSAPI:
            raise BeakerAuthenticationError(
                "Kerberos backend 'http' selected but the 'gssapi' package is "
                "not installed. Install it with: pip install mcp-beaker[kerberos]"
            )
        return False

    # -- Convenience wrappers -----------------------------------------------
    # Authenticated operations are routed through ``bkr`` CLI when using
    # Kerberos, matching how the beaker-ai project handles auth.  Password
    # auth goes through XML-RPC with cookie-based sessions.

    async def whoami(self) -> dict[str, Any]:
        if self._use_bkr:
            return await bkr_cli.bkr_whoami()
        return await self.call_xmlrpc_authenticated("auth.who_am_i")

    async def lab_controllers(self) -> list[str]:
        return await self.call_xmlrpc("lab_controllers")

    async def jobs_filter(self, filters: dict[str, Any]) -> list[str]:
        return await self.call_xmlrpc("jobs.filter", filters)

    async def jobs_upload(self, job_xml: str) -> str:
        if self._use_bkr:
            return await bkr_cli.bkr_job_submit(job_xml)
        return await self.call_xmlrpc_authenticated("jobs.upload", job_xml)

    async def jobs_set_response(self, taskid: str, response: str) -> Any:
        if self._use_bkr:
            await bkr_cli.bkr_job_set_response(taskid, response)
            return None
        return await self.call_xmlrpc_authenticated(
            "jobs.set_response", taskid, response,
        )

    async def taskactions_task_info(self, taskid: str) -> dict[str, Any]:
        return await self.call_xmlrpc("taskactions.task_info", taskid)

    async def taskactions_to_xml(
        self,
        taskid: str,
        clone: bool = False,
        include_logs: bool = True,
    ) -> str:
        return await self.call_xmlrpc(
            "taskactions.to_xml", taskid, clone, True, include_logs,
        )

    async def taskactions_files(self, taskid: str) -> list[dict[str, Any]]:
        return await self.call_xmlrpc("taskactions.files", taskid)

    async def taskactions_stop(self, taskid: str, msg: str) -> Any:
        if self._use_bkr:
            await bkr_cli.bkr_job_cancel(taskid, msg)
            return None
        return await self.call_xmlrpc_authenticated(
            "taskactions.stop", taskid, "cancel", msg,
        )

    async def systems_reserve(self, fqdn: str) -> Any:
        if self._use_bkr:
            await bkr_cli.bkr_system_reserve(fqdn)
            return None
        return await self.call_xmlrpc_authenticated("systems.reserve", fqdn)

    async def systems_release(self, fqdn: str) -> Any:
        if self._use_bkr:
            await bkr_cli.bkr_system_release(fqdn)
            return None
        return await self.call_xmlrpc_authenticated("systems.release", fqdn)

    async def systems_power(
        self, action: str, fqdn: str, force: bool = False,
    ) -> Any:
        if self._use_bkr:
            await bkr_cli.bkr_system_power(fqdn, action, force=force)
            return None
        return await self.call_xmlrpc_authenticated(
            "systems.power", action, fqdn, False, force,
        )

    async def systems_provision(
        self,
        fqdn: str,
        distro_tree_id: int,
        ks_meta: str = "",
        kernel_options: str = "",
        kernel_options_post: str = "",
        kickstart: str = "",
        reboot: bool = True,
    ) -> Any:
        if self._use_bkr:
            await bkr_cli.bkr_system_provision(
                fqdn,
                distro_tree_id,
                ks_meta=ks_meta,
                kernel_options=kernel_options,
                kernel_options_post=kernel_options_post,
                kickstart=kickstart,
                reboot=reboot,
            )
            return None
        return await self.call_xmlrpc_authenticated(
            "systems.provision",
            fqdn,
            distro_tree_id,
            ks_meta,
            kernel_options,
            kernel_options_post,
            kickstart,
            reboot,
        )

    async def systems_loan_grant(
        self,
        fqdn: str,
        recipient: str | None = None,
        comment: str = "",
    ) -> None:
        """Grant a loan for a system.

        Uses ``bkr loan-grant`` when the bkr backend is active, otherwise
        calls ``POST /systems/<fqdn>/loans/`` on the Beaker REST API.
        """
        if self._use_bkr:
            await bkr_cli.bkr_loan_grant(fqdn, recipient=recipient, comment=comment)
            return
        body: dict[str, Any] = {}
        if recipient:
            body["recipient"] = {"user_name": recipient}
        if comment:
            body["comment"] = comment
        await self.rest_post_json(f"/systems/{fqdn}/loans/", body or None)

    async def systems_loan_return(self, fqdn: str) -> None:
        """Return the current loan on a system.

        Uses ``bkr loan-return`` when the bkr backend is active, otherwise
        calls ``PATCH /systems/<fqdn>/loans/+current`` on the Beaker REST API.
        """
        if self._use_bkr:
            await bkr_cli.bkr_loan_return(fqdn)
            return
        await self.rest_patch_json(
            f"/systems/{fqdn}/loans/+current",
            {"finish": "now"},
        )

    async def systems_history(
        self, fqdn: str, since: str | None = None,
    ) -> list[dict[str, Any]]:
        args: list[Any] = [fqdn]
        if since is not None:
            args.append(since)
        return await self.call_xmlrpc("systems.history", *args)

    async def systems_get_inventory(self, fqdn: str) -> dict[str, list[str]]:
        """Fetch the system inventory key-value pairs from the RDF/XML view.

        Returns a dict mapping key names (e.g. PCIID, CPUMODEL) to their
        list of values.
        """
        import xml.etree.ElementTree as _ET
        from collections import defaultdict as _defaultdict

        response = await self.rest_get(
            f"/view/{fqdn}",
            params={"tg_format": "rdfxml"},
            timeout=30.0,
        )
        root = _ET.fromstring(response.text)
        beaker_prefix = f"{self.config.url}/keys/"
        kv: dict[str, list[str]] = _defaultdict(list)
        for elem in root.iter():
            tag = elem.tag
            if tag.startswith("{") and elem.text:
                ns_end = tag.index("}")
                ns = tag[1:ns_end]
                local = tag[ns_end + 1 :]
                if ns.startswith(beaker_prefix) and local == "key":
                    key_name = ns[len(beaker_prefix) :].rstrip("#")
                    kv[key_name].append(elem.text)
        return dict(kv)

    async def systems_get_osmajor_arches(
        self, fqdn: str, tags: list[str] | None = None,
    ) -> dict[str, list[str]]:
        args: list[Any] = [fqdn]
        if tags is not None:
            args.append(tags)
        return await self.call_xmlrpc_authenticated("systems.get_osmajor_arches", *args)

    async def distrotrees_filter(
        self, filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return await self.call_xmlrpc("distrotrees.filter", filters)

    async def distros_get_osmajors(
        self, tags: list[str] | None = None,
    ) -> list[str]:
        if tags is not None:
            return await self.call_xmlrpc("distros.get_osmajors", tags)
        return await self.call_xmlrpc("distros.get_osmajors")

    async def tasks_filter(
        self, filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return await self.call_xmlrpc("tasks.filter", filters)

    async def recipes_tasks_extend(self, task_id: int, kill_time: int) -> Any:
        if self._use_bkr:
            await bkr_cli.bkr_watchdog_extend(f"T:{task_id}", kill_time)
            return None
        return await self.call_xmlrpc_authenticated(
            "recipes.tasks.extend", task_id, kill_time,
        )
