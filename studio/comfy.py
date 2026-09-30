"""Async ComfyUI client: queue prompts, follow execution over the websocket,
free memory, interrupt."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Awaitable, Callable

import aiohttp

log = logging.getLogger("comfy")

EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.client_id = "mmh3-studio-" + uuid.uuid4().hex[:8]
        self.session: aiohttp.ClientSession | None = None
        self.connected = False
        self._handlers: list[EventHandler] = []
        self._ws_task: asyncio.Task | None = None
        self._object_info: dict[str, Any] | None = None

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def close(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
        if self.session:
            await self.session.close()

    def on_event(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    # --- HTTP -----------------------------------------------------------------

    async def _req(self, method: str, path: str, **kw: Any) -> Any:
        assert self.session is not None
        try:
            async with self.session.request(method, self.base + path, **kw) as r:
                text = await r.text()
                body = json.loads(text) if text and text.strip()[:1] in "{[" else text
                if r.status >= 400:
                    raise ComfyError(_describe_error(body) if isinstance(body, dict) else f"HTTP {r.status}: {text[:300]}")
                return body
        except aiohttp.ClientError as exc:
            raise ComfyError(f"ComfyUI unreachable: {exc}") from exc

    async def alive(self) -> bool:
        try:
            await self._req("GET", "/system_stats", timeout=aiohttp.ClientTimeout(total=3))
            return True
        except ComfyError:
            return False

    async def system_stats(self) -> dict[str, Any]:
        return await self._req("GET", "/system_stats")

    async def queue_prompt(self, prompt: dict[str, Any]) -> str:
        body = await self._req("POST", "/prompt", json={"prompt": prompt, "client_id": self.client_id})
        if body.get("node_errors"):
            raise ComfyError(_describe_error(body))
        return body["prompt_id"]

    async def queue_state(self) -> dict[str, Any]:
        return await self._req("GET", "/queue")

    async def history(self, prompt_id: str) -> dict[str, Any] | None:
        body = await self._req("GET", f"/history/{prompt_id}")
        return body.get(prompt_id) if isinstance(body, dict) else None

    async def interrupt(self, prompt_id: str | None = None) -> None:
        payload = {"prompt_id": prompt_id} if prompt_id else {}
        await self._req("POST", "/interrupt", json=payload)

    async def delete_queued(self, prompt_id: str) -> None:
        await self._req("POST", "/queue", json={"delete": [prompt_id]})

    async def free(self, unload_models: bool, free_memory: bool = True) -> None:
        await self._req("POST", "/free", json={"unload_models": unload_models, "free_memory": free_memory})

    async def object_info(self, refresh: bool = False) -> dict[str, Any]:
        if self._object_info is None or refresh:
            self._object_info = await self._req("GET", "/object_info", timeout=aiohttp.ClientTimeout(total=120))
        return self._object_info

    async def choices(self, node: str, field: str) -> list[str]:
        """Enum values a node accepts, e.g. the sampler list or LoRA files."""
        info = await self.object_info()
        spec = ((info.get(node) or {}).get("input") or {})
        for group in ("required", "optional"):
            entry = (spec.get(group) or {}).get(field)
            if entry:
                values = entry[0]
                if isinstance(values, list):
                    return [str(v) for v in values]
                if isinstance(entry[1], dict) and isinstance(entry[1].get("options"), list):
                    return [str(v) for v in entry[1]["options"]]
        return []

    def forget_object_info(self) -> None:
        self._object_info = None

    # --- websocket ------------------------------------------------------------

    async def _ws_loop(self) -> None:
        url = self.base.replace("http", "ws", 1) + f"/ws?clientId={self.client_id}"
        while True:
            try:
                assert self.session is not None
                async with self.session.ws_connect(url, heartbeat=20, max_msg_size=64 * 1024 * 1024) as ws:
                    self.connected = True
                    log.info("connected to ComfyUI websocket")
                    await self._dispatch("studio_connected", {})
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except ValueError:
                                continue
                            await self._dispatch(str(data.get("type")), data.get("data") or {})
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # ComfyUI not up yet, or restarting
                log.debug("websocket: %s", exc)
            if self.connected:
                log.info("ComfyUI websocket disconnected")
                await self._dispatch("studio_disconnected", {})
            self.connected = False
            await asyncio.sleep(2)

    async def _dispatch(self, kind: str, data: dict[str, Any]) -> None:
        for handler in self._handlers:
            try:
                await handler(kind, data)
            except Exception:
                log.exception("event handler failed for %s", kind)


def _describe_error(body: dict[str, Any]) -> str:
    """Turn ComfyUI's validation response into one readable line."""
    err = body.get("error")
    parts = []
    if isinstance(err, dict):
        parts.append(err.get("message") or err.get("type") or "")
    elif err:
        parts.append(str(err))
    for nid, ne in (body.get("node_errors") or {}).items():
        title = ((ne.get("class_type") or "") + f" #{nid}").strip()
        for e in ne.get("errors") or []:
            detail = e.get("details") or e.get("message") or ""
            parts.append(f"{title}: {detail}")
    return "; ".join(p for p in parts if p)[:800] or "ComfyUI rejected the job"
