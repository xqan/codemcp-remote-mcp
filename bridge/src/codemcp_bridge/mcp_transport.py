"""MCP Streamable HTTP compatibility helpers."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import anyio
from anyio.abc import TaskStatus
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.types import Receive, Scope, Send

from .resource_auth import (
    AUTH_SCOPE_KEY,
    AuthenticatedPrincipal,
    NetworkTrustedPrincipal,
    bind_auth_context,
    reset_auth_context,
)

logger = logging.getLogger(__name__)

RequestAuthenticator = Callable[[Scope, Send], Awaitable[bool]]
_GRACEFUL_CLOSE_TIMEOUT_SECONDS = 1.0


class BridgeStreamableHTTPSessionManager(StreamableHTTPSessionManager):
    """Avoid MCP 1.x cancel-scope races when closing stateless requests.

    MCP 1.x can finish routing the HTTP response before the low-level
    ``RequestResponder`` has exited its AnyIO cancel scope. Closing the
    transport input in that window makes ``Server.run()`` cancel the handler
    task while the responder is still unwinding. Track the responder directly
    and only signal EOF after its handler has returned.
    """

    _RESPONDER_STARTED_SCOPE_KEY = "codemcp_bridge.responder_started"
    _RESPONDER_FINISHED_SCOPE_KEY = "codemcp_bridge.responder_finished"

    def __init__(
        self,
        *args: object,
        startup_callback: Callable[[], Awaitable[None]] | None = None,
        shutdown_callback: Callable[[], Awaitable[None]] | None = None,
        request_authenticator: RequestAuthenticator | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._startup_callback = startup_callback
        self._shutdown_callback = shutdown_callback
        self._request_authenticator = request_authenticator
        original_handle_message = self.app._handle_message  # noqa: SLF001

        async def tracked_handle_message(
            message: object,
            session: object,
            lifespan_context: object,
            raise_exceptions: bool = False,
        ) -> None:
            metadata = getattr(message, "message_metadata", None)
            request_context = getattr(metadata, "request_context", None)
            request_scope = getattr(request_context, "scope", None)
            started = (
                request_scope.get(self._RESPONDER_STARTED_SCOPE_KEY)
                if isinstance(request_scope, dict)
                else None
            )
            finished = (
                request_scope.get(self._RESPONDER_FINISHED_SCOPE_KEY)
                if isinstance(request_scope, dict)
                else None
            )
            if started is not None:
                started.set()
            principal = (
                request_scope.get(AUTH_SCOPE_KEY) if isinstance(request_scope, dict) else None
            )
            auth_token = (
                bind_auth_context(principal)
                if isinstance(principal, (AuthenticatedPrincipal, NetworkTrustedPrincipal))
                else None
            )
            try:
                await original_handle_message(
                    message,
                    session,
                    lifespan_context,
                    raise_exceptions,
                )
            finally:
                if auth_token is not None:
                    reset_auth_context(auth_token)
                if finished is not None:
                    finished.set()

        self.app._handle_message = tracked_handle_message  # type: ignore[method-assign]  # noqa: SLF001

    def install_request_authenticator(self, authenticator: RequestAuthenticator) -> None:
        """Install the externally configured MCP request authentication gate."""

        if self._request_authenticator is not None:
            raise RuntimeError("request authenticator is already installed")
        self._request_authenticator = authenticator

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        try:
            async with super().run():
                if self._startup_callback is not None:
                    await self._startup_callback()
                yield
        finally:
            if self._shutdown_callback is not None:
                await self._shutdown_callback()

    async def _handle_stateless_request(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if self._request_authenticator is not None and not await self._request_authenticator(
            scope, send
        ):
            return
        http_transport = StreamableHTTPServerTransport(
            mcp_session_id=None,
            is_json_response_enabled=self.json_response,
            event_store=None,
            security_settings=self.security_settings,
        )
        server_finished = anyio.Event()
        responder_started = anyio.Event()
        responder_finished = anyio.Event()
        scope[self._RESPONDER_STARTED_SCOPE_KEY] = responder_started
        scope[self._RESPONDER_FINISHED_SCOPE_KEY] = responder_finished

        async def run_stateless_server(
            *, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED
        ) -> None:
            try:
                async with http_transport.connect() as streams:
                    read_stream, write_stream = streams
                    task_status.started()
                    await self.app.run(
                        read_stream,
                        write_stream,
                        self.app.create_initialization_options(),
                        stateless=True,
                    )
            except Exception:  # pragma: no cover - delegated MCP failure
                logger.exception("Stateless session crashed")
            finally:
                server_finished.set()

        assert self._task_group is not None
        await self._task_group.start(run_stateless_server)

        try:
            # Keep MCP request processing and responder cleanup shielded from an
            # outer HTTP-task cancellation. Without this, a client disconnect can
            # call terminate() while RequestResponder still owns an AnyIO cancel
            # scope in the server task, which crashes the stateless session.
            with anyio.CancelScope(shield=True):
                await http_transport.handle_request(scope, receive, send)

                # The HTTP response may be routed before RequestResponder.__exit__
                # completes. Only requests dispatched through Server._handle_message
                # need this extra wait. InitializeRequest can be completed directly
                # by ServerSession._received_request and never enters _handle_message.
                # For normal requests, producing a response requires the handler to
                # have started, so responder_started is already set before this point.
                if responder_started.is_set():
                    await responder_finished.wait()

                # Let the tracked _handle_message task return to its task group
                # before the receive loop observes EOF.
                await anyio.sleep(0)

                # Signal EOF to Server.run only after responder cleanup.
                if http_transport._read_stream_writer is not None:  # noqa: SLF001
                    await http_transport._read_stream_writer.aclose()  # noqa: SLF001

                with anyio.move_on_after(_GRACEFUL_CLOSE_TIMEOUT_SECONDS) as close_scope:
                    await server_finished.wait()
                if close_scope.cancel_called:
                    logger.warning("Timed out waiting for stateless MCP session cleanup")
                    await http_transport.terminate()

            # Re-deliver any cancellation that arrived while cleanup was shielded.
            await anyio.sleep(0)
        except BaseException:
            # Cleanup may itself run under cancellation; shield it so transport
            # resources are not abandoned halfway through teardown.
            with anyio.CancelScope(shield=True):
                await http_transport.terminate()
            raise
