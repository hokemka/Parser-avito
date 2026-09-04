from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

NETWORKS = {
    "mainnet": "https://pay.crypt.bot/api",
    "testnet": "https://testnet-pay.crypt.bot/api",
}


class CryptoPayError(Exception):
    pass


class CryptoPayClient:
    def __init__(self, token: str, network: str = "mainnet", timeout: int = 20) -> None:
        self.token = token
        self.base_url = NETWORKS.get(network, NETWORKS["mainnet"])
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not self.token:
            raise CryptoPayError("CryptoBot token is not configured")
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"Crypto-Pay-API-Token": self.token}
        try:
            async with aiohttp.ClientSession(timeout=self.timeout, trust_env=True) as session:
                async with session.post(f"{self.base_url}/{method}", json=clean, headers=headers) as response:
                    payload = await response.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise CryptoPayError(f"network error: {exc}") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            error = payload.get("error") if isinstance(payload, dict) else payload
            raise CryptoPayError(f"{method} failed: {error}")
        return payload.get("result")

    async def get_me(self) -> dict[str, Any]:
        return await self._call("getMe")

    async def create_invoice(
        self,
        amount: float,
        currency_type: str = "fiat",
        fiat: str = "RUB",
        asset: str | None = None,
        accepted_assets: list[str] | None = None,
        description: str | None = None,
        payload: str | None = None,
        expires_in: int = 3600,
        paid_btn_name: str | None = None,
        paid_btn_url: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "amount": f"{amount:.2f}" if currency_type == "fiat" else f"{amount:.6f}".rstrip("0").rstrip("."),
            "currency_type": currency_type,
            "description": (description or "")[:1024] or None,
            "payload": payload,
            "expires_in": expires_in,
            "allow_comments": False,
            "allow_anonymous": True,
        }
        if currency_type == "fiat":
            params["fiat"] = fiat
            if accepted_assets:
                params["accepted_assets"] = ",".join(accepted_assets)
        else:
            params["asset"] = asset
        if paid_btn_name and paid_btn_url:
            params["paid_btn_name"] = paid_btn_name
            params["paid_btn_url"] = paid_btn_url
        return await self._call("createInvoice", params)

    async def get_invoice(self, invoice_id: int | str) -> dict[str, Any] | None:
        result = await self._call("getInvoices", {"invoice_ids": str(invoice_id)})
        items = result.get("items") if isinstance(result, dict) else result
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and str(item.get("invoice_id")) == str(invoice_id):
                    return item
        return None

    async def get_invoices(self, invoice_ids: list[int | str]) -> list[dict[str, Any]]:
        if not invoice_ids:
            return []
        result = await self._call("getInvoices", {"invoice_ids": ",".join(str(i) for i in invoice_ids)})
        items = result.get("items") if isinstance(result, dict) else result
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def check_signature(self, body: bytes, signature: str) -> bool:
        secret = hashlib.sha256(self.token.encode()).digest()
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
