"""Browser-based auth capture — opens QuantData, lets user log in, sniffs credentials."""

from __future__ import annotations

import sys


def _log(msg: str = "") -> None:
    """Print to stderr (stdout is reserved for MCP JSON-RPC)."""
    print(msg, file=sys.stderr)


def capture_credentials() -> tuple[str, str]:
    """Open browser to QuantData, capture auth token and instance ID from network requests.

    Returns:
        (auth_token, instance_id) tuple

    Raises:
        RuntimeError: If playwright is not installed or credentials not captured
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright is required for browser login.\n"
            "Install: pip install 'quantdata-mcp[browser]'\n"
            "Then: playwright install chromium"
        )

    auth_token: str | None = None
    instance_id: str | None = None

    _log("Opening browser to QuantData...")
    _log("Log in to your account — credentials will be captured automatically.")
    _log()

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False)
        except Exception:
            _log("Chromium not installed. Installing now (one-time)...")
            import subprocess
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True,
                stdout=sys.stderr,
                stderr=sys.stderr,
            )
            browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        def handle_request(request):  # type: ignore[no-untyped-def]
            nonlocal auth_token, instance_id
            url = request.url
            if "core-lb-prod.quantdata.us/api" not in url:
                return
            headers = request.headers
            token = headers.get("authorization", "")
            inst = headers.get("x-instance-id", "")
            if token and token.startswith("eyJ") and inst:
                auth_token = token
                instance_id = inst

        page.on("request", handle_request)
        page.goto("https://v3.quantdata.us")

        _log("Waiting for you to log in...")
        _log("(The browser will close automatically once credentials are captured)")
        _log()

        try:
            while auth_token is None or instance_id is None:
                try:
                    page.wait_for_timeout(500)
                    page.title()
                except Exception:
                    break

            if auth_token and instance_id:
                _log(f"  Auth token captured: {auth_token[:20]}...{auth_token[-10:]}")
                _log(f"  Instance ID captured: {instance_id}")
                _log()
                try:
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
        finally:
            browser.close()

    if not auth_token or not instance_id:
        raise RuntimeError(
            "Could not capture credentials. Make sure you logged in and loaded a page with data."
        )

    return auth_token, instance_id
