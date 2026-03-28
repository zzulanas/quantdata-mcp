"""Browser-based auth capture — opens QuantData, lets user log in, sniffs credentials."""

from __future__ import annotations

import sys


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
        print("Playwright is required for browser login.")
        print("Install it with: pip install 'quantdata-mcp[browser]'")
        print("Then run: playwright install chromium")
        sys.exit(1)

    auth_token: str | None = None
    instance_id: str | None = None

    print("Opening browser to QuantData...")
    print("Log in to your account — credentials will be captured automatically.")
    print()

    with sync_playwright() as p:
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

        # Wait for the user to log in and make at least one API call
        print("Waiting for you to log in...")
        print("(The browser will close automatically once credentials are captured)")
        print()

        # Poll until we capture credentials or user closes browser
        try:
            while auth_token is None or instance_id is None:
                try:
                    page.wait_for_timeout(500)
                    # Check if browser is still open
                    page.title()
                except Exception:
                    break

            if auth_token and instance_id:
                print(f"  Auth token captured: {auth_token[:20]}...{auth_token[-10:]}")
                print(f"  Instance ID captured: {instance_id}")
                print()

                # Give user a moment to see the confirmation
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
