"""
QuantData API Client - Reusable wrapper for all QuantData API interactions.

This module provides a clean, testable interface to the QuantData API with support for:
- Session management (date/ticker filtering)
- Page filter management (date, ticker, expiration per page)
- Time scrubbing (historical playback) with metadata-safe updates
- Data fetching for all tool types (GEX, DEX, Net Drift, Flow, OI, etc.)
- Generic fetch via ToolSpec for LLM tool calling
- Error handling and retries
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from quantdata_mcp.tools import ToolSpec

logger = logging.getLogger(__name__)


class QuantDataError(Exception):
    """Base exception for QuantData API errors"""

    pass


class QuantDataAuthError(QuantDataError):
    """Authentication failure"""

    pass


class QuantDataRateLimitError(QuantDataError):
    """Rate limit exceeded"""

    pass


class QuantDataClient:
    """
    Reusable QuantData API client with session and time management.

    Features:
    - Session date/ticker filtering for 0DTE data
    - Page-level filter management (date, ticker, expiration)
    - Time scrubbing for historical playback (metadata-safe)
    - Connection pooling via requests.Session
    - Error handling with retries
    - Generic fetch via ToolSpec for LLM tool calling
    - Batch operations for scraping

    Example:
        client = QuantDataClient(auth_token, instance_id)
        client.set_session_date("2025-12-23", ticker="SPX")
        data = client.fetch_strike_data(GAMMA_TOOL_ID)
    """

    BASE_URL = "https://core-lb-prod.quantdata.us/api"

    def __init__(
        self,
        auth_token: str,
        instance_id: str,
        user_id: str | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        """
        Initialize QuantData API client.

        Args:
            auth_token: QuantData authentication token
            instance_id: QuantData instance ID
            user_id: Optional user ID for tool updates
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds
        """
        self.auth_token = auth_token
        self.instance_id = instance_id
        self.user_id = user_id
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # Connection pooling for better performance
        self.session = requests.Session()
        self.session.headers.update(self._get_base_headers())

        logger.info(f"QuantData client initialized (instance: {instance_id[:8]}...)")

    def _get_base_headers(self) -> dict[str, str]:
        """Get base headers for all requests"""
        return {
            "accept": "application/json",
            "authorization": self.auth_token,
            "x-instance-id": self.instance_id,
            "x-qd-version": "1",
            "origin": "https://v3.quantdata.us",
        }

    def _make_request(self, method: str, endpoint: str, **kwargs: Any) -> requests.Response:
        """
        Make HTTP request with retry logic.

        Args:
            method: HTTP method (GET, PUT, POST, etc.)
            endpoint: API endpoint path
            **kwargs: Additional arguments for requests

        Returns:
            Response object

        Raises:
            QuantDataAuthError: Authentication failed
            QuantDataRateLimitError: Rate limit exceeded
            QuantDataError: Other API errors
        """
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"

        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, **kwargs)

                # Handle authentication errors
                if response.status_code == 401:
                    raise QuantDataAuthError("Authentication failed - check token and instance ID")

                # Handle rate limiting
                if response.status_code == 429:
                    if attempt < self.max_retries - 1:
                        wait_time = self.retry_delay * (2**attempt)
                        logger.warning(f"Rate limited, retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    raise QuantDataRateLimitError("Rate limit exceeded")

                # Raise for other HTTP errors
                response.raise_for_status()

                return response

            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2**attempt)
                    logger.warning(
                        f"Request failed (attempt {attempt + 1}/{self.max_retries}): {e}"
                    )
                    logger.warning(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                raise QuantDataError(
                    f"Request failed after {self.max_retries} attempts: {e}"
                ) from e

        raise QuantDataError("Unexpected error in request handling")

    # ------------------------------------------------------------------
    # User-level session management
    # ------------------------------------------------------------------

    def _ensure_user_id(self) -> bool:
        """Extract user ID from JWT token if not already set. Returns True if available."""
        if self.user_id:
            return True
        try:
            import base64
            import json as json_module

            parts = self.auth_token.split(".")
            payload_str = parts[1] + "=" * (4 - len(parts[1]) % 4)
            decoded = json_module.loads(base64.b64decode(payload_str))
            self.user_id = decoded.get("userId")
            if self.user_id:
                logger.debug(f"Extracted user ID from token: {self.user_id[:8]}...")
                return True
        except Exception as e:
            logger.warning(f"Could not extract user ID from token: {e}")
        return False

    def set_session_date(self, date_str: str, ticker: str = "SPX") -> bool:
        """
        Set global session date filter for 0DTE data.

        This updates the user's global filter settings to use a specific date
        and ticker. Critical for websocket connections to receive correct data.

        Note: This endpoint requires the full user attributes payload.
        We're using a minimal payload that should work in most cases.

        Args:
            date_str: Date in YYYY-MM-DD format
            ticker: Ticker symbol (default: SPX)

        Returns:
            True if successful, False otherwise

        Example:
            client.set_session_date("2025-12-23", ticker="SPX")
        """
        logger.info(f"Setting session date to {date_str} for {ticker}")

        self._ensure_user_id()

        # Full payload matching the API requirements
        payload = {
            "id": self.user_id,
            "fontSizePercentage": 100,
            "globalFilter": {
                "expirationDate": {"filterOperationType": "EQUALS", "value": date_str},
                "sessionDate": {"filterOperationType": "EQUALS", "value": date_str},
                "ticker": {"filterOperationType": "EQUALS", "value": [ticker]},
            },
            "globalTickerConfiguration": {"defaultTicker": "SPY", "favoriteTickers": []},
            "globalToolConfiguration": {
                "hideAxisTitles": False,
                "hideCrosshairs": False,
                "hideDataZoomSliders": False,
                "hideLegends": False,
                "hideStatusIndicators": False,
                "hideTimeSliders": False,
                "hideTitles": False,
                "hideTooltips": False,
            },
            "notificationConfiguration": {"positionType": "BOTTOM_LEFT", "stacked": False},
            "timeZoneType": "AMERICA_NEW_YORK",
            "createdTime": int(datetime.now(UTC).timestamp() * 1000),
            "lastUpdatedTime": int(datetime.now(UTC).timestamp() * 1000),
        }

        try:
            self._make_request("PUT", "user/attributes", json=payload, timeout=10)

            logger.info(f"Session date set to {date_str} ({ticker})")
            return True

        except Exception as e:
            logger.error(f"Failed to set session date: {e}")
            return False

    # ------------------------------------------------------------------
    # Page filter management
    # ------------------------------------------------------------------

    def set_page_filter(
        self,
        page_id: str,
        session_date: str,
        ticker: str = "SPX",
        expiration_date: str | None = None,
    ) -> bool:
        """Set page-level filters (date, ticker, expiration) for all tools on a page.

        Args:
            page_id: QuantData page ID
            session_date: Session date in YYYY-MM-DD format
            ticker: Ticker symbol (default: SPX)
            expiration_date: Expiration date in YYYY-MM-DD format (defaults to session_date for 0DTE)

        Returns:
            True if successful, False otherwise
        """
        if expiration_date is None:
            expiration_date = session_date

        now_ms = int(datetime.now(UTC).timestamp() * 1000)

        payload = {
            "id": page_id,
            "expirationDate": {"filterOperationType": "EQUALS", "value": expiration_date},
            "sessionDate": {"filterOperationType": "EQUALS", "value": session_date},
            "ticker": {"filterOperationType": "EQUALS", "value": [ticker]},
            "createdTime": now_ms,
            "lastUpdatedTime": now_ms,
        }

        try:
            self._make_request("PUT", "page-filter", json=payload, timeout=10)
            logger.info(
                f"Page filter set: page={page_id[:8]}... date={session_date} ticker={ticker}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to set page filter: {e}")
            return False

    # ------------------------------------------------------------------
    # Page layout management
    # ------------------------------------------------------------------

    def update_page_layout(
        self,
        page_id: str,
        tools: list[tuple[str, str, str]],
    ) -> bool:
        """Update page layout to include tools as tabs.

        Args:
            page_id: QuantData page ID
            tools: List of (tool_id, name, component_type) tuples

        Returns:
            True if successful, False otherwise
        """
        tab_children = []
        for tool_id, name, component_type in tools:
            tab_children.append(
                {
                    "type": "tab",
                    "name": name,
                    "component": component_type,
                    "id": tool_id,
                }
            )

        layout = {
            "type": "tabset",
            "children": tab_children,
        }

        payload = {
            "id": page_id,
            "layout": layout,
            "lastUpdatedTime": int(datetime.now(UTC).timestamp() * 1000),
        }

        try:
            self._make_request("PUT", "page", json=payload, timeout=10)
            logger.info(f"Page layout updated: page={page_id[:8]}... tools={len(tools)}")
            return True
        except Exception as e:
            logger.error(f"Failed to update page layout: {e}")
            return False

    # ------------------------------------------------------------------
    # Tool metadata management (metadata-safe)
    # ------------------------------------------------------------------

    def get_tool(self, tool_id: str) -> dict[str, Any] | None:
        """Fetch a tool's current configuration.

        Args:
            tool_id: QuantData tool ID

        Returns:
            Tool DTO dict (id, userId, metadata, pageId, etc.) or None if failed.
            Unwraps from the API's ``response.toolDTO`` envelope.
        """
        try:
            response = self._make_request("GET", f"tool/{tool_id}", timeout=10)
            result: dict[str, Any] = response.json()
            # Unwrap from response.toolDTO envelope
            tool_dto: dict[str, Any] | None = result.get("response", {}).get("toolDTO")
            if tool_dto is None:
                logger.error(f"Unexpected get_tool response structure for {tool_id[:8]}...")
                return None
            return tool_dto
        except Exception as e:
            logger.error(f"Failed to get tool {tool_id[:8]}...: {e}")
            return None

    def update_tool_metadata(
        self,
        tool_id: str,
        metadata_updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update specific metadata fields on a tool, preserving other fields.

        Fetches current tool state, merges metadata_updates into existing metadata,
        and PUTs back. Safe for any tool type -- does not corrupt greek mode, page ID,
        or other existing settings.

        Args:
            tool_id: QuantData tool ID
            metadata_updates: Dict of metadata fields to add/update

        Returns:
            Updated tool dict or None if failed
        """
        # Step 1: fetch current state
        current = self.get_tool(tool_id)
        if current is None:
            logger.error(f"Cannot update metadata: failed to fetch tool {tool_id[:8]}...")
            return None

        # Step 2: merge metadata updates
        existing_metadata = current.get("metadata", {})
        existing_metadata.update(metadata_updates)
        current["metadata"] = existing_metadata
        current["lastUpdatedTime"] = int(datetime.now(UTC).timestamp() * 1000)

        # Step 3: PUT back
        try:
            response = self._make_request("PUT", "tool", json=current, timeout=10)
            result: dict[str, Any] = response.json()
            logger.debug(f"Tool metadata updated: {tool_id[:8]}...")
            return result
        except Exception as e:
            logger.error(f"Failed to update tool metadata {tool_id[:8]}...: {e}")
            return None

    def set_tool_time(self, tool_id: str, minutes_from_midnight: int) -> bool:
        """
        Set time scrubber for historical playback. Preserves existing tool metadata.

        Fetches the tool's current config, adds/updates ``numberOfMinutesIntoMarketOpen``
        in the metadata, and PUTs back. This is safe for any tool type (GEX, DEX, drift,
        etc.) -- it does not overwrite greek mode, page ID, or other fields.

        Args:
            tool_id: QuantData tool ID
            minutes_from_midnight: Time offset in minutes from midnight
                                   (e.g., 570 = 9:30 AM, 960 = 4:00 PM)

        Returns:
            True if successful, False otherwise

        Example:
            # Set to 10:30 AM (10*60 + 30 = 630 minutes)
            client.set_tool_time(DELTA_TOOL_ID, 630)
        """
        hours = minutes_from_midnight // 60
        mins = minutes_from_midnight % 60
        time_str = f"{hours:02d}:{mins:02d}"

        logger.debug(
            f"Setting tool {tool_id[:8]}... time to {time_str} ({minutes_from_midnight} min)"
        )

        result = self.update_tool_metadata(
            tool_id,
            {"numberOfMinutesIntoMarketOpen": minutes_from_midnight},
        )

        if result is not None:
            logger.debug(f"Tool time set to {time_str}")
            return True

        logger.error(f"Failed to set tool time for {tool_id[:8]}...")
        return False

    def reset_to_live(self, tool_id: str) -> bool:
        """
        Remove time scrubber, return to live data. Preserves existing tool metadata.

        Fetches the tool's current config, removes ``numberOfMinutesIntoMarketOpen``
        from metadata, and PUTs back.

        Args:
            tool_id: QuantData tool ID

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Resetting tool {tool_id[:8]}... to live mode")

        # Fetch current state
        current = self.get_tool(tool_id)
        if current is None:
            logger.error(f"Cannot reset to live: failed to fetch tool {tool_id[:8]}...")
            return False

        # Remove the time scrubber key from metadata
        metadata = current.get("metadata", {})
        metadata.pop("numberOfMinutesIntoMarketOpen", None)
        current["metadata"] = metadata
        current["lastUpdatedTime"] = int(datetime.now(UTC).timestamp() * 1000)

        try:
            self._make_request("PUT", "tool", json=current, timeout=10)
            logger.info("Tool reset to live mode")
            return True
        except Exception as e:
            logger.error(f"Failed to reset tool: {e}")
            return False

    # ------------------------------------------------------------------
    # Data fetching -- existing endpoints
    # ------------------------------------------------------------------

    def fetch_strike_data(self, tool_id: str) -> dict[str, Any] | None:
        """
        Fetch GEX or DEX strike exposure data.

        Args:
            tool_id: Tool ID (GAMMA_TOOL_ID for GEX, DELTA_TOOL_ID for DEX)

        Returns:
            Strike data dict or None if failed
        """
        try:
            response = self._make_request("GET", f"options/exposure/strike/{tool_id}", timeout=30)

            result: dict[str, Any] = response.json()
            return result

        except Exception as e:
            logger.error(f"Failed to fetch strike data: {e}")
            return None

    def fetch_net_drift(self, tool_id: str) -> dict[str, Any] | None:
        """
        Fetch Net Drift data.

        Args:
            tool_id: Net Drift tool ID

        Returns:
            Net Drift data dict or None if failed
        """
        try:
            response = self._make_request("GET", f"options/net-drift/{tool_id}", timeout=30)

            result: dict[str, Any] = response.json()
            return result

        except Exception as e:
            logger.error(f"Failed to fetch net drift: {e}")
            return None

    def fetch_consolidated_flow(self, tool_id: str) -> dict[str, Any] | None:
        """
        Fetch consolidated flow data (sweeps/blocks).

        Args:
            tool_id: Consolidated flow tool ID

        Returns:
            Flow data dict or None if failed
        """
        try:
            response = self._make_request(
                "GET", f"options/order-flow/consolidated/{tool_id}", timeout=30
            )

            result: dict[str, Any] = response.json()
            return result

        except Exception as e:
            logger.error(f"Failed to fetch consolidated flow: {e}")
            return None

    def fetch_max_pain(self, tool_id: str) -> dict[str, Any] | None:
        """
        Fetch Max Pain data.

        Max Pain is the strike price where option holders would lose the most money.
        Price tends to gravitate toward max pain, especially near expiration.

        Args:
            tool_id: Max Pain tool ID

        Returns:
            Max Pain data dict with:
            - strikePriceInCentsWithMaxPain: The max pain strike (in cents)
            - stockPriceInCents: Current price (in cents)
            - strikePriceInCentsToIntrinsicValueData: Intrinsic values per strike
            Returns None if request failed.

        Example:
            data = client.fetch_max_pain(MAX_PAIN_TOOL_ID)
            max_pain_strike = data["response"]["strikePriceInCentsWithMaxPain"] / 100
        """
        try:
            response = self._make_request("GET", f"options/max-pain/{tool_id}", timeout=30)

            result: dict[str, Any] = response.json()
            return result

        except Exception as e:
            logger.error(f"Failed to fetch max pain: {e}")
            return None

    def fetch_iv_rank(self, tool_id: str) -> dict[str, Any] | None:
        """
        Fetch Implied Volatility Rank data.

        IV Rank shows where current IV sits relative to its historical range.
        Low IVR (<30%) = options are cheap, good for buying
        High IVR (>70%) = options are expensive, need bigger moves for profit

        Args:
            tool_id: IV Rank tool ID

        Returns:
            IV Rank data dict with:
            - sessionDateToIVRankData: Historical IVR by session date
            - expirationDates: Available expiration dates
            - stockPriceInCents: Current price
            Returns None if request failed.

        Example:
            data = client.fetch_iv_rank(IV_RANK_TOOL_ID)
            today = datetime.now().strftime("%Y-%m-%d")
            iv_data = data["response"]["sessionDateToIVRankData"].get(today, {})
        """
        try:
            response = self._make_request("GET", f"options/iv-rank/{tool_id}", timeout=30)

            result: dict[str, Any] = response.json()
            return result

        except Exception as e:
            logger.error(f"Failed to fetch IV rank: {e}")
            return None

    def fetch_trade_side_stats(self, tool_id: str) -> dict[str, Any] | None:
        """
        Fetch Contract Side Statistics (trade-side aggression data).

        Returns premium breakdown by trade side (AA/A/M/B/BB) for calls and puts.
        AA = Above Ask (aggressive buy), BB = Below Bid (aggressive sell).

        Args:
            tool_id: Trade side statistics tool ID

        Returns:
            Trade side stats dict or None if failed
        """
        try:
            response = self._make_request(
                "GET",
                f"options/contract/statistics/trade-side/{tool_id}",
                timeout=30,
            )

            result: dict[str, Any] = response.json()
            return result

        except Exception as e:
            logger.error(f"Failed to fetch trade side stats: {e}")
            return None

    # ------------------------------------------------------------------
    # Data fetching -- new endpoints
    # ------------------------------------------------------------------

    def fetch_net_flow(self, tool_id: str) -> dict[str, Any] | None:
        """Fetch Net Flow data -- call/put premium flow over time.

        Args:
            tool_id: Net Flow tool ID

        Returns:
            Net flow data dict or None if failed
        """
        try:
            response = self._make_request("GET", f"options/net-flow/{tool_id}", timeout=30)
            result: dict[str, Any] = response.json()
            return result
        except Exception as e:
            logger.error(f"Failed to fetch net flow: {e}")
            return None

    def fetch_oi_by_strike(self, tool_id: str) -> dict[str, Any] | None:
        """Fetch Open Interest by Strike -- put/call OI per strike.

        Args:
            tool_id: OI by Strike tool ID

        Returns:
            OI by strike data dict or None if failed
        """
        try:
            response = self._make_request(
                "GET", f"options/open-interest/strike/{tool_id}", timeout=30
            )
            result: dict[str, Any] = response.json()
            return result
        except Exception as e:
            logger.error(f"Failed to fetch OI by strike: {e}")
            return None

    def fetch_contract_statistics(self, tool_id: str) -> dict[str, Any] | None:
        """Fetch Contract Statistics -- premium, trade count, volume by call/put.

        Args:
            tool_id: Contract Statistics tool ID

        Returns:
            Contract statistics data dict or None if failed
        """
        try:
            response = self._make_request(
                "GET", f"options/contract/statistics/{tool_id}", timeout=30
            )
            result: dict[str, Any] = response.json()
            return result
        except Exception as e:
            logger.error(f"Failed to fetch contract statistics: {e}")
            return None

    def fetch_exposure_by_expiration(self, tool_id: str) -> dict[str, Any] | None:
        """Fetch Exposure by Expiration -- greek exposure across expiration dates.

        Args:
            tool_id: Exposure by Expiration tool ID

        Returns:
            Exposure by expiration data dict or None if failed
        """
        try:
            response = self._make_request(
                "GET", f"options/exposure/expiration/{tool_id}", timeout=30
            )
            result: dict[str, Any] = response.json()
            return result
        except Exception as e:
            logger.error(f"Failed to fetch exposure by expiration: {e}")
            return None

    def fetch_contract_price_time(self, tool_id: str) -> dict[str, Any] | None:
        """Fetch Contract Price / Time -- OHLCV over time for a specific contract.

        Args:
            tool_id: Contract Price / Time tool ID

        Returns:
            Contract price/time data dict or None if failed
        """
        try:
            response = self._make_request(
                "GET", f"options/contract/price/time/{tool_id}", timeout=30
            )
            result: dict[str, Any] = response.json()
            return result
        except Exception as e:
            logger.error(f"Failed to fetch contract price/time: {e}")
            return None

    # ------------------------------------------------------------------
    # Generic fetch by ToolSpec
    # ------------------------------------------------------------------

    def fetch_tool_data(self, tool_spec: ToolSpec) -> dict[str, Any] | None:
        """Fetch data for any tool using its spec's endpoint.

        This is the method the LLM tool executor will use -- it routes to the
        correct REST endpoint based on ``tool_spec.endpoint``.

        Args:
            tool_spec: ToolSpec instance with endpoint and tool_id

        Returns:
            Raw API response dict or None if failed
        """
        try:
            response = self._make_request(
                "GET",
                f"{tool_spec.endpoint}/{tool_spec.tool_id}",
                timeout=30,
            )
            result: dict[str, Any] = response.json()
            return result
        except Exception as e:
            logger.error(f"Failed to fetch {tool_spec.label}: {e}")
            return None

    # ------------------------------------------------------------------
    # Tool CRUD (create, update filter)
    # ------------------------------------------------------------------

    def create_tool(
        self,
        page_id: str,
        tool_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Create a new tool instance on a QuantData page.

        Used to programmatically create tool instances (e.g., Contract Side
        Statistics filtered to specific strikes) on a dedicated page.

        Args:
            page_id: QuantData page ID to create the tool on
            tool_type: Tool type string (e.g., "OPTIONS_CONTRACT_TRADE_SIDE_STATISTICS_CHART")
            metadata: Optional metadata/configuration for the tool

        Returns:
            Created tool dict (includes new tool ID) or None if failed
        """
        payload: dict[str, Any] = {
            "pageId": page_id,
            "type": tool_type,
        }
        if metadata:
            payload["metadata"] = metadata

        try:
            response = self._make_request("POST", "tool", json=payload, timeout=10)

            result: dict[str, Any] = response.json()
            logger.info(f"Created tool: {tool_type} on page {page_id[:8]}...")
            return result

        except Exception as e:
            logger.error(f"Failed to create tool: {e}")
            return None

    def update_tool_filter(
        self,
        tool_id: str,
        user_id: str,
        page_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        Update tool configuration/filters via PUT /api/tool.

        Used to reconfigure a tool instance with specific filters (e.g.,
        filtering Contract Side Statistics to a specific strike price).

        Args:
            tool_id: Tool instance ID to update
            user_id: QuantData user ID (from JWT token)
            page_id: Page ID the tool belongs to
            metadata: Full metadata dict including filter configuration

        Returns:
            Updated tool dict or None if failed
        """
        now_ms = int(datetime.now(UTC).timestamp() * 1000)

        payload: dict[str, Any] = {
            "id": tool_id,
            "userId": user_id,
            "filterGroupIds": [],
            "metadata": metadata,
            "pageId": page_id,
            # NOTE: createdTime is replayed from observed network payload shape.
            # The real tool may have an original creation timestamp -- if QuantData
            # rejects this, pass the actual createdTime from the tool's GET response.
            "createdTime": now_ms,
            "lastUpdatedTime": now_ms,
        }

        try:
            response = self._make_request("PUT", "tool", json=payload, timeout=10)

            result: dict[str, Any] = response.json()
            logger.info(f"Updated tool filter: {tool_id[:8]}...")
            return result

        except Exception as e:
            logger.error(f"Failed to update tool filter: {e}")
            return None

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def fetch_market_snapshot(
        self, gex_tool_id: str, dex_tool_id: str, drift_tool_id: str, flow_tool_id: str
    ) -> dict[str, Any]:
        """
        Fetch complete market snapshot (all data sources).

        Batch operation for scraping - fetches GEX, DEX, Net Drift, and
        Consolidated Flow in sequence.

        Args:
            gex_tool_id: Gamma exposure tool ID
            dex_tool_id: Delta exposure tool ID
            drift_tool_id: Net Drift tool ID
            flow_tool_id: Consolidated flow tool ID

        Returns:
            Dict containing all market data
        """
        logger.debug("Fetching complete market snapshot...")

        # Fetch all data sources
        gex_data = self.fetch_strike_data(gex_tool_id)
        dex_data = self.fetch_strike_data(dex_tool_id)
        net_drift = self.fetch_net_drift(drift_tool_id)
        consolidated_flow = self.fetch_consolidated_flow(flow_tool_id)

        # Extract price from DEX data
        price: float = 0.0
        if dex_data and "response" in dex_data:
            price = float(dex_data["response"].get("stockPriceInCents", 0)) / 100

        snapshot: dict[str, Any] = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f"),
            "gex_data": gex_data,
            "dex_data": dex_data,
            "net_drift": net_drift,
            "consolidated_flow": consolidated_flow,
            "price": price,
        }

        logger.debug(f"Snapshot complete (price: ${price:.2f})")

        return snapshot

    # ------------------------------------------------------------------
    # Page management (for setup)
    # ------------------------------------------------------------------

    def create_page(self, name: str, description: str = "") -> dict[str, Any] | None:
        """Create a new page.

        Args:
            name: Page name
            description: Page description

        Returns:
            Page dict or None if failed
        """
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        self._ensure_user_id()
        payload = {
            "userId": self.user_id,
            "type": "CUSTOM",
            "name": name,
            "description": description,
            "metadata": {"iconName": "IconRobotFace"},
            "isPublic": False,
            "createdTime": now_ms,
            "lastUpdatedTime": now_ms,
        }
        try:
            response = self._make_request("POST", "page", json=payload, timeout=10)
            result: dict[str, Any] = response.json()
            page = result.get("response", {}).get("page")
            if page:
                logger.info(f"Page created: {page.get('id', '')[:8]}... ({name})")
                return page
            return result
        except Exception as e:
            logger.error(f"Failed to create page: {e}")
            return None

    def get_pages(self) -> list[dict[str, Any]]:
        """List all pages for the current user.

        Returns:
            List of page dicts
        """
        try:
            response = self._make_request("GET", "page", timeout=10)
            result = response.json()
            pages: list[dict[str, Any]] = result.get("response", {}).get("pages", [])
            return pages
        except Exception as e:
            logger.error(f"Failed to list pages: {e}")
            return []

    def close(self) -> None:
        """Close the session and cleanup resources"""
        if self.session:
            self.session.close()
            logger.info("QuantData client session closed")
