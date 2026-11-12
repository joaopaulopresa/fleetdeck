"""Entry point: ``fleetdeck-connector <system>`` serves one connector over streamable HTTP."""

import argparse

from fleetdeck_common.control_client import ControlPlaneClient
from fleetdeck_common.logs import configure_logging

from fleetdeck_connectors import billing, contracts, docket, erp, knowledge, patient_record
from fleetdeck_connectors.base import Deps, SystemsClient, Tool, build_server
from fleetdeck_connectors.settings import ConnectorSettings, get_settings

SYSTEMS = {
    "patient-record": patient_record,
    "billing": billing,
    "erp": erp,
    "docket": docket,
    "contracts": contracts,
    "knowledge": knowledge,
}


def tools_for(system: str, deps: Deps, settings: ConnectorSettings) -> list[Tool]:
    """Return the tools of one connector."""
    if system == "knowledge":
        return knowledge.tools(deps, knowledge.ScopedDatabase(settings.scoped_database_url))
    module = SYSTEMS[system]
    return module.tools(deps)  # type: ignore[no-any-return]


def main() -> None:
    """Run one connector."""
    parser = argparse.ArgumentParser(prog="fleetdeck-connector")
    parser.add_argument("system", choices=sorted(SYSTEMS))
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    control = ControlPlaneClient(settings)
    deps = Deps(
        system=args.system,
        control=control,
        systems=SystemsClient(settings.systems_url, settings.internal_token),
    )
    server = build_server(
        args.system,
        SYSTEMS[args.system].INSTRUCTIONS,
        tools_for(args.system, deps, settings),
        control=control,
        public_url=settings.public_url,
    )
    server.run(
        transport="streamable-http",
        host="0.0.0.0",  # noqa: S104
        port=settings.port,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
