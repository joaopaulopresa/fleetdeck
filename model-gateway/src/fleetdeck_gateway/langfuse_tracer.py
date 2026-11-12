"""Sends one Langfuse generation per model call, grouped into the trace of the agent's run."""

import logging

from langfuse import Langfuse, propagate_attributes

from fleetdeck_gateway.settings import GatewaySettings
from fleetdeck_gateway.tracing import CallRecord

log = logging.getLogger(__name__)


class LangfuseTracer:
    """A :class:`~fleetdeck_gateway.tracing.Tracer` backed by a self-hosted Langfuse."""

    def __init__(self, settings: GatewaySettings) -> None:
        self._client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )

    def record(self, call: CallRecord) -> None:
        """Emit the generation. The SDK batches and sends in the background; errors are logged."""
        try:
            self._emit(call)
        except Exception:
            log.exception("langfuse: could not record a model call")

    def _emit(self, call: CallRecord) -> None:
        identity, context, usage = call.identity, call.context, call.usage
        output: object = usage.output_text
        if usage.tool_calls:
            output = {"content": usage.output_text, "tool_calls": usage.tool_calls}
        metadata = {
            "agent_id": identity.agent_id,
            "role": identity.role,
            "department": identity.department or "",
            "run_id": context.run_id or "",
            "case_id": context.case_id or "",
            "data_class": context.data_class,
            "route": call.route.name,
            "trigger": context.trigger or "",
            "latency_ms": str(call.latency_ms),
            "http_status": str(call.status),
        }
        with propagate_attributes(
            user_id=identity.person_id or identity.agent_id,
            session_id=context.session_id,
            trace_name=f"{identity.agent_id} {context.trigger or 'run'}",
            tags=[f"route:{call.route.name}", f"data:{context.data_class}"],
            metadata=metadata,
        ):
            generation = self._client.start_observation(
                trace_context={"trace_id": context.trace_id} if context.trace_id else None,
                name="model-call",
                as_type="generation",
                input=call.request.get("messages"),
                output=output,
                model=call.route.model,
                usage_details={"input": usage.input_tokens, "output": usage.output_tokens},
                cost_details={"total": usage.cost_usd},
                level="ERROR" if call.status >= 400 else "DEFAULT",
            )
            generation.end()

    def shutdown(self) -> None:
        """Send what is still queued."""
        self._client.shutdown()
