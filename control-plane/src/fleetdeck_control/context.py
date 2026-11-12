"""The objects every request handler needs, built once at start-up."""

from dataclasses import dataclass

from fastapi import Request

from fleetdeck_control.agents.activator import Activator
from fleetdeck_control.agents.reaper import IdleReaper
from fleetdeck_control.agents.service import AgentService
from fleetdeck_control.cluster import AgentCluster, FakeAgentCluster, KubernetesAgentCluster
from fleetdeck_control.db import Database
from fleetdeck_control.hermes import HermesClient, HttpHermesClient
from fleetdeck_control.runs import RunService
from fleetdeck_control.settings import Settings


@dataclass
class AppContext:
    """Services shared by all requests of one control-plane process."""

    settings: Settings
    db: Database
    cluster: AgentCluster
    hermes: HermesClient
    agents: AgentService
    activator: Activator
    reaper: IdleReaper
    runs: RunService


def build_context(
    settings: Settings,
    *,
    cluster: AgentCluster | None = None,
    hermes: HermesClient | None = None,
) -> AppContext:
    """Wire the services together. Tests pass fakes for ``cluster`` and ``hermes``."""
    db = Database(settings.database_url)
    if cluster is None:
        if settings.cluster_mode == "fake":
            cluster = FakeAgentCluster()
        else:
            cluster = KubernetesAgentCluster(
                settings.keys_namespace, in_cluster=settings.cluster_mode == "incluster"
            )
    if hermes is None:
        hermes = HttpHermesClient(settings, run_timeout=settings.run_timeout_seconds)
    agents = AgentService(db, cluster)
    activator = Activator(db, cluster, hermes, agents, settings)
    reaper = IdleReaper(db, activator, agents, settings)
    runs = RunService(db, activator, hermes, cluster, settings)
    return AppContext(
        settings=settings,
        db=db,
        cluster=cluster,
        hermes=hermes,
        agents=agents,
        activator=activator,
        reaper=reaper,
        runs=runs,
    )


def get_context(request: Request) -> AppContext:
    """FastAPI dependency returning the process-wide :class:`AppContext`."""
    context: AppContext = request.app.state.context
    return context
