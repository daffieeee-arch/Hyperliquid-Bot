"""Closed collector identities; selecting a profile never grants runtime admission."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CollectorInstance:
    name: str
    app: str
    port: int
    campaign_prefix: str
    template: str
    unit: str
    comment: str

    @property
    def project(self) -> str:
        return "ix-" + self.app

    def container(self, role: str) -> str:
        if role not in {"collector", "status"}:
            raise ValueError("Unknown fixed collector role.")
        return self.project + "-" + role + "-1"


STANDARD = CollectorInstance(
    "standard",
    "hyperliquid-collector",
    18790,
    "hl-cont-",
    "truenas-collector.compose.yml",
    "hyperliquid-continuous-supervisor.service",
    "Hyperliquid public collector continuous supervisor (project-owned)",
)
FAST = CollectorInstance(
    "hyperliquid-fast",
    "hyperliquid-fast-collector",
    18791,
    "hl-fast-",
    "truenas-fast-collector.compose.yml",
    "hyperliquid-fast-continuous-supervisor.service",
    "Hyperliquid FAST-5 collector continuous supervisor (project-owned)",
)


def instance(name: str = "standard") -> CollectorInstance:
    if type(name) is not str or name not in {STANDARD.name, FAST.name}:
        raise ValueError("Unknown fixed collector instance.")
    return FAST if name == FAST.name else STANDARD
