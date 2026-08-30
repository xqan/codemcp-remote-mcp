"""Remote transport providers."""

from .base import LifecycleError, RemoteTransportProvider, TransportContext
from .cloudflare import CLOUDFLARE_TUNNEL_PROVIDER, CloudflareTunnelSettings
from .openai_tunnel import OPENAI_TUNNEL_PROVIDER, OpenAITunnelSettings

TRANSPORT_PROVIDERS: dict[str, RemoteTransportProvider] = {
    OPENAI_TUNNEL_PROVIDER.provider_id: OPENAI_TUNNEL_PROVIDER,
    CLOUDFLARE_TUNNEL_PROVIDER.provider_id: CLOUDFLARE_TUNNEL_PROVIDER,
}


def get_transport_provider(provider_id: str) -> RemoteTransportProvider:
    """Return a known provider or fail closed."""

    try:
        return TRANSPORT_PROVIDERS[provider_id]
    except KeyError as exc:
        raise LifecycleError(f"unsupported remote transport: {provider_id}") from exc


__all__ = [
    "CLOUDFLARE_TUNNEL_PROVIDER",
    "CloudflareTunnelSettings",
    "LifecycleError",
    "OPENAI_TUNNEL_PROVIDER",
    "OpenAITunnelSettings",
    "RemoteTransportProvider",
    "TRANSPORT_PROVIDERS",
    "TransportContext",
    "get_transport_provider",
]
