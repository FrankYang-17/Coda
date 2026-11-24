from coda_davinci.providers.agent_http import AgentHTTPClient
from coda_davinci.providers.configuration import model_network_options, resolve_model_environment


def model_client(client_class, settings, api_key, *, max_retries=0):
    env = resolve_model_environment()
    if env.get("AGENT_PROTOCOL") == "gemini":
        from coda_davinci.providers.gemini_client import GeminiClient

        return GeminiClient(api_key, env["BASE_URL"], settings.api_timeout)
    transport = AgentHTTPClient(
        timeout=settings.api_timeout, **model_network_options(env, provider=settings.provider)
    )
    try:
        return client_class(
            api_key=api_key,
            base_url=env.get("BASE_URL") or None,
            timeout=settings.api_timeout,
            max_retries=max_retries,
            http_client=transport,
        )
    except BaseException:
        transport.close()
        raise


def session_runner(settings):
    if settings.provider == "anthropic":
        from anthropic import Anthropic
        from coda_davinci.agents.anthropic_agent import run_anthropic_agent

        return (Anthropic, run_anthropic_agent)
    from openai import OpenAI

    if settings.openai_api == "chat":
        from coda_davinci.agents.chat_agent import run_chat_agent

        return (OpenAI, run_chat_agent)
    from coda_davinci.agents.agent import run_agent

    return (OpenAI, run_agent)
