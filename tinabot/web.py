"""Web chat interface using aiohttp with WebSocket for real-time streaming."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import secrets
import time
from pathlib import Path

from aiohttp import web
from loguru import logger

from tinabot.agent import ImageInput, TinaAgent
from tinabot.config import WebConfig
from tinabot.history import HistoryLogger
from tinabot.memory import TaskMemory

# Max upload size: 20MB
MAX_UPLOAD_SIZE = 20 * 1024 * 1024

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


class WebServer:
    """Async web server with WebSocket chat and file upload."""

    def __init__(self, config: WebConfig, agent: TinaAgent, memory: TaskMemory):
        self.config = config
        self.agent = agent
        self.memory = memory
        self._history = HistoryLogger(memory.data_dir)
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        # session_id -> task_id mapping (like Telegram's chat_tasks)
        self._session_tasks: dict[str, str] = {}
        # file_id -> file info
        self._uploaded_files: dict[str, dict] = {}
        # ws_id -> running agent asyncio.Task for cancellation
        self._processing: dict[str, asyncio.Task] = {}

    def _check_token(self, request: web.Request) -> bool:
        """Validate auth token from header or query param."""
        if not self.config.auth_token:
            return False
        # Check Authorization header
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:] == self.config.auth_token:
            return True
        # Check query param
        if request.query.get("token") == self.config.auth_token:
            return True
        return False

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve the chat UI HTML (no auth required - auth happens via WebSocket)."""
        html_path = Path(__file__).parent / "static" / "index.html"
        html = html_path.read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

    async def _handle_download(self, request: web.Request) -> web.Response:
        """Serve a local file for download."""
        if not self._check_token(request):
            return web.Response(text="Unauthorized", status=401)

        file_path = request.query.get("path", "")
        if not file_path:
            return web.Response(text="Missing path parameter", status=400)

        # Expand ~ and resolve to absolute path
        resolved = Path(file_path).expanduser().resolve()
        if not resolved.is_file():
            return web.Response(text="File not found", status=404)

        # Determine content type
        content_type, _ = mimetypes.guess_type(str(resolved))
        if not content_type:
            content_type = "application/octet-stream"

        # Stream the file
        resp = web.StreamResponse()
        resp.content_type = content_type
        filename = resolved.name
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{filename}"'
        )
        resp.headers["Content-Length"] = str(resolved.stat().st_size)
        # Allow fetch() from same origin to read headers (needed for blob download)
        resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
        await resp.prepare(request)

        with open(resolved, "rb") as f:
            while chunk := f.read(65536):
                await resp.write(chunk)

        return resp

    async def _handle_upload(self, request: web.Request) -> web.Response:
        """Handle file upload. Returns file metadata as JSON."""
        if not self._check_token(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        reader = await request.multipart()
        field = await reader.next()
        if field is None or field.name != "file":
            return web.json_response({"error": "No file field"}, status=400)

        filename = field.filename or "upload"
        data = bytearray()
        while True:
            chunk = await field.read_chunk(8192)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_UPLOAD_SIZE:
                return web.json_response(
                    {"error": f"File too large (max {MAX_UPLOAD_SIZE // 1024 // 1024}MB)"},
                    status=413,
                )

        ext = Path(filename).suffix.lower()
        is_image = ext in _IMAGE_EXTS

        # Save to disk
        if is_image:
            save_dir = Path("~/.tinabot/data/images").expanduser()
        else:
            save_dir = Path("~/.tinabot/data/files").expanduser()
        save_dir.mkdir(parents=True, exist_ok=True)

        safe_name = f"{int(time.time())}_{filename}"
        save_path = save_dir / safe_name
        save_path.write_bytes(data)

        file_id = secrets.token_hex(8)
        file_info = {
            "id": file_id,
            "name": filename,
            "path": str(save_path),
            "type": "image" if is_image else "document",
            "size": len(data),
        }
        if is_image:
            b64 = base64.b64encode(data).decode("utf-8")
            # Determine media type
            media_types = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".gif": "image/gif",
                ".webp": "image/webp", ".bmp": "image/bmp",
            }
            file_info["b64"] = b64
            file_info["media_type"] = media_types.get(ext, "image/jpeg")

        self._uploaded_files[file_id] = file_info

        return web.json_response({
            "id": file_id,
            "name": filename,
            "type": file_info["type"],
            "size": len(data),
        })

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connection for chat."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        ws_id = secrets.token_hex(8)
        session_id = ""
        authenticated = False

        logger.info(f"WebSocket connected: {ws_id}")

        try:
            async for raw_msg in ws:
                if raw_msg.type == web.WSMsgType.TEXT:
                    try:
                        msg = json.loads(raw_msg.data)
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "message": "Invalid JSON"})
                        continue

                    msg_type = msg.get("type", "")

                    # Auth must be first
                    if msg_type == "auth":
                        token = msg.get("token", "")
                        logger.info(f"WS {ws_id} auth attempt, token_match={token == self.config.auth_token}, task_id={msg.get('task_id')}")
                        if token == self.config.auth_token and self.config.auth_token:
                            authenticated = True
                            session_id = secrets.token_hex(8)

                            # Resolve task from client-provided task_id (from URL)
                            req_task_id = msg.get("task_id", "default")
                            if req_task_id == "default":
                                task = self.memory.get_or_create("default", "Default task")
                            else:
                                task = self.memory.get_task(req_task_id)
                                if not task:
                                    # Unknown task — fall back to default
                                    task = self.memory.get_or_create("default", "Default task")

                            self._session_tasks[session_id] = task.id
                            await ws.send_json({
                                "type": "auth_ok",
                                "session_id": session_id,
                                "task_id": task.id,
                                "task_name": task.name,
                            })
                        else:
                            await ws.send_json({"type": "auth_error", "message": "Invalid token"})
                            await ws.close()
                            break
                        continue

                    if not authenticated:
                        await ws.send_json({"type": "auth_error", "message": "Not authenticated"})
                        await ws.close()
                        break

                    if msg_type == "ping":
                        await self._ws_send(ws, {"type": "pong"})
                    elif msg_type == "message":
                        await self._handle_chat_message(ws, ws_id, session_id, msg)
                    elif msg_type == "command":
                        await self._handle_command(ws, session_id, msg)

                elif raw_msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            # Cancel any in-flight processing
            proc = self._processing.pop(ws_id, None)
            if proc and not proc.done():
                proc.cancel()
            logger.info(f"WebSocket disconnected: {ws_id}")

        return ws

    async def _send_tasks(
        self, ws: web.WebSocketResponse, session_id: str,
        show_picker: bool = True,
    ):
        """Send current task list to client."""
        tasks = self.memory.list_tasks()
        active_id = self._session_tasks.get(session_id)
        # If no task assigned, pick the first active one
        if not active_id:
            active_task = self.memory.get_active_task()
            if active_task:
                active_id = active_task.id
                self._session_tasks[session_id] = active_id
        task_list = []
        for t in tasks:
            task_list.append({
                "id": t.id,
                "name": t.name,
                "active": t.id == active_id,
                "turn_count": t.turn_count,
                "compressed": bool(t.summary),
            })
        await ws.send_json({
            "type": "tasks", "tasks": task_list,
            "show_picker": show_picker,
        })

    async def _send_history(
        self, ws: web.WebSocketResponse, task_id: str,
        limit: int = 10, before_index: int | None = None,
    ):
        """Send paginated conversation history for a task."""
        events, has_more, next_offset = self._history.read_paginated(
            task_id, max_pairs=limit, before_index=before_index,
        )
        if not events:
            if before_index is not None:
                # Explicit load-more with no results
                await self._ws_send(ws, {
                    "type": "history", "items": [], "has_more": False,
                    "prepend": True,
                })
            return

        items = self._events_to_items(events)
        if items:
            await self._ws_send(ws, {
                "type": "history",
                "items": items,
                "has_more": has_more,
                "offset": next_offset,
                "prepend": before_index is not None,
            })

    @staticmethod
    def _events_to_items(events: list[dict]) -> list[dict]:
        """Convert history events to display items for the client."""
        items: list[dict] = []
        for ev in events:
            kind = ev.get("event", "")
            if kind == "user_message":
                text = ev.get("text", "")
                # Strip the WEB_FILE_INSTRUCTION prefix if present
                marker = "Do NOT use the `open` command.]\n\n"
                idx = text.find(marker)
                if idx >= 0:
                    text = text[idx + len(marker):]
                if text:
                    items.append({"role": "user", "text": text})
            elif kind == "tool_call":
                name = ev.get("name", "?")
                inp = ev.get("input", {})
                detail = _tool_detail(name, inp)
                items.append({"role": "tool", "name": name, "detail": detail})
            elif kind == "response":
                text = ev.get("text", "")
                cost = ev.get("cost_usd")
                tokens = {}
                if ev.get("input_tokens"):
                    tokens["input_tokens"] = ev["input_tokens"]
                if ev.get("output_tokens"):
                    tokens["output_tokens"] = ev["output_tokens"]
                if ev.get("cache_read_tokens"):
                    tokens["cache_read_tokens"] = ev["cache_read_tokens"]
                if text:
                    items.append({
                        "role": "bot", "text": text,
                        **({"cost_usd": cost} if cost is not None else {}),
                        **tokens,
                    })
        return items

    def _get_or_create_task(self, session_id: str, message: str = "") -> str:
        """Get or create the active task for a session."""
        task_id = self._session_tasks.get(session_id)
        if task_id:
            task = self.memory.get_task(task_id)
            if task:
                return task_id
        name = message[:80] if message else "Web chat"
        task = self.memory.create_task(name)
        self._session_tasks[session_id] = task.id
        return task.id

    async def _handle_chat_message(
        self, ws: web.WebSocketResponse, ws_id: str, session_id: str, msg: dict
    ):
        """Handle an incoming chat message."""
        text = msg.get("text", "").strip()
        if not text:
            return

        # Cancel previous processing for this ws
        proc = self._processing.pop(ws_id, None)
        if proc and not proc.done():
            proc.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(proc), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

        task_id = self._get_or_create_task(session_id, text)
        task = self.memory.get_task(task_id)

        # Resolve file attachments
        images: list[ImageInput] = []
        file_refs: list[str] = []
        for f in msg.get("files", []):
            fid = f.get("id", "")
            info = self._uploaded_files.pop(fid, None)
            if not info:
                continue
            if info["type"] == "image":
                images.append(ImageInput(
                    data=info["b64"],
                    media_type=info["media_type"],
                ))
            else:
                file_refs.append(info["path"])

        # Prepend file paths to message for document context
        if file_refs:
            prefix = "\n".join(
                f"[Attached file: {p}]" for p in file_refs
            )
            text = f"{prefix}\n\n{text}"

        # Create agent processing task
        agent_task = asyncio.create_task(
            self._process_and_stream(ws, ws_id, task, text, images)
        )
        self._processing[ws_id] = agent_task
        agent_task.add_done_callback(
            lambda _, wid=ws_id: self._processing.pop(wid, None)
        )

    async def _ws_send(self, ws: web.WebSocketResponse, msg: dict):
        """Send JSON to WebSocket, silently ignoring failures."""
        try:
            if not ws.closed:
                await ws.send_json(msg)
        except Exception:
            pass  # Connection lost — don't kill the agent

    async def _keepalive_loop(self, ws: web.WebSocketResponse):
        """Send periodic heartbeats to keep Cloudflare Tunnel alive during
        long agent operations (idle timeout is ~100s)."""
        try:
            while not ws.closed:
                await asyncio.sleep(15)
                await self._ws_send(ws, {"type": "heartbeat"})
        except asyncio.CancelledError:
            pass

    async def _process_and_stream(
        self,
        ws: web.WebSocketResponse,
        ws_id: str,
        task,
        text: str,
        images: list[ImageInput] | None,
    ):
        """Run agent.process() and stream events to WebSocket."""
        async def on_text(chunk: str):
            await self._ws_send(ws, {"type": "text", "content": chunk})

        async def on_thinking(chunk: str):
            await self._ws_send(ws, {"type": "thinking", "content": chunk})

        async def on_tool(name: str, input_data: dict):
            detail = _tool_detail(name, input_data)
            await self._ws_send(ws, {"type": "tool", "name": name, "detail": detail})

        # Start server-side keepalive to prevent Cloudflare idle timeout
        keepalive = asyncio.create_task(self._keepalive_loop(ws))

        try:
            response = await self.agent.process(
                message=text,
                task=task,
                on_text=on_text,
                on_thinking=on_thinking,
                on_tool=on_tool,
                images=images if images else None,
                web_mode=True,
            )

            done_msg: dict = {"type": "done", "text": response.text or ""}
            if response.input_tokens or response.output_tokens:
                done_msg["input_tokens"] = response.input_tokens
                done_msg["output_tokens"] = response.output_tokens
                done_msg["cache_read_tokens"] = response.cache_read_tokens
            if response.cost_usd is not None:
                done_msg["cost_usd"] = response.cost_usd
            if response.num_turns:
                done_msg["num_turns"] = response.num_turns
            await self._ws_send(ws, done_msg)

        except asyncio.CancelledError:
            await self._ws_send(ws, {"type": "error", "message": "Interrupted"})
            raise
        except Exception as e:
            logger.error(f"Agent error for ws {ws_id}: {e}")
            await self._ws_send(ws, {"type": "error", "message": str(e)})
        finally:
            keepalive.cancel()

    async def _handle_command(
        self, ws: web.WebSocketResponse, session_id: str, msg: dict
    ):
        """Handle task management commands."""
        cmd = msg.get("cmd", "")
        args = msg.get("args", "")

        if cmd == "new":
            name = args or "New task"
            task = self.memory.create_task(name)
            self._session_tasks[session_id] = task.id
            await ws.send_json({
                "type": "task_changed",
                "task_id": task.id,
                "task_name": task.name,
            })

        elif cmd == "tasks":
            await self._send_tasks(ws, session_id, show_picker=True)

        elif cmd == "resume":
            task = self.memory.get_task(args)
            if task:
                self._session_tasks[session_id] = task.id
                await ws.send_json({
                    "type": "task_changed",
                    "task_id": task.id,
                    "task_name": task.name,
                })
                await self._send_history(ws, task.id)
            else:
                await ws.send_json({"type": "error", "message": f"Task '{args}' not found"})

        elif cmd == "history":
            # Load more history (scroll up)
            task_id = self._session_tasks.get(session_id)
            if task_id:
                try:
                    params = json.loads(args) if isinstance(args, str) and args else {}
                except (json.JSONDecodeError, TypeError):
                    params = {}
                before = params.get("before")
                limit = min(params.get("limit", 10), 50)
                await self._send_history(ws, task_id, limit=limit, before_index=before)

        elif cmd == "rename":
            task_id = self._session_tasks.get(session_id)
            if task_id and args:
                self.memory.rename_task(task_id, args)
                await ws.send_json({
                    "type": "task_changed",
                    "task_id": task_id,
                    "task_name": args[:80],
                    "clear": False,
                })
            else:
                await ws.send_json({"type": "error", "message": "No active task or missing name"})

        elif cmd == "delete":
            task_id = args or self._session_tasks.get(session_id)
            if task_id:
                task = self.memory.get_task(task_id)
                if task:
                    was_current = self._session_tasks.get(session_id) == task_id
                    self.memory.delete_task(task_id)
                    if was_current:
                        del self._session_tasks[session_id]
                    await ws.send_json({
                        "type": "info",
                        "message": f"Deleted [{task_id}] {task.name}",
                    })
                    if was_current:
                        # Navigate to default task
                        default = self.memory.get_or_create("default", "Default task")
                        self._session_tasks[session_id] = default.id
                        await ws.send_json({
                            "type": "task_changed",
                            "task_id": default.id,
                            "task_name": default.name,
                        })
                        await self._send_history(ws, default.id)
                    else:
                        await self._send_tasks(ws, session_id, show_picker=False)
                else:
                    await ws.send_json({"type": "error", "message": f"Task '{task_id}' not found"})

        elif cmd == "models":
            from tinabot.config import Config
            data = Config.load_raw()
            active = data.get("active_profile", "")
            profiles = data.get("profiles", {})
            model_list = []
            for pname, praw in profiles.items():
                model_list.append({
                    "name": pname,
                    "model": praw.get("model", "?"),
                    "provider": praw.get("provider", "?"),
                    "active": pname == active,
                })
            await ws.send_json({"type": "models", "models": model_list})

        elif cmd == "model":
            if not args:
                agent = self.agent.config
                from tinabot.config import Config
                active = Config.load_raw().get("active_profile", "")
                await ws.send_json({
                    "type": "info",
                    "message": f"Profile: {active}, {agent.model} ({agent.provider})",
                })
            else:
                from tinabot.config import Config, ProfileConfig
                data = Config.load_raw()
                profiles = data.get("profiles", {})
                if args not in profiles:
                    await ws.send_json({"type": "error", "message": f"Profile '{args}' not found"})
                else:
                    profile = ProfileConfig(**profiles[args])
                    data["active_profile"] = args
                    Config.save_raw(data)
                    self.agent.config.apply_profile(profile)
                    self.agent.reinit(self.agent.config)
                    await ws.send_json({
                        "type": "info",
                        "message": f"Switched to '{args}': {profile.model} ({profile.provider})",
                    })

        elif cmd == "compress":
            task_id = self._session_tasks.get(session_id)
            if not task_id:
                await ws.send_json({"type": "error", "message": "No active task"})
                return
            task = self.memory.get_task(task_id)
            if not task:
                await ws.send_json({"type": "error", "message": "No active task"})
                return
            await ws.send_json({"type": "info", "message": "Compressing..."})
            summary = await self.agent.force_compress(task)
            if summary:
                await ws.send_json({"type": "info", "message": f"Compressed. Summary:\n{summary}"})
            else:
                await ws.send_json({"type": "error", "message": "Compression failed"})

    async def start(self):
        """Start the web server."""
        self._app = web.Application(client_max_size=MAX_UPLOAD_SIZE + 1024)
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/t/{task_id}", self._handle_index)
        self._app.router.add_get("/ws", self._handle_ws)
        self._app.router.add_get("/download", self._handle_download)
        self._app.router.add_post("/upload", self._handle_upload)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await site.start()
        logger.info(f"Web server started at http://{self.config.host}:{self.config.port}")

        # Block until cancelled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Stop the web server."""
        # Cancel all in-flight processing
        for proc in self._processing.values():
            if not proc.done():
                proc.cancel()
        self._processing.clear()

        if self._runner:
            await self._runner.cleanup()
            self._runner = None


def _tool_detail(name: str, input_data: dict) -> str:
    """Build a short description of a tool call for display."""
    if name == "Bash":
        cmd = input_data.get("command", "")
        return cmd.split("\n")[0][:80]
    elif name in ("Read", "Write", "Edit"):
        path = input_data.get("file_path", "")
        short = path.rsplit("/", 1)[-1] if "/" in path else path
        return f"{name} {short}"
    elif name in ("Glob", "Grep"):
        return f"{name} {input_data.get('pattern', '')[:60]}"
    elif name == "WebSearch":
        return f"Search {input_data.get('query', '')[:60]}"
    elif name == "WebFetch":
        return f"Fetch {input_data.get('url', '')[:60]}"
    elif name == "Task":
        return input_data.get("description", "")[:60]
    return name
