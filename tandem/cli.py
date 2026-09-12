"""Console entrypoint for the Tandem financial automation system.

Launches the operator console and all banking simulators as separate processes,
each bound to the configured host (loopback by default; see `tandem.config.Settings`).
This lives in the `tandem` package (rather than only under `scripts/`) so it ships
inside the built wheel and is runnable via the installed `tandem` console script even
without the source checkout -- `scripts/start_services.py` is now a thin dev-convenience
shim that delegates here.
"""

import subprocess
import sys
import time
from dataclasses import dataclass
from typing import List, Tuple

from tandem.config import Settings, settings


def _service_specs(settings_obj: Settings) -> List[Tuple[str, str, int]]:
    """(display name, ASGI app import path, port) for each service, read from
    `settings_obj` at call time -- never baked in at import time -- so a test (or any
    other caller) can launch an isolated set of ports via a distinct `Settings`
    instance instead of the process-wide singleton."""
    return [
        ("Core Bank Simulator (Alpha)", "simulators.core_bank.app:app", settings_obj.core_bank_port),
        ("Core Bank Simulator (Beta)", "simulators.core_bank.beta_app:app", settings_obj.core_bank_2_port),
        ("Card Processor Simulator", "simulators.processor.app:app", settings_obj.processor_port),
        ("Notice Simulator", "simulators.documents.app:app", settings_obj.documents_port),
        ("Tandem Operator Console", "tandem.api.app:app", settings_obj.tandem_port),
    ]


@dataclass(frozen=True)
class ServiceCommand:
    """A launchable service: its display name, port, and uvicorn subprocess argv."""

    name: str
    port: int
    cmd: List[str]


def build_service_commands(
    bind_host: str, settings_obj: Settings = settings
) -> List[ServiceCommand]:
    """Build the uvicorn subprocess argv for each service, bound to `bind_host`.

    Root cause (H-10): this previously hardcoded `--host 0.0.0.0` unconditionally, so
    every admin/mutation route was reachable from any host on the network regardless of
    the configured `TANDEM_HOST`. The bind host is now driven by settings, which
    defaults to loopback-only (127.0.0.1) for local/bare-metal runs; a containerized
    deployment (see docker-compose.yml) opts into 0.0.0.0 explicitly via `TANDEM_HOST`
    because it needs to be reachable through the container's published ports.
    """
    commands: List[ServiceCommand] = []
    for name, app_path, port in _service_specs(settings_obj):
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            app_path,
            "--host",
            bind_host,
            "--port",
            str(port),
            "--log-level",
            "info",
        ]
        commands.append(ServiceCommand(name=name, port=port, cmd=cmd))
    return commands


def main() -> int:
    processes: List[tuple] = []
    bind_host = settings.tandem_host
    print("=================================================================")
    print("           TANDEM FINANCIAL AUTOMATION SYSTEM                   ")
    print("=================================================================")

    try:
        for service in build_service_commands(bind_host):
            print(f"[*] Launching {service.name} on http://{bind_host}:{service.port}...")
            p = subprocess.Popen(service.cmd)
            processes.append((service.name, p))

        print("\n[+] All services active!")
        print(f"  - Operator Console:      http://{bind_host}:{settings.tandem_port}")
        print(f"  - Core Bank Alpha:       http://{bind_host}:{settings.core_bank_port}")
        print(f"  - Core Bank Beta:        http://{bind_host}:{settings.core_bank_2_port}")
        print(f"  - Card Processor:        http://{bind_host}:{settings.processor_port}")
        print(f"  - Notice Simulator:      http://{bind_host}:{settings.documents_port}")
        print("\nPress Ctrl+C to terminate all services.\n")

        while True:
            time.sleep(1)
            for name, p in processes:
                if p.poll() is not None:
                    print(f"[!] Warning: Service '{name}' exited prematurely with code {p.returncode}")
                    return p.returncode

    except KeyboardInterrupt:
        print("\n[*] Shutting down all services...")
        return 0
    finally:
        for name, p in processes:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
        print("[+] All services halted cleanly.")


if __name__ == "__main__":
    sys.exit(main() or 0)
