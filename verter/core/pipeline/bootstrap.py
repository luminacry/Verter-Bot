from importlib import import_module

_BUILTIN_STAGE_MODULES = (
    "verter.core.pipeline.waking_check.stage",
    "verter.core.pipeline.whitelist_check.stage",
    "verter.core.pipeline.session_status_check.stage",
    "verter.core.pipeline.rate_limit_check.stage",
    "verter.core.pipeline.content_safety_check.stage",
    "verter.core.pipeline.preprocess_stage.stage",
    "verter.core.pipeline.process_stage.stage",
    "verter.core.pipeline.result_decorate.stage",
    "verter.core.pipeline.respond.stage",
)


def ensure_builtin_stages_registered() -> None:
    for module in _BUILTIN_STAGE_MODULES:
        import_module(module)
