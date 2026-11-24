import os


def resolve_model_environment(*, model=None):
    return dict(os.environ)


def model_network_options(environment, *, provider, model=None):
    if "AGENT_HTTPS_PROXY" not in environment:
        return {}
    return {"proxy": environment["AGENT_HTTPS_PROXY"].strip() or None, "trust_env": False}
