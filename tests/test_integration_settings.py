from noob_agent.settings import IntegrationSettings


def test_integration_settings_read_only_explicit_environment_values() -> None:
    settings = IntegrationSettings.from_environ(
        {
            "WANDB_API_KEY": "wandb-key",
            "WANDB_ENTITY": "noob-team",
            "WANDB_PROJECT": "noob-agent-dev",
            "NOOB_AGENT_TRACE_MODE": "weave",
            "WEAVE_DISABLED": "false",
            "NOOB_AGENT_MODEL_PROVIDER": "wandb-inference",
            "NOOB_AGENT_INFERENCE_MODEL": "meta-llama/example",
            "CWSANDBOX_API_KEY": "coreweave-token",
            "NOOB_AGENT_SANDBOX_MODE": "serverless",
        }
    )

    assert settings.wandb.api_key == "wandb-key"
    assert settings.wandb.project == "noob-agent-dev"
    assert settings.trace.enabled is True
    assert settings.model.provider == "wandb-inference"
    assert settings.sandbox.enabled is True


def test_integration_settings_default_all_remote_adapters_to_disabled() -> None:
    settings = IntegrationSettings.from_environ({})

    assert settings.trace.enabled is False
    assert settings.model.provider == "disabled"
    assert settings.sandbox.enabled is False
